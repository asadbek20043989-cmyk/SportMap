"""Scheduler: avtomatik eslatmalar va fon ishlari.

Har SCHEDULER_INTERVAL_SECONDS da quyidagilar bajariladi:
  1. views    - zalni ko'rib, jim ketganlarga follow-up        (FOLLOWUP_ENABLED bo'lsa)
  2. abandon  - yarim qolgan arizalar uchun follow-up          (FOLLOWUP_ENABLED bo'lsa)
  3. remind   - bepul sinov kuni mijozga eslatma
  4. confirm  - "keldingizmi?" so'rovi
  5. sla      - LEAD_SLA_HOURS dan beri "Yangi" qolgan lead uchun egasiga eslatma
  6. sheets   - Sheets'ga yozilmay qolgan leadlarni qayta yuborish
  7. cleanup  - kuniga bir marta eski FSM/abandon hujjatlarini tozalash

Takroriy yuborishdan himoya: xabar yuborishdan OLDIN navbat maydoni (next_fu_at, remind_at, ...)
None qilinadi (\"band qilish\"), yuborilgandan keyin kerak bo'lsa qayta rejalashtiriladi. Shunda
Firestore yozuvi yiqilsa yoki ikki nusxa parallel ishlasa ham mijozga bir xabar ikki marta bormaydi.
"""
import asyncio
import logging
import time
from datetime import timedelta

from aiogram import Bot

import common
import config
import db
import sheets
from common import btn, kb, lead_card_uz, crm_kb, safe_send, slot_label
from texts import t
from utils import SLOT_HOURS, TZ, adjust_quiet, esc, now_local, now_utc

log = logging.getLogger("followup")

NAME_FALLBACK = {"uz": "do'st", "ru": "друг", "en": "friend"}
MAX_SEND_ERRORS = 3            # xabar yuborib bo'lmasa, shuncha urinishdan keyin to'xtaymiz
RETRY_AFTER_ERROR_HOURS = 1
STALE_REMINDER_HOURS = 6       # bot uzoq o'chib qolgan bo'lsa, eskirgan "bugun sinov" eslatmasi yuborilmaydi
SHEETS_RETRY_SECONDS = 300
SHEETS_RETRY_BATCH = 10
CLEANUP_HOUR = 4               # kunlik tozalash soati (Toshkent)
SEND_PAUSE = 0.05              # xabarlar orasida (Telegram: ~30 xabar/soniya)

_state = {"cleanup_day": None, "sheets_at": 0.0}


# ------------------------------------------------------------------ sof yordamchilar (testlanadi)

def in_quiet_hours(now=None) -> bool:
    """Hozir 'tinch soat'mi (QUIET_START_HOUR..QUIET_END_HOUR, Toshkent vaqti bilan)."""
    local = (now or now_utc()).astimezone(TZ)
    return not (config.QUIET_END_HOUR <= local.hour < config.QUIET_START_HOUR)


def gap_until(user: dict | None, now=None):
    """Bir odamga ketma-ket eslatma oralig'i hali o'tmagan bo'lsa, ruxsat etilgan vaqt; aks holda None."""
    last = (user or {}).get("last_fu_at")
    if not last:
        return None
    now = now or now_utc()
    ready = last + timedelta(hours=config.FOLLOWUP_MIN_GAP_HOURS)
    return ready if ready > now else None


def opted_out(user: dict | None) -> bool:
    """Foydalanuvchi eslatmalarni o'chirgan (/stop) yoki botni bloklagan."""
    user = user or {}
    return bool(user.get("followup_off") or user.get("blocked"))


def display_name(user: dict | None, lang: str) -> str:
    name = " ".join(str((user or {}).get("name") or "").split())
    return esc(name) if name else NAME_FALLBACK.get(lang, NAME_FALLBACK["uz"])


def pick_lang(*sources) -> str:
    for src in sources:
        lang = (src or {}).get("lang")
        if lang:
            return lang
    return "uz"


# ------------------------------------------------------------------ 1-2. follow-up (ko'rganlar va yarim qolganlar)

async def _drop(coll: str, doc_id: str) -> None:
    await db.patch(coll, doc_id, next_fu_at=None)


