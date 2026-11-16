"""Admin va zal egasi: CRM tugmalari (st:<lead_id>:<kod>) va boshqaruv buyruqlari.

MUHIM: bu router dispatcher'ga `handlers.router` dan OLDIN ulanishi kerak. handlers.py oxirida
hamma callback va xabarni ushlaydigan fallback bor, aks holda `st:` tugmalari "eskirgan tugma"
deb javob oladi (main.py shu tartibni saqlaydi).

Buyruqlar:
  /admin      - buyruqlar ro'yxati
  /stats      - statistika (faqat super admin)
  /reload     - zallar jadvalini qayta o'qish (faqat super admin)
  /gyms       - zallar ro'yxati (admin: hammasi, egasi: o'zinikilari)
  /myleads    - (eski nomi: /leads) ochiq arizalar (egasi) yoki so'nggi arizalar (admin)
  /unanswered - bot javob bera olmagan savollar
"""
import logging
import re
import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import common
import config
import db
import sheets
from common import CODE_STATUS, answer_cb, crm_kb, lead_card_uz, safe_send
from texts import DISTRICTS, STATUS_LABELS
from utils import TZ, esc, now_utc

log = logging.getLogger("admin")

router = Router()
router.message.filter(F.chat.type == "private")

# Yakuniy holatlarda tashrif eslatmasi va "keldingizmi?" so'rovi kerak emas
FINAL_STATUSES = {"visited", "won", "lost", "no_show"}

NO_ACCESS_ADMIN = "Bu buyruq faqat adminlar uchun 🙂"
NO_ACCESS_STAFF = "Bu buyruq faqat zal egalari va adminlar uchun 🙂"

MAX_PROBLEMS_SHOWN = 20
MAX_CARDS = 10

COUNTER_LABELS = [
    ("gym_views", "👀 Zal ko'rishlar"),
    ("asks_total", "❓ Savollar"),
    ("unanswered_total", "🤷 Javobsiz savollar"),
    ("leads_total", "📝 Arizalar"),
    ("leads_join", "   ↳ yozilish"),
    ("leads_trial", "   ↳ bepul sinov"),
    ("followups_sent", "🔔 Follow-up xabarlar"),
    ("sla_reminders", "⏰ Egasiga eslatmalar"),
]
STATUS_ORDER = ("new", "contacted", "visited", "won", "lost", "no_show")


# ------------------------------------------------------------------ sof yordamchilar (testlanadi)

def status_fields(status: str, now=None) -> dict:
    """Holat o'zgarganda leadga yoziladigan maydonlar.

    sla_at doim tozalanadi (egasi javob berdi). Yakuniy holatlarda remind_at va confirm_at ham
    tozalanadi: endi mijozga eslatma yoki \"keldingizmi?\" yuborilmaydi."""
    fields = {"status": status, "status_at": now or now_utc(), "sla_at": None}
    if status in FINAL_STATUSES:
        fields["remind_at"] = None
        fields["confirm_at"] = None
    return fields


def can_manage(uid: int, lead: dict, gym: dict | None) -> bool:
    """Leadni faqat super admin yoki zal egasi o'zgartira oladi."""
    if uid in common.admin_ids():
        return True
    if lead.get("owner_id") and uid == lead["owner_id"]:
        return True
    return bool(gym and gym.get("owner_tg_id") and gym["owner_tg_id"] == uid)


def fmt_n(value) -> str:
    if value is None or value < 0:
        return "?"
    return f"{int(value):,}".replace(",", " ")


def chunk_lines(lines: list[str], limit: int = 3800) -> list[str]:
    """Telegram xabar uzunligi chegarasiga (4096) sig'adigan bo'laklarga ajratadi."""
    out: list[str] = []
    cur = ""
    for line in lines:
        line = line[:limit]
        if cur and len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


def stats_text(s: dict, today: dict, gym_names: dict[str, str] | None = None) -> str:
    """/stats xabari. s = db.stats(), today = db.day_counters()."""
    gym_names = gym_names or {}
    counters = s.get("counters") or {}
    lines = [
        "📊 <b>Statistika</b>",
        "",
        f"👥 Foydalanuvchilar: <b>{fmt_n(s.get('users'))}</b>",
        f"📝 Arizalar: jami <b>{fmt_n(s.get('leads_total'))}</b> · bugun <b>{fmt_n(s.get('leads_today'))}</b>"
        f" · 7 kun <b>{fmt_n(s.get('leads_week'))}</b>",
        "",
        "<b>Holatlar bo'yicha:</b>",
    ]
    for status in STATUS_ORDER:
        lines.append(f"{STATUS_LABELS[status]}: {fmt_n(s.get('st_' + status))}")
    pending = s.get("sheet_pending")
    if pending:
        lines += ["", f"⚠️ Sheets'ga yozilmagan arizalar: <b>{fmt_n(pending)}</b> (bot qayta yuboradi)"]

    def block(title: str, data: dict) -> list[str]:
        rows = [f"{label}: {fmt_n(data.get(key, 0))}" for key, label in COUNTER_LABELS]
        return ["", f"<b>{title}</b>", *rows]

    lines += block("Bugun", today or {})
    lines += block("Jami", counters)

    by_gym = counters.get("leads_total_by_gym") or {}
    if by_gym:
        top = sorted(by_gym.items(), key=lambda kv: -int(kv[1] or 0))[:5]
        lines += ["", "<b>🏆 Eng ko'p ariza olgan zallar:</b>"]
        for i, (key, n) in enumerate(top, 1):
            lines.append(f"{i}. {esc(gym_names.get(key, key))} — {fmt_n(n)}")
    return "\n".join(lines)


