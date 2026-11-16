"""Firestore: foydalanuvchilar, leadlar, ko'rishlar (follow-up), hisoblagichlar.

Muhim qoida: so'rovlarda faqat bitta maydon bo'yicha tengsizlik (<=, >=) ishlatiladi,
qolgan shartlar Python'da tekshiriladi. Shunda Firestore'da qo'lda indeks yaratish shart emas.
"""
import asyncio
import logging
import re
import secrets
import time
from datetime import datetime, timedelta

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import DELETE_FIELD, Increment

import config
from utils import TZ, adjust_quiet, now_local, now_utc

log = logging.getLogger("db")

_client = None
_lang_cache: dict[int, str] = {}

STATUSES = ("new", "contacted", "visited", "won", "lost", "no_show")


def init():
    """Firebase'ni ishga tushiradi (main.py'dan bir marta chaqiriladi)."""
    global _client
    if _client is not None:
        return _client
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(config.google_credentials()))
    _client = firestore.client()
    return _client


def col(name: str):
    return init().collection(name)


async def run(fn, *args, **kwargs):
    """Sinxron Firestore chaqiruvini oqimda bajaradi (bot qotib qolmasligi uchun)."""
    return await asyncio.to_thread(fn, *args, **kwargs)


def new_id() -> str:
    """Qisqa lead id (callback_data 64 baytdan oshmasligi uchun)."""
    return f"{int(time.time()):x}{secrets.token_hex(2)}"


def safe_key(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]+", "_", str(value))[:40] or "x"


def _log_fail(what: str):
    log.exception("Firestore xatosi: %s", what)


# ---------------------------------------------------------------- foydalanuvchilar

async def get_user(tg_id: int) -> dict | None:
    try:
        snap = await run(lambda: col("users").document(str(tg_id)).get())
        return snap.to_dict() if snap.exists else None
    except Exception:
        _log_fail("get_user")
        return None


async def upsert_user(tg_id: int, **fields) -> None:
    fields["last_seen"] = now_utc()
    try:
        await run(lambda: col("users").document(str(tg_id)).set(fields, merge=True))
    except Exception:
        _log_fail("upsert_user")


async def get_lang(tg_id: int) -> str:
    if tg_id in _lang_cache:
        return _lang_cache[tg_id]
    user = await get_user(tg_id)
    lang = (user or {}).get("lang") or "uz"
    if user and user.get("lang"):
        _lang_cache[tg_id] = lang
    return lang


async def set_lang(tg_id: int, lang: str, **extra) -> None:
    _lang_cache[tg_id] = lang
    await upsert_user(tg_id, lang=lang, **extra)


async def has_lang(tg_id: int) -> bool:
    if tg_id in _lang_cache:
        return True
    user = await get_user(tg_id)
    if user and user.get("lang"):
        _lang_cache[tg_id] = user["lang"]
        return True
    return False


async def set_followups(tg_id: int, enabled: bool) -> None:
    await upsert_user(tg_id, followup_off=not enabled)


async def mark_blocked(tg_id: int, blocked: bool) -> None:
    await upsert_user(tg_id, blocked=blocked)


async def check_daily_limit(tg_id: int) -> bool:
    """True = hali ariza qoldirish mumkin."""
    user = await get_user(tg_id) or {}
    today = now_local().strftime("%Y-%m-%d")
    if user.get("lead_day") != today:
        return True
    return int(user.get("lead_count") or 0) < config.LEAD_LIMIT_PER_DAY


async def bump_daily_count(tg_id: int) -> None:
    today = now_local().strftime("%Y-%m-%d")
    user = await get_user(tg_id) or {}
    count = int(user.get("lead_count") or 0) + 1 if user.get("lead_day") == today else 1
    await upsert_user(tg_id, lead_day=today, lead_count=count)


# ---------------------------------------------------------------- FSM (storage.py ishlatadi)

def fsm_doc(doc_id: str):
    return col("fsm").document(doc_id)


# ---------------------------------------------------------------- leadlar

def _lead_ref(lead_id: str):
    return col("leads").document(lead_id)


