import csv
from pathlib import Path
from unittest.mock import patch

import sheets

SAMPLE = Path(__file__).resolve().parent.parent / "gyms_sample.csv"


def row(**over):
    base = {
        "id": "g1", "name": "Power Gym", "district": "Chilonzor", "address": "Chilonzor 9", "lat": "41.27",
        "lon": "69.2", "phone": "+998901000001", "tg_username": "@powergym", "owner_tg_id": "2002",
        "hours": "06-23", "prices": "1 oy: 350 000 so'm", "price_month": "", "amenities": "kardio",
        "photos": "a.jpg | b.jpg", "free_trial": "ha", "notes": "", "active": "ha",
    }
    base.update(over)
    return base


def test_id_error():
    assert sheets.id_error("chil-power") is None
    assert sheets.id_error("a:b") is not None
    assert sheets.id_error("x" * sheets.MAX_GYM_ID_BYTES) is None
    assert sheets.id_error("x" * (sheets.MAX_GYM_ID_BYTES + 1)) is not None
    # bayt hisoblanadi, belgi emas: kirill harflar 2 baytdan
    assert sheets.id_error("з" * 29) is not None


def test_normalize():
    g = sheets._normalize(row())
    assert g["district_key"] == "chilonzor" and g["district_ok"]
    assert g["tg_username"] == "powergym"
    assert g["owner_tg_id"] == 2002
    assert g["price_month"] == 350000            # 'prices' matnidan ajratildi
    assert g["photos"] == ["a.jpg", "b.jpg"]
    assert g["free_trial"] is True and g["active"] is True
    assert sheets._normalize(row(price_month="420 000"))["price_month"] == 420000
    assert sheets._normalize(row(id="")) is None
    assert sheets._normalize(row(active="yo'q"))["active"] is False


def test_district_aliases():
    assert sheets.district_key("Чиланзар") == "chilonzor"
    assert sheets.district_key("Mirzo Ulug'bek tumani") == "mirzoulugbek"
    assert sheets.district_key("Yunusabad") == "yunusobod"


def test_validate_reports_problems():
    gyms = [
        sheets._normalize(row(id="a")),
        sheets._normalize(row(id="a", name="Dublikat")),
        sheets._normalize(row(id="b:c", name="Ikki nuqta")),
        sheets._normalize(row(id="d", district="Noma'lum tuman")),
        sheets._normalize(row(id="e", lat="", lon="")),
        sheets._normalize(row(id="f", prices="so'rang")),
    ]
    text = "\n".join(sheets.validate(gyms))
    assert "Takroriy id: a" in text
    assert "':'" in text
    assert "tuman tanilmadi" in text
    assert "lokatsiya yo'q" in text
    assert "price_month" in text


async def test_load_hides_gyms_with_bad_ids():
    rows = [row(id="ok-1"), row(id="bad:id", name="Yomon"), row(id="y" * 80, name="Uzun"), row(id="off", active="yo'q")]
    with patch.object(sheets, "_read_rows", lambda: rows):
        await sheets._load()
    assert [g["id"] for g in sheets._cache["gyms"]] == ["ok-1"]
    problems = "\n".join(sheets.problems())
    assert "bad:id" in problems and "Uzun" in problems
    assert sheets.loaded_at() > 0


def test_sample_csv_is_valid():
    with open(SAMPLE, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) >= 5
    gyms = [g for g in (sheets._normalize(r) for r in rows) if g]
    active = [g for g in gyms if g["active"]]
    assert 0 < len(active) < len(gyms)           # kamida bitta nofaol zal namunada bor
    assert sheets.validate(active) == []         # namuna jadvalda hech qanday muammo bo'lmasligi kerak
    assert all(g["price_month"] for g in active)
    assert len({g["district_key"] for g in active}) >= 4
