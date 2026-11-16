"""Google Sheets: zallar ma'lumotini o'qish (kesh bilan) va leadlarni yozish."""
import asyncio
import difflib
import logging
import re
import time
from datetime import datetime

import gspread

import config
from texts import DISTRICT_ALIASES, DISTRICT_NAMES, norm_name
from utils import TZ, parse_month_price

log = logging.getLogger("sheets")

GYMS_SHEET = "Zallar"
LEADS_SHEET = "Leads"
LEAD_HEADERS = [
    "date", "gym_id", "gym", "name", "phone", "phone_verified", "goal", "type",
    "visit", "lang", "source", "tg_id", "tg_username", "lead_id",
]
_FALSE = {"yo'q", "yoq", "yo`q", "no", "0", "false", "нет", "n"}
_TRUE = {"ha", "yes", "1", "true", "да", "y"}

_sh = None
_leads_ws = None
_cache = {"ts": 0.0, "gyms": [], "problems": [], "loaded": False}
_lock = asyncio.Lock()
_refreshing = False


def _spreadsheet():
    global _sh
    if _sh is None:
        client = gspread.service_account_from_dict(config.google_credentials())
        _sh = client.open_by_key(config.SHEET_ID)
    return _sh


def district_key(value: str) -> str:
    """'Mirzo Ulug'bek tumani' / 'Чиланзар' / 'Chilanzar' -> kanonik kalit.

    Tanilmagan nom bo'lsa, eng yaqin kalit (xato yozilgan holat) tanlanadi.
    Hech narsaga o'xshamasa, normallashtirilgan nom qaytadi (zal tuman ro'yxatida chiqmaydi).
    """
    n = norm_name(value)
    if n in DISTRICT_ALIASES:
        return DISTRICT_ALIASES[n]
    close = difflib.get_close_matches(n, list(DISTRICT_ALIASES), n=1, cutoff=0.8)
    return DISTRICT_ALIASES[close[0]] if close else n


def _to_float(value):
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _to_int(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) if digits else None


def _normalize(row: dict):
    r = {str(k).strip().lower(): str(v).strip() for k, v in row.items()}
    gym_id = r.get("id", "")
    if not gym_id or not r.get("name"):
        return None
    active_raw = r.get("active", "").lower()
    active = active_raw not in _FALSE
    key = district_key(r.get("district", ""))
    price_month = _to_int(r.get("price_month")) or parse_month_price(r.get("prices", ""))
    return {
        "id": gym_id,
        "name": r.get("name", ""),
        "district": r.get("district", ""),
        "district_key": key,
        "district_ok": key in DISTRICT_NAMES,
        "address": r.get("address", ""),
        "lat": _to_float(r.get("lat")),
        "lon": _to_float(r.get("lon")),
        "phone": r.get("phone", ""),
        "tg_username": r.get("tg_username", "").lstrip("@"),
        "owner_tg_id": _to_int(r.get("owner_tg_id")),
        "hours": r.get("hours", ""),
        "prices": r.get("prices", ""),
        "price_month": price_month,
        "amenities": r.get("amenities", ""),
        "photos": [p.strip() for p in re.split(r"[|\n]+", r.get("photos", "")) if p.strip()],
        "free_trial": r.get("free_trial", "").lower() in _TRUE,
        "notes": r.get("notes", ""),
        "active": active,
    }


# callback_data 64 bayt bilan cheklangan. Eng uzun naqsh: "trial:<id>:b" -> id uchun 64 - 8 = 56 bayt.
MAX_GYM_ID_BYTES = 56


def id_error(gym_id: str) -> str | None:
    """Zal id'si tugma (callback_data) uchun yaroqli emasmi? Yaroqli bo'lsa None."""
    if ":" in gym_id:
        return "id ichida ':' belgisi bo'lmasligi kerak"
    size = len(gym_id.encode("utf-8"))
    if size > MAX_GYM_ID_BYTES:
        return f"id juda uzun ({size} bayt, eng ko'pi {MAX_GYM_ID_BYTES})"
    return None