async def _followup_one(bot: Bot, coll: str, item: dict, now) -> None:
    kind = "abandon" if coll == "abandon" else "viewed"
    doc_id = item["_id"]
    tg_id = item.get("tg_id")
    gym_id = item.get("gym_id")
    sent = int(item.get("fu_sent") or 0)

    if not tg_id or not gym_id or item.get("converted") or sent >= config.FOLLOWUP_MAX:
        await _drop(coll, doc_id)
        return
    user = await db.get_user(tg_id) or {}
    if opted_out(user):
        await _drop(coll, doc_id)
        return
    wait = gap_until(user, now)
    if wait:  # yaqinda boshqa eslatma yuborilgan: keyinroqqa suramiz
        await db.patch(coll, doc_id, next_fu_at=adjust_quiet(wait, config.QUIET_START_HOUR, config.QUIET_END_HOUR))
        return
    gym = await sheets.get_gym(gym_id)
    if not gym:  # zal jadvaldan olib tashlangan yoki faol emas
        await _drop(coll, doc_id)
        return
    if kind == "abandon" and await db.find_recent_lead(tg_id, gym_id, item.get("type", "join")):
        await db.delete_doc("abandon", doc_id)  # ariza keyinroq baribir qoldirilgan
        return

    if not await db.patch(coll, doc_id, next_fu_at=None):  # band qilish; yozib bo'lmasa yubormaymiz
        return

    lang = pick_lang(item, user)
    name = display_name(user, lang)
    gym_name = esc(gym["name"])
    if kind == "viewed":
        trial = t(lang, "fu_trial_line") if gym.get("free_trial") else ""
        text = t(lang, f"fu_viewed_{min(sent + 1, 2)}", name=name, gym=gym_name, trial=trial)
        markup = common.fu_keyboard(lang, gym["id"])
        next_hours = config.FOLLOWUP_SECOND_HOURS
    else:
        text = t(lang, f"fu_abandon_{min(sent + 1, 2)}", name=name, gym=gym_name)
        action = "trial" if (item.get("type") == "trial" and gym.get("free_trial")) else "join"
        markup = kb([
            [btn(t(lang, "btn_trial" if action == "trial" else "btn_join"), f"{action}:{gym['id']}:f")],
            [btn(t(lang, "btn_fu_stop"), "fu:stop")],
        ])
        next_hours = config.FOLLOWUP_FIRST_HOURS

    result = await safe_send(bot, tg_id, text, markup)
    if result == "ok":
        await db.followup_done(coll, doc_id, sent + 1, next_hours)
        await db.set_user_last_followup(tg_id)
        await db.bump("followups_sent")
    elif result == "error":
        errors = int(item.get("fu_err") or 0) + 1
        retry = None
        if errors < MAX_SEND_ERRORS:
            retry = adjust_quiet(now + timedelta(hours=RETRY_AFTER_ERROR_HOURS), config.QUIET_START_HOUR, config.QUIET_END_HOUR)
        await db.patch(coll, doc_id, fu_err=errors, next_fu_at=retry)
    # "blocked": safe_send foydalanuvchini belgilab qo'ygan, navbat allaqachon bo'sh


async def process_followups(bot: Bot, coll: str) -> None:
    now = now_utc()
    if in_quiet_hours(now):
        return  # navbatdagilar o'z joyida turadi, tinch soat tugagach yuboriladi
    if not await sheets.get_gyms():
        return  # jadval o'qilmagan: zallarni 'yo'q' deb hisoblab navbatni tozalab yubormaymiz
    for item in await db.due(coll, "next_fu_at"):
        try:
            await _followup_one(bot, coll, item, now)
        except Exception:
            log.exception("Follow-up xatosi (%s/%s)", coll, item.get("_id"))
        await asyncio.sleep(SEND_PAUSE)


async def process_views(bot: Bot) -> None:
    await process_followups(bot, "views")


async def process_abandon(bot: Bot) -> None:
    await process_followups(bot, "abandon")


# ------------------------------------------------------------------ 3. sinov kuni eslatmasi

def _is_open_trial(lead: dict) -> bool:
    return lead.get("type") == "trial" and lead.get("status", "new") in ("new", "contacted")


async def process_reminders(bot: Bot) -> None:
    """Sinov kuni eslatma. Tinch soat tekshirilmaydi: ertalabki eslatma 07:00 da ketadi (mijoz o'zi tanlagan vaqt)."""
    now = now_utc()
    for lead in await db.due("leads", "remind_at"):
        try:
            lid = lead.get("lead_id") or lead["_id"]
            due_at = lead.get("remind_at")
            stale = due_at is not None and (now - due_at) > timedelta(hours=STALE_REMINDER_HOURS)
            if not _is_open_trial(lead) or lead.get("visit_slot") not in SLOT_HOURS or stale:
                await db.update_lead(lid, remind_at=None)
                continue
            if not await db.update_lead(lid, remind_at=None):  # band qilish
                continue
            user = await db.get_user(lead["tg_id"]) or {}
            if opted_out(user):
                continue
            lang = pick_lang(lead, user)
            gym = await sheets.get_gym(lead.get("gym_id", ""))
            address = esc(gym.get("address")) if gym and gym.get("address") else ""
            text = t(lang, "remind_visit", gym=esc(lead["gym"]), slot=slot_label(lang, lead["visit_slot"]), address=address)
            if not address:
                text = text.rsplit("\n", 1)[0]  # bo'sh "📍" qatorini olib tashlaymiz
            markup = kb([[btn(t(lang, "btn_visit_resched"), f"v:r:{lid}")]])
            await safe_send(bot, lead["tg_id"], text, markup)
        except Exception:
            log.exception("Sinov eslatmasi xatosi (%s)", lead.get("_id"))
        await asyncio.sleep(SEND_PAUSE)


# ------------------------------------------------------------------ 4. "keldingizmi?"