def lead_from_doc(snap) -> dict:
    """Hujjatni lug'atga aylantiradi. Eski versiya (v1) arizalarida `lead_id` va `gym` maydonlari yo'q:
    hujjat id'si lead_id bo'ladi, `gym_name` esa `gym` o'rniga ishlatiladi."""
    data = snap.to_dict() or {}
    data.setdefault("lead_id", snap.id)
    data.setdefault("gym", data.get("gym_name") or data.get("gym_id") or "")
    return data


async def create_lead(lead: dict) -> None:
    """Asosiy saqlash joyi. Xato bo'lsa istisno ko'tariladi (chaqiruvchi hal qiladi)."""
    await run(lambda: _lead_ref(lead["lead_id"]).set(lead))


async def get_lead(lead_id: str) -> dict | None:
    try:
        snap = await run(lambda: _lead_ref(lead_id).get())
        return lead_from_doc(snap) if snap.exists else None
    except Exception:
        _log_fail("get_lead")
        return None


async def update_lead(lead_id: str, **fields) -> bool:
    try:
        await run(lambda: _lead_ref(lead_id).update(fields))
        return True
    except Exception:
        _log_fail("update_lead")
        return False


async def find_recent_lead(tg_id: int, gym_id: str, lead_type: str) -> dict | None:
    """Oxirgi LEAD_DEDUPE_HOURS ichida shu zalga shu turdagi ariza bormi."""
    since = now_utc() - timedelta(hours=config.LEAD_DEDUPE_HOURS)
    try:
        docs = await run(
            lambda: list(
                col("leads").where("tg_id", "==", tg_id).where("gym_id", "==", gym_id).limit(50).stream()
            )
        )
    except Exception:
        _log_fail("find_recent_lead")
        return None
    best = None
    for d in docs:
        data = d.to_dict()
        if data.get("type") != lead_type or data.get("created_at") is None:
            continue
        if data["created_at"] >= since and (best is None or data["created_at"] > best["created_at"]):
            best = data
    return best


async def phone_recent_count(phone: str) -> int:
    """Shu raqam bilan oxirgi 24 soatda nechta ariza (soxta arizalardan himoya)."""
    since = now_utc() - timedelta(hours=24)
    try:
        docs = await run(lambda: list(col("leads").where("phone", "==", phone).limit(50).stream()))
    except Exception:
        _log_fail("phone_recent_count")
        return 0
    return sum(1 for d in docs if (d.to_dict().get("created_at") or since) >= since)


async def open_leads_of_owner(owner_id: int, limit: int = 30) -> list[dict]:
    try:
        docs = await run(
            lambda: list(
                col("leads").where("owner_id", "==", owner_id).where("status", "in", ["new", "contacted"]).limit(limit).stream()
            )
        )
    except Exception:
        _log_fail("open_leads_of_owner")
        return []
    rows = [d.to_dict() for d in docs]
    rows.sort(key=lambda r: r.get("created_at") or now_utc(), reverse=True)
    return rows


async def recent_leads(limit: int = 10) -> list[dict]:
    try:
        docs = await run(
            lambda: list(col("leads").order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit).stream())
        )
        return [lead_from_doc(d) for d in docs]
    except Exception:
        _log_fail("recent_leads")
        return []


async def sheet_pending_leads(limit: int = 20) -> list[dict]:
    try:
        docs = await run(lambda: list(col("leads").where("sheet_ok", "==", False).limit(limit).stream()))
        return [d.to_dict() for d in docs]
    except Exception:
        _log_fail("sheet_pending_leads")
        return []


def visit_fields(day_iso: str, slot: str, remind_at, confirm_at) -> dict:
    return {
        "visit_day": day_iso,
        "visit_slot": slot,
        "remind_at": remind_at,
        "confirm_at": confirm_at,
    }


# ---------------------------------------------------------------- "due" navbatlari (scheduler)

