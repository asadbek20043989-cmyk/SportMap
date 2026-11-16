from datetime import timedelta
from unittest.mock import patch

import db
import followup
import sheets
from fakes import FakeBot, ago, ahead, buttons, fresh_db, sample_gym, sample_lead, set_gyms
from utils import TZ, now_utc

USER = 5005


def setup(user=None, gyms=None):
    fs = fresh_db()
    set_gyms(gyms if gyms is not None else [sample_gym()])
    fs.data("users")[str(USER)] = {"name": "Ali", "lang": "uz", **(user or {})}
    return fs, FakeBot()


def view(**over):
    doc = {"tg_id": USER, "gym_id": "g1", "lang": "uz", "last_view_at": ago(days=1), "next_fu_at": ago(minutes=1), "fu_sent": 0}
    doc.update(over)
    return doc


def abandon(**over):
    doc = {"tg_id": USER, "gym_id": "g1", "type": "join", "lang": "uz", "started_at": ago(hours=3), "fu_sent": 0, "next_fu_at": ago(minutes=1)}
    doc.update(over)
    return doc


def awake():
    """Tinch soat tekshiruvini o'chiradi: test qaysi soatda ishlashidan qat'i nazar bir xil natija bersin."""
    return patch.object(followup, "in_quiet_hours", lambda now=None: False)


def asleep():
    return patch.object(followup, "in_quiet_hours", lambda now=None: True)


# ------------------------------------------------------------------ sof yordamchilar

def test_in_quiet_hours_boundaries():
    def at(h):
        return now_utc().astimezone(TZ).replace(hour=h, minute=0)

    assert followup.in_quiet_hours(at(21)) and followup.in_quiet_hours(at(3)) and followup.in_quiet_hours(at(8))
    assert not followup.in_quiet_hours(at(9)) and not followup.in_quiet_hours(at(20))


def test_gap_and_opt_out_helpers():
    assert followup.gap_until({}, now_utc()) is None
    assert followup.gap_until({"last_fu_at": ago(hours=1)}, now_utc()) is not None
    assert followup.gap_until({"last_fu_at": ago(hours=48)}, now_utc()) is None
    assert followup.opted_out({"followup_off": True}) and followup.opted_out({"blocked": True})
    assert not followup.opted_out({}) and not followup.opted_out(None)
    assert followup.display_name({"name": "  Ali   <b> "}, "uz") == "Ali &lt;b&gt;"
    assert followup.display_name({}, "ru") == "друг"


# ------------------------------------------------------------------ views

async def test_first_followup_sent_and_rescheduled():
    fs, bot = setup()
    fs.data("views")[f"{USER}_g1"] = view()
    with awake():
        await followup.process_followups(bot, "views")
    [msg] = bot.to(USER)
    assert "Ali" in msg["text"] and "Power Gym" in msg["text"] and "Bepul sinov" in msg["text"]
    assert buttons(msg["markup"]) == ["join:g1:f", "ask:g1", "fu:stop"]
    doc = fs.data("views")[f"{USER}_g1"]
    assert doc["fu_sent"] == 1 and doc["next_fu_at"] > ahead(hours=60)
    assert fs.data("users")[str(USER)]["last_fu_at"] is not None
    assert fs.data("stats")["global"]["followups_sent"] == 1


async def test_second_followup_is_last():
    fs, bot = setup()
    fs.data("views")[f"{USER}_g1"] = view(fu_sent=1)
    with awake():
        await followup.process_followups(bot, "views")
    assert len(bot.to(USER)) == 1 and "kutmoqda" in bot.to(USER)[0]["text"]
    doc = fs.data("views")[f"{USER}_g1"]
    assert doc["fu_sent"] == 2 and doc["next_fu_at"] is None


async def test_no_double_send_on_next_tick():
    fs, bot = setup()
    fs.data("views")[f"{USER}_g1"] = view()
    with awake():
        await followup.process_followups(bot, "views")
        await followup.process_followups(bot, "views")
    assert len(bot.to(USER)) == 1


