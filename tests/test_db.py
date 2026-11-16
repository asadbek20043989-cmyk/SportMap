import db
from fakes import ago, ahead, fresh_db, sample_lead


async def test_due_returns_only_past_datetimes():
    fs = fresh_db()
    fs.data("leads").update({
        "a": {"lead_id": "a", "sla_at": ago(minutes=5)},
        "b": {"lead_id": "b", "sla_at": ahead(hours=1)},
        "c": {"lead_id": "c", "sla_at": None},      # bajarilgan
        "d": {"lead_id": "d"},                      # maydon yo'q
    })
    assert [x["_id"] for x in await db.due("leads", "sla_at")] == ["a"]


async def test_patch_and_delete_doc():
    fs = fresh_db()
    assert await db.patch("views", "v1", a=1)
    assert await db.patch("views", "v1", b=2)
    assert fs.data("views")["v1"] == {"a": 1, "b": 2}
    assert await db.delete_doc("views", "v1")
    assert "v1" not in fs.data("views")


async def test_update_lead_missing_returns_false():
    fresh_db()
    assert await db.update_lead("yoq", status="won") is False


async def test_ping():
    fresh_db()
    assert await db.ping() is True


async def test_bump_and_day_counters():
    fs = fresh_db()
    await db.bump("leads_total", "chil-power")
    await db.bump("leads_total", "chil-power")
    await db.bump("asks_total")
    assert fs.data("stats")["global"]["leads_total"] == 2
    assert fs.data("stats")["global"]["leads_total_by_gym"] == {"chil_power": 2}
    today = await db.day_counters()
    assert today["leads_total"] == 2 and today["asks_total"] == 1
    assert await db.day_counters("19990101") == {}


async def test_find_recent_lead_and_dedupe_window():
    fs = fresh_db()
    fs.data("leads")["x"] = sample_lead(lead_id="x", created_at=ago(hours=2))
    fs.data("leads")["old"] = sample_lead(lead_id="old", created_at=ago(hours=30), type="join")
    assert (await db.find_recent_lead(5005, "g1", "trial"))["lead_id"] == "x"
    assert await db.find_recent_lead(5005, "g1", "join") is None       # 24 soatdan eski
    assert await db.find_recent_lead(9999, "g1", "trial") is None


async def test_stats_counts():
    fs = fresh_db()
    fs.data("users").update({"1": {}, "2": {}})
    fs.data("leads")["a"] = sample_lead(lead_id="a", status="new", created_at=ago(hours=1))
    fs.data("leads")["b"] = sample_lead(lead_id="b", status="won", created_at=ago(days=3), sheet_ok=False)
    fs.data("leads")["c"] = sample_lead(lead_id="c", status="won", created_at=ago(days=30))
    s = await db.stats()
    assert s["users"] == 2 and s["leads_total"] == 3
    assert s["leads_today"] == 1 and s["leads_week"] == 2
    assert s["st_won"] == 2 and s["st_new"] == 1 and s["st_lost"] == 0
    assert s["sheet_pending"] == 1


async def test_cleanup_old_removes_stale_fsm_and_abandon():
    fs = fresh_db()
    fs.data("fsm").update({"old": {"updated_at": ago(days=20)}, "new": {"updated_at": ago(days=1)}})
    fs.data("abandon").update({"old": {"started_at": ago(days=20)}, "new": {"started_at": ago(hours=3)}})
    assert await db.cleanup_old(14) == 2
    assert set(fs.data("fsm")) == {"new"} and set(fs.data("abandon")) == {"new"}


async def test_followup_done_schedules_or_stops():
    fs = fresh_db()
    fs.data("views")["v"] = {"fu_sent": 0}
    await db.followup_done("views", "v", 1, 72)
    assert fs.data("views")["v"]["fu_sent"] == 1 and fs.data("views")["v"]["next_fu_at"] > ahead(hours=60)
    await db.followup_done("views", "v", 2, 72)            # FOLLOWUP_MAX = 2: boshqa yuborilmaydi
    assert fs.data("views")["v"]["next_fu_at"] is None


async def test_legacy_v1_leads_are_usable():
    """v1 arizalarida lead_id/gym/status yo'q: kartalar va CRM tugmalari baribir ishlashi kerak."""
    import common

    fs = fresh_db()
    fs.data("leads")["OLDDOC123"] = {
        "gym_id": "g001", "gym_name": "Power House", "name": "Ali", "phone": "+998901112233",
        "goal": "Ozish", "type": "join", "lang": "uz", "source": "direct", "tg_id": 5, "tg_username": "",
        "created_at": ago(days=40),
    }
    [lead] = await db.recent_leads(5)
    assert lead["lead_id"] == "OLDDOC123" and lead["gym"] == "Power House"
    assert "Power House" in common.lead_card_uz(lead)
    assert common.crm_kb(lead).inline_keyboard[0][0].callback_data == "st:OLDDOC123:c"
    assert (await db.get_lead("OLDDOC123"))["gym"] == "Power House"
    assert await db.update_lead("OLDDOC123", status="contacted")
