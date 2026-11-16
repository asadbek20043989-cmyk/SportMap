"""Testlar uchun muhit: haqiqiy kalitlar kerak emas, internet ham kerak emas."""
import os
import sys
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("SHEET_ID", "test")
os.environ["SUPER_ADMIN_IDS"] = "1001"
os.environ["FOLLOWUP_ENABLED"] = "1"

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
