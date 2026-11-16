"""Umumiy yordamchilar: tugmalar, zal kartasi, lead yuborish, egasiga xabar, xavfsiz yuborish."""
import asyncio
import logging
from datetime import date, timedelta

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import config
import db
import sheets
from texts import DISTRICT_NAMES, DISTRICTS, STATUS_LABELS, TEXTS, WEEKDAYS, t
from utils import (
    SLOT_HOURS,
    TZ,
    adjust_quiet,
    available_slots,
    esc,
    fmt_km,
    maps_urls,
    now_local,
    now_utc,
    parse_day,
    upcoming_days,
    visit_schedule,
)

log = logging.getLogger("common")

SOURCES = {"b": "browse", "r": "recommend", "n": "near", "f": "followup"}
GOAL_UZ = {"lose": "Ozish", "muscle": "Mushak chiqarish", "healthy": "Sog'lom turmush tarzi", "other": "Boshqa"}
TYPE_UZ = {"join": "📝 Yozilish", "trial": "🎁 Bepul sinov"}
STATUS_CODES = {"new": "o", "contacted": "c", "visited": "v", "won": "w", "lost": "l", "no_show": "n"}
CODE_STATUS = {v: k for k, v in STATUS_CODES.items()}


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_ids() -> set[int]:
    return set(config.SUPER_ADMIN_IDS)


# ------------------------------------------------------------------ xavfsiz yuborish / tahrirlash

async def safe_send(bot: Bot, chat_id: int, text: str, reply_markup=None, **kw) -> str:
    """'ok' | 'blocked' | 'error'. Bot bloklangan bo'lsa foydalanuvchi belgilab qo'yiladi."""
    for attempt in (1, 2):
        try:
            await bot.send_message(chat_id, text, reply_markup=reply_markup, **kw)
            return "ok"
        except TelegramRetryAfter as e:
            if attempt == 2:
                return "error"
            await asyncio.sleep(min(e.retry_after, 30))
        except TelegramForbiddenError:
            await db.mark_blocked(chat_id, True)
            return "blocked"
        except TelegramBadRequest as e:
            log.warning("Xabar yuborilmadi (%s): %s", chat_id, e)
            return "error"
        except TelegramNetworkError:
            if attempt == 2:
                return "error"
            await asyncio.sleep(2)
        except Exception:
            log.exception("Kutilmagan xato (send_message %s)", chat_id)
            return "error"
    return "error"


async def answer_cb(cb: CallbackQuery, text: str | None = None, alert: bool = False) -> None:
    try:
        await cb.answer(text, show_alert=alert)
    except Exception:
        pass


async def show(cb: CallbackQuery, text: str, markup=None) -> None:
    """Xabarni tahrirlaydi; bo'lmasa (masalan, rasmli xabar) o'chirib, yangisini yuboradi."""
    msg = cb.message
    try:
        await msg.edit_text(text, reply_markup=markup)
        return
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return
    except Exception:
        log.debug("edit_text bo'lmadi", exc_info=True)
    try:
        await msg.delete()
    except Exception:
        pass
    await safe_send(cb.bot, cb.from_user.id, text, markup)


# ------------------------------------------------------------------ nomlar va yorliqlar

def district_name(g: dict, lang: str) -> str:
    names = DISTRICT_NAMES.get(g.get("district_key"))
    return names.get(lang) or names["uz"] if names else g.get("district", "")


def district_label(key: str, lang: str) -> str:
    names = DISTRICT_NAMES[key]
    return names.get(lang) or names["uz"]


def contact_of(g: dict, lang: str) -> str:
    parts = []
    if g.get("phone"):
        parts.append(esc(g["phone"]))
    if g.get("tg_username"):
        parts.append(f"@{esc(g['tg_username'])}")
    return " · ".join(parts) if parts else t(lang, "no_contact")


def day_label(lang: str, day: date) -> str:
    today = now_local().date()
    if day == today:
        return t(lang, "day_today")
    if day == today + timedelta(days=1):
        return t(lang, "day_tomorrow")
    return f"{WEEKDAYS[lang][day.weekday()]} {day:%d.%m}"


def slot_label(lang: str, slot: str) -> str:
    return t(lang, f"slot_{slot}")


def visit_label(lang: str, day: date, slot: str) -> str:
    return f"{day_label(lang, day)}, {slot_label(lang, slot)}"


# ------------------------------------------------------------------ menyu, ro'yxat, karta