async def due(collection: str, field: str, limit: int = 30) -> list[dict]:
    """field <= hozir bo'lgan hujjatlar. Bajarilganlarda field None qilib qo'yiladi."""
    now = now_utc()
    try:
        docs = await run(lambda: list(col(collection).where(field, "<=", now).limit(limit).stream()))
    except Exception:
        _log_fail(f"due:{collection}.{field}")
        return []
    out = []
    for d in docs:
        data = d.to_dict()
        data["_id"] = d.id
        out.append(data)
    return out


async def patch(collection: str, doc_id: str, **fields) -> bool:
    try:
        await run(lambda: col(collection).document(doc_id).set(fields, merge=True))
        return True
    except Exception:
        _log_fail(f"patch:{collection}")
        return False


async def delete_doc(collection: str, doc_id: str) -> bool:
    try:
        await run(lambda: col(collection).document(doc_id).delete())
        return True
    except Exception:
        _log_fail(f"delete_doc:{collection}")
        return False


async def ping() -> bool:
    """Firestore'ga ulanish ishlayaptimi (ishga tushirishda bir marta tekshiriladi)."""
    try:
        await run(lambda: list(col("stats").limit(1).stream()))
        return True
    except Exception:
        _log_fail("ping")
        return False


# ---------------------------------------------------------------- ko'rishlar va yarim qolgan arizalar (follow-up)

def _stage_hours(sent: int) -> int:
    return config.FOLLOWUP_FIRST_HOURS if sent == 0 else config.FOLLOWUP_SECOND_HOURS


def _next_at(hours: int) -> datetime:
    return adjust_quiet(now_utc() + timedelta(hours=hours), config.QUIET_START_HOUR, config.QUIET_END_HOUR)


async def record_view(tg_id: int, gym_id: str, lang: str) -> None:
    """Foydalanuvchi zal kartasini ko'rdi: jim ketsa, follow-up uchun navbatga qo'yiladi."""
    if not config.FOLLOWUP_ENABLED:
        return
    doc_id = f"{tg_id}_{safe_key(gym_id)}"
    try:
        snap = await run(lambda: col("views").document(doc_id).get())
        old = snap.to_dict() if snap.exists else {}
        if old.get("converted"):
            return
        sent = int(old.get("fu_sent") or 0)
        fields = {"tg_id": tg_id, "gym_id": gym_id, "lang": lang, "last_view_at": now_utc()}
        fields["next_fu_at"] = _next_at(_stage_hours(sent)) if sent < config.FOLLOWUP_MAX else None
        await run(lambda: col("views").document(doc_id).set(fields, merge=True))
    except Exception:
        _log_fail("record_view")


async def start_abandon(tg_id: int, gym_id: str, lead_type: str, lang: str) -> None:
    if not config.FOLLOWUP_ENABLED:
        return
    try:
        fields = {
            "tg_id": tg_id,
            "gym_id": gym_id,
            "type": lead_type,
            "lang": lang,
            "started_at": now_utc(),
            "fu_sent": 0,
            "next_fu_at": _next_at(config.FOLLOWUP_ABANDON_HOURS),
        }
        await run(lambda: col("abandon").document(str(tg_id)).set(fields))
    except Exception:
        _log_fail("start_abandon")


async def clear_abandon(tg_id: int) -> None:
    try:
        await run(lambda: col("abandon").document(str(tg_id)).delete())
    except Exception:
        _log_fail("clear_abandon")


async def mark_converted(tg_id: int, gym_id: str) -> None:
    """Ariza qoldirildi: bu zal bo'yicha follow-up kerak emas."""
    doc_id = f"{tg_id}_{safe_key(gym_id)}"
    await patch("views", doc_id, tg_id=tg_id, gym_id=gym_id, converted=True, next_fu_at=None)
    await clear_abandon(tg_id)


async def followup_done(collection: str, doc_id: str, sent: int, hours_next: int | None) -> None:
    """Follow-up yuborildi: hisoblagich oshadi, keyingisi rejalashtiriladi (yoki to'xtaydi)."""
    nxt = _next_at(hours_next) if (hours_next is not None and sent < config.FOLLOWUP_MAX) else None
    await patch(collection, doc_id, fu_sent=sent, last_fu_at=now_utc(), next_fu_at=nxt)