def validate(gyms: list[dict]) -> list[str]:
    """Jadvaldagi xatolarni topadi (admin /gyms va /reload da ko'radi)."""
    problems = []
    seen = {}
    for g in gyms:
        if g["id"] in seen:
            problems.append(f"Takroriy id: {g['id']}")
        seen[g["id"]] = True
        err = id_error(g["id"])
        if err:
            problems.append(f"{g['id'][:30]} ({g['name']}): {err} — zal botda ko'rsatilmaydi")
        if not g["district_ok"]:
            problems.append(f"{g['id']} ({g['name']}): tuman tanilmadi — '{g['district']}'. Zal tumanlar ro'yxatida chiqmaydi")
        elif norm_name(g["district"]) != g["district_key"] and norm_name(g["district"]) not in DISTRICT_ALIASES:
            problems.append(f"{g['id']} ({g['name']}): tuman '{g['district']}' deb o'qildi → {DISTRICT_NAMES[g['district_key']]['uz']}. Jadvalda to'g'rilang")
        if g["lat"] is None or g["lon"] is None:
            problems.append(f"{g['id']} ({g['name']}): lokatsiya yo'q (yaqin zallar ro'yxatida chiqmaydi)")
        if g["price_month"] is None:
            problems.append(f"{g['id']} ({g['name']}): oylik narx aniqlanmadi — 'price_month' ustunini to'ldiring (tavsiya uchun kerak)")
    return problems


def _read_rows() -> list[dict]:
    ws = _spreadsheet().worksheet(GYMS_SHEET)
    return ws.get_all_records(numericise_ignore=["all"])


async def _load():
    rows = await asyncio.to_thread(_read_rows)
    parsed = [g for g in (_normalize(r) for r in rows) if g]
    active = [g for g in parsed if g["active"]]
    # id'si tugmaga sig'maydigan zal chiqarib tashlanadi: aks holda bitta yaroqsiz tugma
    # butun tuman ro'yxati xabarini Telegram'da rad ettiradi (BUTTON_DATA_INVALID).
    _cache["gyms"] = [g for g in active if not id_error(g["id"])]
    _cache["problems"] = validate(active)
    _cache["ts"] = time.time()
    _cache["loaded"] = True


async def _background_refresh():
    global _refreshing
    try:
        await _load()
    except Exception:
        log.exception("Zallar jadvalini fonda yangilab bo'lmadi")
    finally:
        _refreshing = False


async def get_gyms(force: bool = False) -> list[dict]:
    """Keshdagi zallar. Kesh eskirsa: bo'sh bo'lsa kutamiz, bo'lmasa eski ro'yxatni
    darhol qaytarib, yangilashni fonda bajaramiz (hech kim jadval kutib qolmaydi)."""
    global _refreshing
    fresh = (time.time() - _cache["ts"]) < config.SHEET_CACHE_SECONDS
    if fresh and not force:
        return _cache["gyms"]
    if _cache["loaded"] and not force:
        if not _refreshing:
            _refreshing = True
            asyncio.create_task(_background_refresh())
        return _cache["gyms"]
    async with _lock:
        if not force and (time.time() - _cache["ts"]) < config.SHEET_CACHE_SECONDS and _cache["loaded"]:
            return _cache["gyms"]
        try:
            await _load()
        except Exception:
            log.exception("Zallar jadvalini o'qib bo'lmadi")
        return _cache["gyms"]


def problems() -> list[str]:
    return list(_cache["problems"])


def loaded_at() -> float:
    """Jadval oxirgi marta muvaffaqiyatli o'qilgan vaqt (time.time). /reload natijani shu bilan tekshiradi."""
    return _cache["ts"]


async def get_gym(gym_id: str):
    for g in await get_gyms():
        if g["id"] == gym_id:
            return g
    return None


def _get_leads_ws():
    """Leads varag'ini topadi/yaratadi. Sarlavhalar eski bo'lsa, eski varaq
    'Leads_old_<sana>' deb qayta nomlanadi (ma'lumot yo'qolmaydi)."""
    global _leads_ws
    if _leads_ws is not None:
        return _leads_ws
    sh = _spreadsheet()
    try:
        ws = sh.worksheet(LEADS_SHEET)
        header = ws.row_values(1)
        if header != LEAD_HEADERS:
            stamp = datetime.now(TZ).strftime("%Y%m%d_%H%M")
            ws.update_title(f"Leads_old_{stamp}")
            ws = sh.add_worksheet(LEADS_SHEET, rows=1000, cols=len(LEAD_HEADERS))
            ws.append_row(LEAD_HEADERS)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(LEADS_SHEET, rows=1000, cols=len(LEAD_HEADERS))
        ws.append_row(LEAD_HEADERS)
    _leads_ws = ws
    return ws


def _append_lead(row: list):
    global _leads_ws
    try:
        _get_leads_ws().append_row(row, value_input_option="RAW")
    except Exception:
        _leads_ws = None  # varaq o'chirilgan/yangilangan bo'lishi mumkin: bir marta qayta urinamiz
        _get_leads_ws().append_row(row, value_input_option="RAW")


async def append_lead(row: list):
    await asyncio.to_thread(_append_lead, row)
