"""Sof (tashqi xizmatlarga bog'liq bo'lmagan) yordamchi funksiyalar."""
import html
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Tashkent")
UTC = timezone.utc

# Sinov vaqti oralig'i: (boshlanish soati, tugash soati)
SLOT_HOURS = {"morning": (6, 12), "day": (12, 17), "evening": (17, 23)}
# Eslatma (kun ichida, soat:daqiqa) va "keldingizmi?" so'rovi (kun_siljishi, soat:daqiqa)
SLOT_REMIND_AT = {"morning": (7, 0), "day": (11, 0), "evening": (16, 0)}
SLOT_CONFIRM_AT = {"morning": (0, 13, 0), "day": (0, 18, 30), "evening": (1, 9, 0)}


def esc(value) -> str:
    return html.escape(str(value or ""), quote=False)


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_local() -> datetime:
    return datetime.now(TZ)


# ------------------------------------------------------------------ telefon

def norm_phone(raw: str, verified: bool = False):
    """Telefonni +998XXXXXXXXX ko'rinishiga keltiradi.

    Matn ko'rinishida kiritilgan raqam faqat O'zbekiston raqami (12 xona) bo'lishi
    kerak. Telegram tugmasi orqali yuborilgan o'z kontakti (verified=True) uchun
    xorijiy raqamlar ham qabul qilinadi.
    """
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 9:
        digits = "998" + digits
    if len(digits) == 12 and digits.startswith("998"):
        return "+" + digits
    if verified and 10 <= len(digits) <= 15:
        return "+" + digits
    return None


# ------------------------------------------------------------------ narx / masofa

_MONTH_RE = re.compile(
    r"(?:\b1\s*(?:oy|month|мес\w*)|oylik|monthly|месяц\w*)[^\d;\n]{0,14}(\d[\d\s\u00a0.,]*\d|\d)",
    re.IGNORECASE,
)


def parse_month_price(text: str):
    """'1 oy: 350 000 so'm; 3 oy: ...' ichidan oylik narxni (so'm) ajratadi."""
    m = _MONTH_RE.search(text or "")
    if not m:
        return None
    value = int(re.sub(r"\D", "", m.group(1)) or 0)
    return value if 20_000 <= value <= 20_000_000 else None


def fmt_money(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fmt_km(km: float) -> str:
    return f"{km:.1f}".rstrip("0").rstrip(".") if km >= 1 else f"{km:.1f}"


def maps_urls(lat, lon):
    return (
        f"https://www.google.com/maps?q={lat},{lon}",
        f"https://yandex.com/maps/?pt={lon},{lat}&z=16&l=map",
    )


# ------------------------------------------------------------------ vaqt / jadval

def adjust_quiet(dt: datetime, quiet_start: int, quiet_end: int) -> datetime:
    """Tunda xabar yuborilmasligi uchun vaqtni keyingi 'ruxsat etilgan' vaqtga suradi."""
    local = dt.astimezone(TZ)
    if quiet_end <= local.hour < quiet_start:
        return dt
    if local.hour >= quiet_start:
        local = (local + timedelta(days=1)).replace(hour=quiet_end, minute=0, second=0, microsecond=0)
    else:
        local = local.replace(hour=quiet_end, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC)


def available_slots(day: date, now: datetime | None = None) -> list[str]:
    """Berilgan kun uchun hali tugamagan vaqt oraliqlari."""
    now = (now or now_local()).astimezone(TZ)
    if day < now.date():
        return []
    if day > now.date():
        return list(SLOT_HOURS)
    return [k for k, (_, end) in SLOT_HOURS.items() if now.hour < end - 1]


def upcoming_days(n: int = 4, now: datetime | None = None) -> list[date]:
    now = (now or now_local()).astimezone(TZ)
    days = []
    for i in range(n + 1):
        d = now.date() + timedelta(days=i)
        if available_slots(d, now):
            days.append(d)
        if len(days) == n:
            break
    return days


def visit_schedule(day: date, slot: str, now: datetime | None = None):
    """(eslatma vaqti | None, 'keldingizmi?' vaqti) - ikkalasi ham UTC."""
    now = (now or now_utc()).astimezone(UTC)
    rh, rm = SLOT_REMIND_AT[slot]
    remind = datetime.combine(day, time(rh, rm), tzinfo=TZ).astimezone(UTC)
    if remind <= now:
        remind = None
    shift, ch, cm = SLOT_CONFIRM_AT[slot]
    confirm = datetime.combine(day + timedelta(days=shift), time(ch, cm), tzinfo=TZ).astimezone(UTC)
    if confirm <= now:
        confirm = now + timedelta(hours=1)
    return remind, confirm


def parse_day(value: str):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