async def process_confirms(bot: Bot) -> None:
    now = now_utc()
    if in_quiet_hours(now):
        return
    for lead in await db.due("leads", "confirm_at"):
        try:
            lid = lead.get("lead_id") or lead["_id"]
            if not _is_open_trial(lead):
                await db.update_lead(lid, confirm_at=None)
                continue
            if not await db.update_lead(lid, confirm_at=None):  # band qilish
                continue
            user = await db.get_user(lead["tg_id"]) or {}
            if opted_out(user):
                continue
            lang = pick_lang(lead, user)
            text = t(lang, "confirm_visit", name=esc(lead["name"]), gym=esc(lead["gym"]))
            markup = kb([[btn(t(lang, "btn_visit_yes"), f"v:y:{lid}"), btn(t(lang, "btn_visit_no"), f"v:n:{lid}")]])
            if await safe_send(bot, lead["tg_id"], text, markup) == "ok":
                await db.update_lead(lid, confirm_sent_at=now)
        except Exception:
            log.exception("'Keldingizmi?' xatosi (%s)", lead.get("_id"))
        await asyncio.sleep(SEND_PAUSE)


# ------------------------------------------------------------------ 5. egasiga SLA eslatmasi

async def process_sla(bot: Bot) -> None:
    for lead in await db.due("leads", "sla_at"):
        try:
            lid = lead.get("lead_id") or lead["_id"]
            if lead.get("status", "new") != "new":
                await db.update_lead(lid, sla_at=None)
                continue
            if not await db.update_lead(lid, sla_at=None):  # band qilish
                continue
            header = f"⏰ <b>Eslatma:</b> ariza {config.LEAD_SLA_HOURS} soatdan beri «Yangi» holatda — {esc(lead['gym'])}"
            text = lead_card_uz(lead, header)
            markup = crm_kb(lead)
            owner = lead.get("owner_id")
            delivered = bool(owner) and await safe_send(bot, owner, text, markup) == "ok"
            if not delivered:
                warn = "⚠️ <i>Zal egasiga yetmadi</i>\n\n"
                for admin in common.admin_ids() - {owner}:
                    await safe_send(bot, admin, warn + text, markup)
            await db.update_lead(lid, sla_notified=True)
            await db.bump("sla_reminders")
        except Exception:
            log.exception("SLA eslatmasi xatosi (%s)", lead.get("_id"))
        await asyncio.sleep(SEND_PAUSE)


# ------------------------------------------------------------------ 6. Sheets'ga qayta yuborish

async def retry_sheets(_bot: Bot | None = None) -> None:
    if time.time() - _state["sheets_at"] < SHEETS_RETRY_SECONDS:
        return
    _state["sheets_at"] = time.time()
    for lead in await db.sheet_pending_leads(limit=SHEETS_RETRY_BATCH):
        lid = lead.get("lead_id")
        try:
            await sheets.append_lead(common.lead_row(lead))
        except Exception:
            log.warning("Leadni Sheets'ga qayta yozib bo'lmadi (#%s); keyinroq urinamiz", lid, exc_info=True)
            return
        if not await db.update_lead(lid, sheet_ok=True):
            # qator yozildi, lekin belgi qo'yilmadi: davom etsak jadvalda takroriy qatorlar paydo bo'ladi
            log.error("Lead #%s Sheets'ga yozildi, lekin sheet_ok belgisi qo'yilmadi — to'xtatildi", lid)
            return


# ------------------------------------------------------------------ 7. kunlik tozalash

async def maybe_cleanup(_bot: Bot | None = None) -> None:
    local = now_local()
    day = local.strftime("%Y%m%d")
    if local.hour < CLEANUP_HOUR or _state["cleanup_day"] == day:
        return
    _state["cleanup_day"] = day
    removed = await db.cleanup_old()
    log.info("Kunlik tozalash: %s ta eski hujjat o'chirildi", removed)


# ------------------------------------------------------------------ asosiy sikl

def jobs():
    items = []
    if config.FOLLOWUP_ENABLED:
        items += [("views", process_views), ("abandon", process_abandon)]
    items += [
        ("remind", process_reminders),
        ("confirm", process_confirms),
        ("sla", process_sla),
        ("sheets", retry_sheets),
        ("cleanup", maybe_cleanup),
    ]
    return items


async def tick(bot: Bot) -> None:
    """Bitta ish yiqilsa, qolganlari to'xtamaydi."""
    for name, job in jobs():
        try:
            await job(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scheduler: '%s' ishi xato bilan tugadi", name)


async def run(bot: Bot) -> None:
    interval = max(30, config.SCHEDULER_INTERVAL_SECONDS)
    log.info("Scheduler ishga tushdi (har %s soniyada, follow-up %s)", interval, "yoqilgan" if config.FOLLOWUP_ENABLED else "o'chirilgan")
    await asyncio.sleep(10)  # bot to'liq ishga tushib olsin
    while True:
        await tick(bot)
        await asyncio.sleep(interval)


def start(bot: Bot) -> asyncio.Task:
    return asyncio.create_task(run(bot), name="scheduler")