async def test_skips_opted_out_blocked_converted():
    for user, over in (({"followup_off": True}, {}), ({"blocked": True}, {}), ({}, {"converted": True})):
        fs, bot = setup(user)
        fs.data("views")[f"{USER}_g1"] = view(**over)
        with awake():
            await followup.process_followups(bot, "views")
        assert bot.sent == [] and fs.data("views")[f"{USER}_g1"]["next_fu_at"] is None


async def test_min_gap_postpones():
    fs, bot = setup({"last_fu_at": ago(hours=1)})
    fs.data("views")[f"{USER}_g1"] = view()
    with awake():
        await followup.process_followups(bot, "views")
    doc = fs.data("views")[f"{USER}_g1"]
    assert bot.sent == [] and doc["fu_sent"] == 0 and doc["next_fu_at"] > ahead(hours=15)


async def test_quiet_hours_leave_queue_untouched():
    fs, bot = setup()
    fs.data("views")[f"{USER}_g1"] = view()
    with asleep():
        await followup.process_followups(bot, "views")
    assert bot.sent == [] and fs.data("views")[f"{USER}_g1"]["next_fu_at"] is not None
    with awake():                         # tinch soat tugagach yuboriladi
        await followup.process_followups(bot, "views")
    assert len(bot.sent) == 1


async def test_removed_gym_dropped_but_empty_sheet_cache_keeps_queue():
    fs, bot = setup(gyms=[sample_gym(id="other")])
    fs.data("views")[f"{USER}_g1"] = view()
    with awake():
        await followup.process_followups(bot, "views")
    assert bot.sent == [] and fs.data("views")[f"{USER}_g1"]["next_fu_at"] is None

    fs, bot = setup(gyms=[])              # jadval o'qilmagan: navbatni tozalamaymiz
    fs.data("views")[f"{USER}_g1"] = view()
    with awake():
        await followup.process_followups(bot, "views")
    assert bot.sent == [] and fs.data("views")[f"{USER}_g1"]["next_fu_at"] is not None


async def test_blocked_user_gets_no_retry():
    fs, bot = setup()
    bot.blocked.add(USER)
    fs.data("views")[f"{USER}_g1"] = view()
    with awake():
        await followup.process_followups(bot, "views")
    assert fs.data("views")[f"{USER}_g1"]["next_fu_at"] is None and fs.data("users")[str(USER)]["blocked"] is True


async def test_send_errors_retry_then_give_up():
    fs, bot = setup()
    bot.failing.add(USER)
    fs.data("views")[f"{USER}_g1"] = view()
    with awake():
        await followup.process_followups(bot, "views")
    doc = fs.data("views")[f"{USER}_g1"]
    assert doc["fu_err"] == 1 and doc["next_fu_at"] is not None and doc["fu_sent"] == 0
    fs.data("views")[f"{USER}_g1"] = view(fu_err=2)
    with awake():
        await followup.process_followups(bot, "views")
    assert fs.data("views")[f"{USER}_g1"]["next_fu_at"] is None


async def test_russian_user_gets_russian_text():
    fs, bot = setup({"lang": "ru"})
    fs.data("views")[f"{USER}_g1"] = view(lang="ru")
    with awake():
        await followup.process_followups(bot, "views")
    assert "Вы недавно интересовались" in bot.to(USER)[0]["text"]


# ------------------------------------------------------------------ abandon

async def test_abandon_followup_resumes_flow():
    fs, bot = setup()
    fs.data("abandon")[str(USER)] = abandon(type="trial")
    with awake():
        await followup.process_followups(bot, "abandon")
    [msg] = bot.to(USER)
    assert "yarim qoldi" in msg["text"] and buttons(msg["markup"]) == ["trial:g1:f", "fu:stop"]
    doc = fs.data("abandon")[str(USER)]
    assert doc["fu_sent"] == 1 and doc["next_fu_at"] > ahead(hours=18)