def gym_line(g: dict) -> str:
    flags = ""
    if g.get("free_trial"):
        flags += " 🎁"
    if not g.get("owner_tg_id"):
        flags += " ⚠️egasi yo'q"
    price = f" · {g['price_month'] // 1000} ming/oy" if g.get("price_month") else ""
    return f"• {esc(g['name'])} — <code>{esc(g['id'])}</code>{price}{flags}"


def gyms_overview(gyms: list[dict], problems: list[str]) -> list[str]:
    """Admin uchun: tumanlar bo'yicha guruhlangan ro'yxat."""
    lines = [f"🏋️ <b>Faol zallar: {len(gyms)}</b>"]
    known = set()
    for key, names in DISTRICTS:
        items = sorted((g for g in gyms if g["district_key"] == key), key=lambda g: g["name"].lower())
        known.add(key)
        if items:
            lines += ["", f"<b>{esc(names['uz'])}</b> ({len(items)})"]
            lines += [gym_line(g) for g in items]
    other = sorted((g for g in gyms if g["district_key"] not in known), key=lambda g: g["name"].lower())
    if other:
        lines += ["", f"<b>❓ Tumani noma'lum</b> ({len(other)})"]
        lines += [gym_line(g) for g in other]
    if problems:
        lines += ["", f"⚠️ Jadvalda {len(problems)} ta muammo bor — /reload"]
    return lines


# ------------------------------------------------------------------ ruxsatlar

async def owned_gyms(uid: int) -> list[dict]:
    return [g for g in await sheets.get_gyms() if g.get("owner_tg_id") == uid]


async def staff_role(uid: int) -> str | None:
    if uid in common.admin_ids():
        return "admin"
    return "owner" if await owned_gyms(uid) else None


async def reply_lines(message: Message, lines: list[str]) -> None:
    for part in chunk_lines(lines):
        await message.answer(part)


# ------------------------------------------------------------------ CRM tugmalari

_STATUS_LINE = re.compile(r"Holat: <b>.*?</b>")


async def refresh_card(cb: CallbackQuery, lead: dict) -> None:
    """Xabardagi 'Holat' qatori va tugmalarni yangilaydi (sarlavha va qolgan matn saqlanadi)."""
    msg = cb.message
    if msg is None or not hasattr(msg, "edit_text"):
        return
    new_line = f"Holat: <b>{STATUS_LABELS[lead['status']]}</b>"
    old = getattr(msg, "html_text", None) or ""
    if _STATUS_LINE.search(old):
        text = _STATUS_LINE.sub(lambda _m: new_line, old, count=1)
    else:
        text = lead_card_uz(lead)
    try:
        await msg.edit_text(text, reply_markup=crm_kb(lead))
    except Exception:
        log.debug("CRM xabarini tahrirlab bo'lmadi", exc_info=True)


@router.callback_query(F.data.startswith("st:"))
async def cb_status(cb: CallbackQuery):
    parts = (cb.data.split(":") + ["", ""])[:3]
    _, lead_id, code = parts
    status = CODE_STATUS.get(code)
    lead = await db.get_lead(lead_id) if (status and lead_id) else None
    if not lead:
        await answer_cb(cb, "Ariza topilmadi yoki tugma eskirgan.", alert=True)
        return
    uid = cb.from_user.id
    gym = await sheets.get_gym(lead.get("gym_id", ""))
    if not can_manage(uid, lead, gym):
        await answer_cb(cb, "Bu arizani o'zgartirishga ruxsatingiz yo'q.", alert=True)
        return
    if lead.get("status", "new") == status:
        await answer_cb(cb, "Holat allaqachon shunday.")
        return
    fields = status_fields(status)
    if not await db.update_lead(lead_id, **fields):
        await answer_cb(cb, "Saqlab bo'lmadi, birozdan keyin qayta urinib ko'ring.", alert=True)
        return
    lead.update(fields)
    await answer_cb(cb, STATUS_LABELS[status])
    await refresh_card(cb, lead)


