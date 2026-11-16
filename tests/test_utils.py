from datetime import date, datetime, timedelta

from utils import (
    TZ, UTC, adjust_quiet, available_slots, fmt_money, haversine_km, norm_phone, parse_day,
    parse_month_price, upcoming_days, visit_schedule,
)


def local(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=TZ)


def test_norm_phone():
    assert norm_phone("+998 90 123 45 67") == "+998901234567"
    assert norm_phone("90 123 45 67") == "+998901234567"
    assert norm_phone("0 0 998901234567".replace(" ", "")) == "+998901234567"
    assert norm_phone("12345") is None
    assert norm_phone("+1 415 555 2671") is None            # matn bilan faqat O'zbekiston raqami
    assert norm_phone("+1 415 555 2671", verified=True) == "+14155552671"  # o'z kontakti bo'lsa xorijiy ham mumkin


def test_parse_month_price():
    assert parse_month_price("1 oy: 350 000 so'm; 3 oy: 900 000 so'm") == 350000
    assert parse_month_price("Oylik: 280000") == 280000
    assert parse_month_price("Narxlar so'rang") is None
    assert parse_month_price("1 oy: 50") is None             # 20 000 dan kam: ishonchsiz


def test_fmt_money():
    assert fmt_money(1250000) == "1 250 000"


def test_haversine():
    assert haversine_km(41.3, 69.2, 41.3, 69.2) == 0
    assert 1.0 < haversine_km(41.30, 69.20, 41.31, 69.21) < 2.0


def test_adjust_quiet():
    # 22:00 -> ertasi kuni 09:00, 03:00 -> o'sha kuni 09:00, 12:00 o'zgarmaydi
    assert adjust_quiet(local(2026, 11, 16, 22), 21, 9).astimezone(TZ) == local(2026, 11, 17, 9)
    assert adjust_quiet(local(2026, 11, 16, 3), 21, 9).astimezone(TZ) == local(2026, 11, 16, 9)
    noon = local(2026, 11, 16, 12)
    assert adjust_quiet(noon, 21, 9) == noon
    assert adjust_quiet(local(2026, 11, 16, 21), 21, 9).astimezone(TZ) == local(2026, 11, 17, 9)
    assert adjust_quiet(local(2026, 11, 16, 9), 21, 9) == local(2026, 11, 16, 9)


def test_available_slots():
    now = local(2026, 11, 16, 20, 15)
    assert available_slots(date(2026, 11, 16), now) == ["evening"]
    assert available_slots(date(2026, 11, 16), local(2026, 11, 16, 22, 30)) == []
    assert available_slots(date(2026, 11, 17), now) == ["morning", "day", "evening"]
    assert available_slots(date(2026, 11, 15), now) == []
    assert available_slots(date(2026, 11, 16), local(2026, 11, 16, 8)) == ["morning", "day", "evening"]


def test_upcoming_days_skips_today_when_over():
    days = upcoming_days(4, local(2026, 11, 16, 23, 0))
    assert days[0] == date(2026, 11, 17) and len(days) == 4


def test_visit_schedule():
    now = local(2026, 11, 16, 10).astimezone(UTC)
    remind, confirm = visit_schedule(date(2026, 11, 17), "morning", now)
    assert remind.astimezone(TZ) == local(2026, 11, 17, 7)
    assert confirm.astimezone(TZ) == local(2026, 11, 17, 13)
    _, confirm = visit_schedule(date(2026, 11, 17), "evening", now)
    assert confirm.astimezone(TZ) == local(2026, 11, 18, 9)   # kechki sinovdan keyin ertasi kuni ertalab
    # bugungi ertalabki sinov, hozir soat 10:00: eslatma vaqti o'tib ketgan -> None
    remind, confirm = visit_schedule(date(2026, 11, 16), "morning", now)
    assert remind is None
    assert confirm.astimezone(TZ) == local(2026, 11, 16, 13)
    # tasdiqlash vaqti ham o'tib ketgan bo'lsa, 1 soatdan keyin
    late = local(2026, 11, 16, 20).astimezone(UTC)
    _, confirm = visit_schedule(date(2026, 11, 16), "day", late)
    assert confirm == late + timedelta(hours=1)


def test_parse_day():
    assert parse_day("2026-11-16") == date(2026, 11, 16)
    assert parse_day("xato") is None
    assert parse_day(None) is None