async def test_abandon_falls_back_to_join_if_gym_has_no_trial():
    fs, bot = setup(gyms=[sample_gym(free_trial=False)])
    fs.data("abandon")[str(USER)] = abandon(type="trial")
    with awake():
        await followup.process_followups(bot, "abandon")
    assert buttons(bot.to(USER)[0]["markup"])[0] == "join:g1:f"


async def test_abandon_removed_if_lead_already_submitted():
    fs, bot = setup()
    fs.data("abandon")[str(USER)] = abandon()
    fs.data("leads")["l1"] = sample_lead(lead_id="l1", type="join", created_at=ago(minutes=30))
    with awake():
        await followup.process_followups(bot, "abandon")
    assert bot.sent == [] and str(USER) not in fs.data("abandon")


# ------------------------------------------------------------------ sinov eslatmasi va "keldingizmi?"

async def test_visit_reminder():
    fs, bot = setup()
    fs.data("leads")["abc123"] = sample_lead(remind_at=ago(minutes=2))
    await followup.process_reminders(bot)
    [msg] = bot.to(USER)
    assert "Chilonzor 9" in msg["text"] and "Power Gym" in msg["text"] and "Kunduzi" in msg["text"]
    assert buttons(msg["markup"]) == ["v:r:abc123"]
    assert fs.data("leads")["abc123"]["remind_at"] is None
    await followup.process_reminders(bot)
    assert len(bot.to(USER)) == 1


async def test_visit_reminder_without_address_has_no_empty_line():
    fs, bot = setup(gyms=[sample_gym(address="")])
    fs.data("leads")["abc123"] = sample_lead(remind_at=ago(minutes=2))
    await followup.process_reminders(bot)
    assert not bot.to(USER)[0]["text"].rstrip().endswith("📍")


async def test_visit_reminder_skipped_cases():
    cases = [
        (sample_lead(remind_at=ago(minutes=2), status="visited"), {}),
        (sample_lead(remind_at=ago(minutes=2), type="join"), {}),
        (sample_lead(remind_at=ago(hours=7)), {}),                          # eskirgan
        (sample_lead(remind_at=ago(minutes=2)), {"followup_off": True}),    # /stop
        (sample_lead(remind_at=ago(minutes=2), visit_slot=None), {}),
    ]
    for lead, user in cases:
        fs, bot = setup(user)
        fs.data("leads")["abc123"] = lead
        await followup.process_reminders(bot)
        assert bot.sent == [] and fs.data("leads")["abc123"]["remind_at"] is None


async def test_confirm_visit():
    fs, bot = setup()
    fs.data("leads")["abc123"] = sample_lead(confirm_at=ago(minutes=2))
    with awake():
        await followup.process_confirms(bot)
    [msg] = bot.to(USER)
    assert "bordingizmi" in msg["text"] and buttons(msg["markup"]) == ["v:y:abc123", "v:n:abc123"]
    lead = fs.data("leads")["abc123"]
    assert lead["confirm_at"] is None and lead["confirm_sent_at"] is not None


async def test_confirm_skipped_for_finished_leads_and_quiet_hours():
    fs, bot = setup()
    fs.data("leads")["abc123"] = sample_lead(confirm_at=ago(minutes=2), status="won")
    with awake():
        await followup.process_confirms(bot)
    assert bot.sent == [] and fs.data("leads")["abc123"]["confirm_at"] is None

    fs, bot = setup()
    fs.data("leads")["abc123"] = sample_lead(confirm_at=ago(minutes=2))
    with asleep():
        await followup.process_confirms(bot)
    assert bot.sent == [] and fs.data("leads")["abc123"]["confirm_at"] is not None


# ------------------------------------------------------------------ SLA

async def test_sla_reminder_goes_to_owner_once():
    fs, bot = setup()
    fs.data("leads")["abc123"] = sample_lead(sla_at=ago(minutes=1), owner_id=2002)
    await followup.process_sla(bot)
    [msg] = bot.to(2002)
    assert "Eslatma" in msg["text"] and "Ali" in msg["text"] and "st:abc123:c" in buttons(msg["markup"])
    assert bot.to(1001) == []
    lead = fs.data("leads")["abc123"]
    assert lead["sla_at"] is None and lead["sla_notified"] is True
    await followup.process_sla(bot)
    assert len(bot.to(2002)) == 1