# ------------------------------------------------------------------ buyruqlar

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    role = await staff_role(message.from_user.id)
    if role is None:
        await message.answer(NO_ACCESS_STAFF)
        return
    lines = [
        "🛠 <b>Boshqaruv buyruqlari</b>",
        "/myleads — ochiq arizalar",
        "/gyms — zallar ro'yxati",
        "/unanswered — bot javob bera olmagan savollar",
    ]
    if role == "admin":
        lines += ["/stats — statistika", "/reload — zallar jadvalini qayta o'qish"]
    await message.answer("\n".join(lines))


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in common.admin_ids():
        await message.answer(NO_ACCESS_ADMIN)
        return
    s = await db.stats()
    today = await db.day_counters()
    names = {db.safe_key(g["id"]): g["name"] for g in await sheets.get_gyms()}
    await message.answer(stats_text(s, today, names))


@router.message(Command("reload"))
async def cmd_reload(message: Message):
    if message.from_user.id not in common.admin_ids():
        await message.answer(NO_ACCESS_ADMIN)
        return
    started = time.time()
    gyms = await sheets.get_gyms(force=True)
    if sheets.loaded_at() < started:
        # get_gyms xatoni yutib, eski ro'yxatni qaytaradi: yangilanganini loaded_at() dan bilamiz
        await message.answer(
            f"❌ Jadvalni o'qib bo'lmadi. Eski ro'yxat ishlatilmoqda ({len(gyms)} ta zal).\n"
            "Sabab loglarda; jadval service account'ga ulashilganini tekshiring."
        )
        return
    problems = sheets.problems()
    lines = [f"✅ Jadval yangilandi: <b>{len(gyms)}</b> ta faol zal."]
    if problems:
        lines += ["", f"⚠️ <b>Muammolar ({len(problems)}):</b>"]
        lines += [f"• {esc(p)}" for p in problems[:MAX_PROBLEMS_SHOWN]]
        if len(problems) > MAX_PROBLEMS_SHOWN:
            lines.append(f"…va yana {len(problems) - MAX_PROBLEMS_SHOWN} ta")
    else:
        lines.append("Jadvalda muammo topilmadi 👍")
    await reply_lines(message, lines)


@router.message(Command("gyms"))
async def cmd_gyms(message: Message):
    uid = message.from_user.id
    role = await staff_role(uid)
    if role is None:
        await message.answer(NO_ACCESS_STAFF)
        return
    if role == "admin":
        lines = gyms_overview(await sheets.get_gyms(), sheets.problems())
    else:
        mine = await owned_gyms(uid)
        lines = [f"🏋️ <b>Sizning zallaringiz: {len(mine)}</b>", ""] + [
            gym_line(g) for g in sorted(mine, key=lambda g: g["name"].lower())
        ]
    await reply_lines(message, lines)


@router.message(Command("myleads", "leads"))
async def cmd_myleads(message: Message):
    uid = message.from_user.id
    role = await staff_role(uid)
    if role is None:
        await message.answer(NO_ACCESS_STAFF)
        return
    leads = await db.open_leads_of_owner(uid, limit=30)
    title = "📋 <b>Ochiq arizalar</b> (Yangi va Aloqa qilindi)"
    if not leads and role == "admin":
        leads = await db.recent_leads(MAX_CARDS)
        title = "📋 <b>So'nggi arizalar</b>"
    if not leads:
        await message.answer("Hozircha ochiq arizalar yo'q 👍")
        return
    shown = leads[:MAX_CARDS]
    more = f"\nKo'rsatildi: {len(shown)} / {len(leads)}" if len(leads) > len(shown) else ""
    await message.answer(title + more)
    for lead in shown:
        await safe_send(message.bot, uid, lead_card_uz(lead), crm_kb(lead))


@router.message(F.photo, F.from_user.id.in_(config.SUPER_ADMIN_IDS))
async def admin_photo(message: Message):
    """Super admin rasm yuborsa, jadvalning `photos` ustuni uchun file_id qaytaramiz."""
    file_id = message.photo[-1].file_id
    await message.answer(
        "Jadvalning <b>photos</b> ustuniga shuni qo'ying (bir nechta bo'lsa | bilan ajrating):\n"
        f"<code>{file_id}</code>"
    )


@router.message(Command("unanswered"))
async def cmd_unanswered(message: Message):
    uid = message.from_user.id
    role = await staff_role(uid)
    if role is None:
        await message.answer(NO_ACCESS_STAFF)
        return
    rows = await db.recent_unanswered(50)
    if role == "owner":
        mine = {g["id"] for g in await owned_gyms(uid)}
        rows = [r for r in rows if r.get("gym_id") in mine]
    rows = rows[:10]
    if not rows:
        await message.answer("Javobsiz savollar yo'q 👍")
        return
    names = {g["id"]: g["name"] for g in await sheets.get_gyms()}
    lines = ["🤷 <b>Bot javob bera olmagan savollar</b>", ""]
    for r in rows:
        created = r.get("created_at")
        when = created.astimezone(TZ).strftime("%d.%m %H:%M") if created else ""
        gym = names.get(r.get("gym_id"), r.get("gym_id", ""))
        lines.append(f"<b>{esc(gym)}</b> · {when}\n{esc(r.get('question', ''))}\n")
    lines.append("💡 Bunday savollar javobini jadvaldagi zal ma'lumotlariga (notes ustuni) qo'shing.")
    await reply_lines(message, lines)
