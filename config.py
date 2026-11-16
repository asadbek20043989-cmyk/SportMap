import json
import os

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"'{name}' to'ldirilmagan. .env faylini (yoki Railway Variables) tekshiring.")
    return value


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "ha", "yes", "on"}


BOT_TOKEN = _required("BOT_TOKEN")
GEMINI_API_KEY = _required("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
SHEET_ID = _required("SHEET_ID")

SUPER_ADMIN_IDS = {
    int(x) for x in os.getenv("SUPER_ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()
}
NOTIFY_SUPER_ADMINS = _flag("NOTIFY_SUPER_ADMINS", True)

SHEET_CACHE_SECONDS = _int("SHEET_CACHE_SECONDS", 60)
AI_LIMIT_PER_HOUR = _int("AI_LIMIT_PER_HOUR", 20)

# --- Leadlar: takroriy va soxta arizalardan himoya
LEAD_DEDUPE_HOURS = _int("LEAD_DEDUPE_HOURS", 24)      # bir zalga bir turdagi ariza oralig'i
LEAD_LIMIT_PER_DAY = _int("LEAD_LIMIT_PER_DAY", 5)     # bir foydalanuvchi kuniga nechta ariza
LEAD_SLA_HOURS = _int("LEAD_SLA_HOURS", 2)             # lead "yangi" holatda qolsa, egasiga eslatma

# --- Ro'yxat
PAGE_SIZE = _int("PAGE_SIZE", 5)

# --- Avtomatik follow-up
FOLLOWUP_ENABLED = _flag("FOLLOWUP_ENABLED", True)
FOLLOWUP_FIRST_HOURS = _int("FOLLOWUP_FIRST_HOURS", 24)    # zalni ko'rib, jim ketganlarga 1-eslatma
FOLLOWUP_SECOND_HOURS = _int("FOLLOWUP_SECOND_HOURS", 72)  # 1-eslatmadan keyin 2-eslatma
FOLLOWUP_ABANDON_HOURS = _int("FOLLOWUP_ABANDON_HOURS", 2) # ariza yarim qolsa
FOLLOWUP_MAX = _int("FOLLOWUP_MAX", 2)                     # bir zal bo'yicha eng ko'pi bilan
FOLLOWUP_MIN_GAP_HOURS = _int("FOLLOWUP_MIN_GAP_HOURS", 20)  # bir odamga ketma-ket eslatma oralig'i
QUIET_START_HOUR = _int("QUIET_START_HOUR", 21)            # shu soatdan keyin xabar yuborilmaydi
QUIET_END_HOUR = _int("QUIET_END_HOUR", 9)                 # shu soatgacha xabar yuborilmaydi
SCHEDULER_INTERVAL_SECONDS = _int("SCHEDULER_INTERVAL_SECONDS", 120)


def google_credentials() -> dict:
    """Service account ma'lumotlari: JSON matn (Railway) yoki fayl (kompyuter)."""
    raw = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
    if raw:
        return json.loads(raw)
    path = os.getenv("GOOGLE_CREDENTIALS_FILE", "service-account.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)