async def test_sla_falls_back_to_admin_and_skips_answered():
    fs, bot = setup()
    bot.blocked.add(2002)
    fs.data("leads")["abc123"] = sample_lead(sla_at=ago(minutes=1))
    await followup.process_sla(bot)
    assert bot.to(2002) == [] and "Zal egasiga yetmadi" in bot.to(1001)[0]["text"]

    fs, bot = setup()
    fs.data("leads")["abc123"] = sample_lead(sla_at=ago(minutes=1), status="contacted")
    await followup.process_sla(bot)
    assert bot.sent == [] and fs.data("leads")["abc123"]["sla_at"] is None


# ------------------------------------------------------------------ Sheets, tozalash, sikl

async def test_sheets_retry_marks_ok_and_is_throttled():
    fs, bot = setup()
    fs.data("leads")["abc123"] = sample_lead(sheet_ok=False)
    rows = []

    async def append(row):
        rows.append(row)

    followup._state["sheets_at"] = 0.0
    with patch.object(sheets, "append_lead", append):
        await followup.retry_sheets(bot)
        assert len(rows) == 1 and rows[0][-1] == "abc123"
        assert fs.data("leads")["abc123"]["sheet_ok"] is True
        fs.data("leads")["abc123"]["sheet_ok"] = False
        await followup.retry_sheets(bot)            # 5 daqiqalik oraliq: bu safar o'tkazib yuboriladi
        assert len(rows) == 1


async def test_sheets_retry_failure_keeps_flag():
    fs, bot = setup()
    fs.data("leads")["abc123"] = sample_lead(sheet_ok=False)

    async def boom(row):
        raise RuntimeError("Sheets o'chiq")

    followup._state["sheets_at"] = 0.0
    with patch.object(sheets, "append_lead", boom):
        await followup.retry_sheets(bot)
    assert fs.data("leads")["abc123"]["sheet_ok"] is False


async def test_daily_cleanup_runs_once_per_day_after_4am():
    fs, bot = setup()
    fs.data("fsm")["old"] = {"updated_at": ago(days=20)}
    fs.data("fsm")["new"] = {"updated_at": ago(days=1)}
    followup._state["cleanup_day"] = None
    early = now_utc().astimezone(TZ).replace(hour=2)
    with patch.object(followup, "now_local", lambda: early):
        await followup.maybe_cleanup(bot)
    assert "old" in fs.data("fsm")                  # soat 04:00 dan oldin tegmaydi
    late = early.replace(hour=5)
    with patch.object(followup, "now_local", lambda: late):
        await followup.maybe_cleanup(bot)
        assert set(fs.data("fsm")) == {"new"}
        fs.data("fsm")["old2"] = {"updated_at": ago(days=20)}
        await followup.maybe_cleanup(bot)           # o'sha kuni ikkinchi marta ishlamaydi
    assert "old2" in fs.data("fsm")


async def test_tick_continues_after_job_failure():
    ran = []

    async def boom(bot):
        raise RuntimeError("buzildi")

    async def ok(bot):
        ran.append("ok")

    with patch.object(followup, "process_views", boom), patch.object(followup, "process_reminders", boom), \
            patch.object(followup, "process_sla", ok):
        await followup.tick(FakeBot())
    assert ran == ["ok"]


def test_jobs_respect_followup_flag():
    from unittest.mock import patch as p

    names = [n for n, _ in followup.jobs()]
    assert names[:2] == ["views", "abandon"] and {"remind", "confirm", "sla", "sheets", "cleanup"} <= set(names)
    with p.object(followup.config, "FOLLOWUP_ENABLED", False):
        names = [n for n, _ in followup.jobs()]
    assert "views" not in names and "abandon" not in names and "remind" in names