async def set_user_last_followup(tg_id: int) -> None:
    await patch("users", str(tg_id), last_fu_at=now_utc())


# ---------------------------------------------------------------- javobsiz savollar

async def log_unanswered(tg_id: int, gym_id: str, question: str, lang: str) -> None:
    try:
        row = {
            "tg_id": tg_id,
            "gym_id": gym_id,
            "question": question[:500],
            "lang": lang,
            "created_at": now_utc(),
        }
        await run(lambda: col("unanswered").add(row))
        await bump("unanswered_total")
    except Exception:
        _log_fail("log_unanswered")


async def recent_unanswered(limit: int = 10) -> list[dict]:
    try:
        docs = await run(
            lambda: list(col("unanswered").order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit).stream())
        )
        return [d.to_dict() for d in docs]
    except Exception:
        _log_fail("recent_unanswered")
        return []


# ---------------------------------------------------------------- hisoblagichlar va statistika

async def bump(counter: str, gym_id: str | None = None, n: int = 1) -> None:
    """stats/global va stats/d_<kun> hujjatlarida hisoblagichni oshiradi."""
    day = now_local().strftime("%Y%m%d")

    def _do():
        data = {counter: Increment(n)}
        if gym_id:
            data[f"{counter}_by_gym"] = {safe_key(gym_id): Increment(n)}
        col("stats").document("global").set(data, merge=True)
        col("stats").document(f"d_{day}").set({counter: Increment(n)}, merge=True)

    try:
        await run(_do)
    except Exception:
        _log_fail("bump")


async def day_counters(day: str | None = None) -> dict:
    """stats/d_<YYYYMMDD> hisoblagichlari (standart: bugun)."""
    day = day or now_local().strftime("%Y%m%d")
    try:
        snap = await run(lambda: col("stats").document(f"d_{day}").get())
        return snap.to_dict() if snap.exists else {}
    except Exception:
        _log_fail("day_counters")
        return {}


async def _count(query) -> int:
    try:
        res = await run(lambda: query.count(alias="n").get())
        return int(res[0][0].value)
    except Exception:
        _log_fail("count")
        return -1


async def stats() -> dict:
    """/stats uchun: count() agregatsiyasi (butun hujjatlarni o'qimaydi) + hisoblagichlar."""
    start_today = now_local().replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = start_today - timedelta(days=6)
    leads = col("leads")
    jobs = {
        "users": _count(col("users")),
        "leads_total": _count(leads),
        "leads_today": _count(leads.where("created_at", ">=", start_today)),
        "leads_week": _count(leads.where("created_at", ">=", week_ago)),
        "sheet_pending": _count(leads.where("sheet_ok", "==", False)),
    }
    for s in STATUSES:
        jobs[f"st_{s}"] = _count(leads.where("status", "==", s))
    keys = list(jobs)
    values = await asyncio.gather(*jobs.values())
    out = dict(zip(keys, values))
    try:
        snap = await run(lambda: col("stats").document("global").get())
        out["counters"] = snap.to_dict() if snap.exists else {}
    except Exception:
        _log_fail("stats.global")
        out["counters"] = {}
    return out


# ---------------------------------------------------------------- tozalash

async def _delete_old(collection: str, field: str, cutoff, limit: int = 200) -> int:
    removed = 0
    try:
        docs = await run(lambda: list(col(collection).where(field, "<=", cutoff).limit(limit).stream()))
        for d in docs:
            await run(d.reference.delete)
            removed += 1
    except Exception:
        _log_fail(f"cleanup:{collection}")
    return removed


async def cleanup_old(days: int = 14) -> int:
    """Eski FSM holatlari va eskirgan yarim qolgan arizalar (abandon) hujjatlarini o'chiradi."""
    cutoff = now_utc() - timedelta(days=days)
    removed = await _delete_old("fsm", "updated_at", cutoff)
    removed += await _delete_old("abandon", "started_at", cutoff)
    return removed


__all__ = [
    "DELETE_FIELD", "STATUSES", "TZ", "init", "col", "new_id", "safe_key",
]
