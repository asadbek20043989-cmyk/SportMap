import string

import db
from sheets import MAX_GYM_ID_BYTES
from texts import STATUS_LABELS, TEXTS, t
from common import CODE_STATUS, STATUS_CODES


def fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_all_languages_have_same_keys():
    uz = set(TEXTS["uz"])
    for lang in ("ru", "en"):
        assert set(TEXTS[lang]) == uz, lang


def test_placeholders_match_across_languages():
    for key, text in TEXTS["uz"].items():
        for lang in ("ru", "en"):
            assert fields(TEXTS[lang][key]) == fields(text), f"{lang}:{key}"


def test_followup_texts_format():
    for lang in TEXTS:
        t(lang, "fu_viewed_1", name="Ali", gym="Gym", trial="")
        t(lang, "fu_viewed_2", name="Ali", gym="Gym", trial="")
        t(lang, "fu_abandon_1", name="Ali", gym="Gym")
        t(lang, "fu_abandon_2", name="Ali", gym="Gym")
        t(lang, "remind_visit", gym="G", slot="s", address="a")
        t(lang, "confirm_visit", name="Ali", gym="G")


def test_status_codes_roundtrip():
    assert set(STATUS_CODES) == set(db.STATUSES) == set(STATUS_LABELS)
    assert all(CODE_STATUS[code] == status for status, code in STATUS_CODES.items())
    assert len(set(STATUS_CODES.values())) == len(STATUS_CODES)


def test_callback_data_fits_64_bytes():
    gid = "x" * MAX_GYM_ID_BYTES
    lead_id = db.new_id()
    patterns = [f"trial:{gid}:b", f"join:{gid}:f", f"g:{gid}:b", f"ask:{gid}", f"st:{lead_id}:o", f"v:y:{lead_id}"]
    assert all(len(p.encode()) <= 64 for p in patterns)
    assert len(f"trial:{'x' * (MAX_GYM_ID_BYTES + 1)}:b".encode()) > 64   # chegara aniq