def main_menu_kb(lang: str) -> InlineKeyboardMarkup:
    rows, row = [], []
    for key, names in DISTRICTS:
        row.append(btn(names.get(lang) or names["uz"], f"d:{key}:0"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([btn(t(lang, "btn_recommend"), "rec:start")])
    rows.append([btn(t(lang, "btn_near"), "near:start")])
    return kb(rows)


def gym_list(gyms: list[dict], lang: str, district_key: str, page: int):
    """Tumandagi zallar ro'yxati (sahifalangan): (matn, tugmalar | None)."""
    items = sorted((g for g in gyms if g["district_key"] == district_key), key=lambda g: g["name"].lower())
    if not items:
        return t(lang, "no_gyms"), kb([[btn(t(lang, "back_districts"), "menu")]])
    size = max(1, config.PAGE_SIZE)
    pages = (len(items) + size - 1) // size
    page = max(0, min(page, pages - 1))
    chunk = items[page * size:(page + 1) * size]
    rows = [[btn(("🎁 " if g["free_trial"] else "") + g["name"], f"g:{g['id']}:b")] for g in chunk]
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(btn("◀️", f"d:{district_key}:{page - 1}"))
        nav.append(btn(f"{page + 1}/{pages}", "x"))
        if page < pages - 1:
            nav.append(btn("▶️", f"d:{district_key}:{page + 1}"))
        rows.append(nav)
    rows.append([btn(t(lang, "back_districts"), "menu")])
    text = t(lang, "gyms_in", district=district_label(district_key, lang), n=len(items)) + "\n\n" + t(lang, "gyms_footer")
    return text, kb(rows)


def gym_card_text(g: dict, lang: str) -> str:
    lines = [f"<b>{esc(g['name'])}</b>"]
    for label, key in (("lbl_address", "address"), ("lbl_hours", "hours"), ("lbl_prices", "prices"), ("lbl_amenities", "amenities")):
        if g.get(key):
            lines.append(f"{t(lang, label)}: {esc(g[key])}")
    lines.append(f"{t(lang, 'lbl_phone')}: {contact_of(g, lang)}")
    if g.get("free_trial"):
        lines.append("")
        lines.append(t(lang, "lbl_trial"))
    return "\n".join(lines)


def gym_card_kb(g: dict, lang: str, src: str = "b") -> InlineKeyboardMarkup:
    gid = g["id"]
    rows = [[btn(t(lang, "btn_ask"), f"ask:{gid}"), btn(t(lang, "btn_join"), f"join:{gid}:{src}")]]
    if g.get("free_trial"):
        rows.append([btn(t(lang, "btn_trial"), f"trial:{gid}:{src}")])
    if g.get("lat") is not None and g.get("lon") is not None:
        google, yandex = maps_urls(g["lat"], g["lon"])
        rows.append([
            InlineKeyboardButton(text=t(lang, "btn_map_google"), url=google),
            InlineKeyboardButton(text=t(lang, "btn_map_yandex"), url=yandex),
        ])
    rows.append([btn(t(lang, "back_list"), f"d:{g['district_key']}:0"), btn(t(lang, "btn_menu"), "menu")])
    return kb(rows)


def gym_buttons(items: list[tuple[dict, str]], src: str) -> list[list[InlineKeyboardButton]]:
    """[(zal, qo'shimcha yozuv)] -> har biri uchun bitta tugma."""
    return [[btn(f"{g['name']}{(' · ' + extra) if extra else ''}", f"g:{g['id']}:{src}")] for g, extra in items]


def dates_kb(lang: str) -> InlineKeyboardMarkup | None:
    days = upcoming_days(4)
    if not days:
        return None
    buttons = [btn(f"{day_label(lang, d)} · {d:%d.%m}", f"day:{d.isoformat()}") for d in days]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([btn(t(lang, "btn_menu"), "menu")])
    return kb(rows)


def slots_kb(lang: str, day: date) -> InlineKeyboardMarkup | None:
    slots = available_slots(day)
    if not slots:
        return None
    rows = [[btn(slot_label(lang, s), f"slot:{s}")] for s in slots]
    rows.append([btn(t(lang, "btn_menu"), "menu")])
    return kb(rows)


def fu_keyboard(lang: str, gym_id: str, with_join: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if with_join:
        rows.append([btn(t(lang, "btn_join"), f"join:{gym_id}:f"), btn(t(lang, "btn_ask"), f"ask:{gym_id}")])
    rows.append([btn(t(lang, "btn_fu_stop"), "fu:stop")])
    return kb(rows)


# ------------------------------------------------------------------ zal egasi: xabar va CRM tugmalari

def _tg_link(lead: dict) -> str:
    if lead.get("tg_username"):
        return f"@{esc(lead['tg_username'])}"
    return f'<a href="tg://user?id={lead["tg_id"]}">profil</a>'


def lead_card_uz(lead: dict, header: str | None = None) -> str:
    status = STATUS_LABELS.get(lead.get("status", "new"), "")
    verified = "✅ Telegram orqali tasdiqlangan" if lead.get("phone_verified") else "⚠️ qo'lda kiritilgan"
    created = lead.get("created_at")
    when = created.astimezone(TZ).strftime("%d.%m %H:%M") if created else ""
    lines = [
        header or f"🔔 <b>Yangi ariza</b> — {esc(lead['gym'])}",
        "",
        f"👤 {esc(lead['name'])}",
        f"📞 {esc(lead['phone'])} ({verified})",
        f"🎯 Maqsad: {GOAL_UZ.get(lead.get('goal'), esc(lead.get('goal')))}",
        f"📋 Tur: {TYPE_UZ.get(lead.get('type'), '')}",
    ]
    if lead.get("type") == "trial" and lead.get("visit_day"):
        day = parse_day(lead["visit_day"])
        if day:
            lines.append(f"📅 Sinov vaqti: {visit_label('uz', day, lead['visit_slot'])}")
    lines += [
        f"✈️ Telegram: {_tg_link(lead)}",
        f"🕒 {when} · #{lead['lead_id']}",
        "",
        f"Holat: <b>{status}</b>",
    ]
    return "\n".join(lines)


def crm_kb(lead: dict) -> InlineKeyboardMarkup:
    current = lead.get("status", "new")
    lid = lead["lead_id"]

    def b(status: str):
        label = STATUS_LABELS[status]
        return btn(("✔ " if status == current else "") + label, f"st:{lid}:{STATUS_CODES[status]}")

    return kb([[b("contacted"), b("visited")], [b("won"), b("lost")], [b("no_show")]])


async def notify_lead(bot: Bot, lead: dict, gym: dict, header: str | None = None, with_crm: bool = True) -> bool:
    """Leadni zal egasiga yuboradi. Egasiga yetmasa (yoki NOTIFY_SUPER_ADMINS yoqilgan bo'lsa)
    super adminlarga ham yuboradi. Hech kimga yetmasa False qaytaradi."""
    text = lead_card_uz(lead, header)
    markup = crm_kb(lead) if with_crm else None
    owner = gym.get("owner_tg_id")
    owner_ok = bool(owner) and await safe_send(bot, owner, text, markup) == "ok"
    reached = owner_ok
    if not owner_ok or config.NOTIFY_SUPER_ADMINS:
        warn = "" if owner_ok else "⚠️ <i>Zal egasiga yetmadi (owner_tg_id yo'q yoki bot bloklangan)</i>\n\n"
        for admin in admin_ids() - {owner}:
            if await safe_send(bot, admin, warn + text, markup) == "ok":
                reached = True
    if not reached:
        log.error("Lead %s hech kimga yetkazilmadi (zal %s)", lead.get("lead_id"), gym.get("id"))
    return reached


async def notify_owner_note(bot: Bot, gym: dict, text: str) -> None:
    """Qisqa ma'lumot (mijoz keldi / vaqtni o'zgartirdi). Egasi yo'q bo'lsa adminlarga."""
    owner = gym.get("owner_tg_id")
    if owner and await safe_send(bot, owner, text) == "ok":
        return
    for admin in admin_ids() - {owner}:
        await safe_send(bot, admin, text)


# ------------------------------------------------------------------ lead yaratish

_locks: dict[int, asyncio.Lock] = {}


def _user_lock(user_id: int) -> asyncio.Lock:
    return _locks.setdefault(user_id, asyncio.Lock())


def lead_row(lead: dict) -> list:
    visit = ""
    if lead.get("type") == "trial" and lead.get("visit_day"):
        visit = f"{lead['visit_day']} {lead['visit_slot']}"
    created = lead["created_at"].astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")
    return [
        created, lead["gym_id"], lead["gym"], lead["name"], lead["phone"],
        "ha" if lead.get("phone_verified") else "yo'q",
        GOAL_UZ.get(lead.get("goal"), lead.get("goal", "")),
        "Bepul sinov" if lead.get("type") == "trial" else "Yozilish",
        visit, lead.get("lang", ""), lead.get("source", ""),
        lead["tg_id"], lead.get("tg_username", ""), lead["lead_id"],
    ]


async def submit_lead(bot: Bot, user, lang: str, data: dict) -> tuple[str, str]:
    """Arizani saqlaydi va egasiga yuboradi.

    Qaytaradi: (natija, mijozga matn). natija: ok | dup | limit | fail | missing.
    'ok' faqat ariza kamida bitta joyga (Firestore yoki Sheets) saqlanganda."""
    gym = await sheets.get_gym(data.get("gym_id", ""))
    if not gym:
        return "missing", t(lang, "gym_missing")
    lead_type = data.get("lead_type", "join")
    name = (data.get("name") or "").strip()
    phone = data.get("phone")
    if not name or not phone:
        return "fail", t(lang, "lead_fail")

    async with _user_lock(user.id):
        if await db.find_recent_lead(user.id, gym["id"], lead_type):
            return "dup", t(lang, "dup_lead", gym=esc(gym["name"]), contact=contact_of(gym, lang))
        if not await db.check_daily_limit(user.id) or await db.phone_recent_count(phone) >= config.LEAD_LIMIT_PER_DAY:
            return "limit", t(lang, "lead_limit")

        now = now_utc()
        remind_at = confirm_at = None
        day = None
        slot = data.get("slot")
        if lead_type == "trial":
            day = parse_day(data.get("day"))
            if day is None or slot not in SLOT_HOURS:
                return "fail", t(lang, "lead_fail")
            remind_at, confirm_at = visit_schedule(day, slot)

        lead = {
            "lead_id": db.new_id(),
            "gym_id": gym["id"],
            "gym": gym["name"],
            "name": name,
            "phone": phone,
            "phone_verified": bool(data.get("phone_verified")),
            "goal": data.get("goal", "other"),
            "type": lead_type,
            "visit_day": day.isoformat() if day else None,
            "visit_slot": slot if day else None,
            "lang": lang,
            "source": SOURCES.get(data.get("source", "b"), "browse"),
            "tg_id": user.id,
            "tg_username": user.username or "",
            "owner_id": gym.get("owner_tg_id"),
            "status": "new",
            "created_at": now,
            "status_at": now,
            "sla_at": adjust_quiet(now + timedelta(hours=config.LEAD_SLA_HOURS), config.QUIET_START_HOUR, config.QUIET_END_HOUR),
            "remind_at": remind_at,
            "confirm_at": confirm_at,
            "owner_notified": False,
        }

        sheet_ok = True
        try:
            await sheets.append_lead(lead_row(lead))
        except Exception:
            sheet_ok = False
            log.exception("Leadni Sheets'ga yozib bo'lmadi")
        lead["sheet_ok"] = sheet_ok

        db_ok = True
        try:
            await db.create_lead(lead)
        except Exception:
            db_ok = False
            log.exception("Leadni Firestore'ga yozib bo'lmadi")

        if not sheet_ok and not db_ok:
            return "fail", t(lang, "lead_fail")

        await db.bump_daily_count(user.id)
        await db.mark_converted(user.id, gym["id"])
        await db.upsert_user(user.id, name=name, username=user.username or "")

    # --- bu yerdan keyin ariza allaqachon saqlangan: xatolar mijozga ko'rsatilmaydi
    await db.bump("leads_total", gym["id"])
    await db.bump(f"leads_{lead_type}")
    reached = await notify_lead(bot, lead, gym, with_crm=db_ok)
    if db_ok:
        await db.update_lead(lead["lead_id"], owner_notified=reached)
    if not db_ok:
        for admin in admin_ids():
            await safe_send(bot, admin, f"⚠️ Lead Firestore'ga yozilmadi (Sheets'da bor): #{lead['lead_id']}. CRM tugmalari ishlamaydi.")

    if lead_type == "trial":
        text = t(lang, "done_trial", name=esc(name), gym=esc(gym["name"]), when=visit_label(lang, day, slot))
    else:
        text = t(lang, "done", name=esc(name), gym=esc(gym["name"]))
    return "ok", text


async def reschedule_visit(bot: Bot, lang: str, lead_id: str, day: date, slot: str) -> str | None:
    """Sinov vaqtini o'zgartiradi. Mijozga matn qaytaradi (topilmasa None)."""
    lead = await db.get_lead(lead_id)
    if not lead:
        return None
    remind_at, confirm_at = visit_schedule(day, slot)
    ok = await db.update_lead(
        lead_id,
        visit_day=day.isoformat(),
        visit_slot=slot,
        remind_at=remind_at,
        confirm_at=confirm_at,
        status="contacted" if lead.get("status") == "no_show" else lead.get("status", "new"),
    )
    if not ok:
        return None
    gym = await sheets.get_gym(lead["gym_id"])
    if gym:
        await notify_owner_note(
            bot, gym,
            f"🔁 <b>{esc(lead['name'])}</b> ({esc(lead['phone'])}) sinov vaqtini o'zgartirdi: {visit_label('uz', day, slot)}\n#{lead_id}",
        )
    return t(lang, "resched_done", when=visit_label(lang, day, slot), gym=esc(lead["gym"]))

