from types import SimpleNamespace
from unittest.mock import patch

import admin
import db
import sheets
from fakes import ago, ahead, fresh_db, sample_gym, sample_lead, set_gyms
from utils import now_utc

ADMIN_ID, OWNER_ID, STRANGER = 1001, 2002, 7777


class FakeMessage:
    def __init__(self, html_text="", user_id=OWNER_ID):
        self.html_text = html_text
        self.edits = []
        self.answers = []
        self.from_user = SimpleNamespace(id=user_id)
        self.bot = None

    async def edit_text(self, text, reply_markup=None, **kw):
        self.edits.append((text, reply_markup))

    async def answer(self, text, **kw):
        self.answers.append(text)


def make_cb(data, user_id, html_text="🔔 <b>Yangi ariza</b> — Power Gym\n\nHolat: <b>🆕 Yangi</b>"):
    cb = SimpleNamespace(data=data, from_user=SimpleNamespace(id=user_id), message=FakeMessage(html_text), answers=[])

    async def answer(text=None, show_alert=False):
        cb.answers.append((text, show_alert))

    cb.answer = answer
    return cb


def seed(**lead_over):
    fs = fresh_db()
    set_gyms([sample_gym()])
    lead = sample_lead(sla_at=ahead(hours=1), remind_at=ahead(hours=2), confirm_at=ahead(hours=5), **lead_over)
    fs.data("leads")[lead["lead_id"]] = lead
    return fs, lead


def stored(fs, lead_id="abc123"):
    return fs.data("leads")[lead_id]


# ------------------------------------------------------------------ sof funksiyalar

def test_status_fields_clear_rules():
    contacted = admin.status_fields("contacted")
    assert contacted["sla_at"] is None and "remind_at" not in contacted and "confirm_at" not in contacted
    for status in ("visited", "won", "lost", "no_show"):
        f = admin.status_fields(status)
        assert f["status"] == status and f["sla_at"] is None
        assert f["remind_at"] is None and f["confirm_at"] is None
    assert admin.status_fields("new")["sla_at"] is None


def test_can_manage():
    lead, gym = sample_lead(), sample_gym()
    assert admin.can_manage(ADMIN_ID, lead, gym)
    assert admin.can_manage(OWNER_ID, lead, gym)
    assert not admin.can_manage(STRANGER, lead, gym)
    # egasi jadvalda keyin o'zgargan bo'lsa, yangi egasi ham boshqara oladi
    assert admin.can_manage(3003, lead, sample_gym(owner_tg_id=3003))
    assert not admin.can_manage(STRANGER, sample_lead(owner_id=None), None)


def test_fmt_n_and_chunk_lines():
    assert admin.fmt_n(-1) == "?" and admin.fmt_n(None) == "?" and admin.fmt_n(12345) == "12 345"
    parts = admin.chunk_lines(["a" * 3000, "b" * 3000, "c"])
    assert len(parts) == 2 and all(len(p) <= 3800 for p in parts)


def test_stats_text():
    s = {
        "users": 120, "leads_total": 30, "leads_today": 2, "leads_week": 11, "sheet_pending": 1,
        "st_new": 5, "st_contacted": 4, "st_visited": 3, "st_won": 2, "st_lost": 1, "st_no_show": -1,
        "counters": {"gym_views": 900, "leads_total": 30, "leads_total_by_gym": {"chil_power": 20, "other": 10}},
    }
    text = admin.stats_text(s, {"gym_views": 40}, {"chil_power": "Power <Gym>"})
    assert "<b>120</b>" in text and "Sheets'ga yozilmagan arizalar: <b>1</b>" in text
    assert "Kelmadi: ?" in text                       # -1 = hisoblab bo'lmadi
    assert "Power &lt;Gym&gt; — 20" in text           # nom HTML-xavfsiz
    assert "Zal ko'rishlar: 40" in text and "Zal ko'rishlar: 900" in text
    assert text.index("Power &lt;Gym&gt;") < text.index("other")


def test_gyms_overview_flags():
    gyms = [sample_gym(), sample_gym(id="g2", name="Yangi", district_key="yunusobod", owner_tg_id=None, free_trial=False),
            sample_gym(id="g3", name="Nomalum", district_key="xxx")]
    text = "\n".join(admin.gyms_overview(gyms, ["muammo"]))
    assert "Faol zallar: 3" in text and "egasi yo'q" in text and "Tumani noma'lum" in text and "/reload" in text


# ------------------------------------------------------------------ CRM tugmalari

async def test_owner_marks_visited_clears_followup_fields():
    fs, _ = seed()
    cb = make_cb("st:abc123:v", OWNER_ID)
    await admin.cb_status(cb)
    lead = stored(fs)
    assert lead["status"] == "visited"
    assert lead["sla_at"] is None and lead["remind_at"] is None and lead["confirm_at"] is None
    assert cb.answers[-1][1] is False
    text, markup = cb.message.edits[-1]
    assert "Holat: <b>✅ Keldi</b>" in text and text.startswith("🔔 <b>Yangi ariza</b>")   # sarlavha saqlanadi
    assert "✔ ✅ Keldi" in [b.text for row in markup.inline_keyboard for b in row]


async def test_contacted_clears_only_sla():
    fs, _ = seed()
    await admin.cb_status(make_cb("st:abc123:c", OWNER_ID))
    lead = stored(fs)
    assert lead["status"] == "contacted" and lead["sla_at"] is None
    assert lead["remind_at"] is not None and lead["confirm_at"] is not None   # sinov hali bo'ladi


async def test_super_admin_allowed_stranger_denied():
    fs, _ = seed()
    cb = make_cb("st:abc123:w", STRANGER)
    await admin.cb_status(cb)
    assert stored(fs)["status"] == "new" and cb.answers[-1][1] is True and not cb.message.edits
    await admin.cb_status(make_cb("st:abc123:w", ADMIN_ID))
    assert stored(fs)["status"] == "won"


async def test_same_status_and_stale_buttons():
    fs, _ = seed(status="contacted")
    cb = make_cb("st:abc123:c", OWNER_ID)
    await admin.cb_status(cb)
    assert not cb.message.edits and stored(fs)["sla_at"] is not None     # hech narsa o'zgarmadi
    for data in ("st:yoq:c", "st:abc123:zzz", "st:abc123", "st::"):
        cb = make_cb(data, OWNER_ID)
        await admin.cb_status(cb)
        assert cb.answers[-1][1] is True, data


async def test_card_rebuilt_when_status_line_missing():
    seed()
    cb = make_cb("st:abc123:l", OWNER_ID, html_text="")
    await admin.cb_status(cb)
    assert "❌ Rad etdi" in cb.message.edits[-1][0] and "Ali" in cb.message.edits[-1][0]


# ------------------------------------------------------------------ buyruqlar

async def test_stats_command_admin_only():
    fs, _ = seed()
    fs.data("users")["1"] = {}
    msg = FakeMessage(user_id=STRANGER)
    await admin.cmd_stats(msg)
    assert "faqat adminlar" in msg.answers[0]
    msg = FakeMessage(user_id=ADMIN_ID)
    await admin.cmd_stats(msg)
    assert "Statistika" in msg.answers[0] and "Foydalanuvchilar: <b>1</b>" in msg.answers[0]


async def test_reload_reports_success_and_failure():
    fresh_db()
    rows = [dict(id="ok", name="Zal", district="Chilonzor", lat="41.2", lon="69.2", price_month="300000", active="ha")]
    with patch.object(sheets, "_read_rows", lambda: rows):
        msg = FakeMessage(user_id=ADMIN_ID)
        await admin.cmd_reload(msg)
    assert "1</b> ta faol zal" in msg.answers[0] and "muammo topilmadi" in msg.answers[0]

    def boom():
        raise RuntimeError("Sheets ishlamayapti")

    with patch.object(sheets, "_read_rows", boom):
        msg = FakeMessage(user_id=ADMIN_ID)
        await admin.cmd_reload(msg)
    assert "o'qib bo'lmadi" in msg.answers[0] and "Eski ro'yxat" in msg.answers[0]

    msg = FakeMessage(user_id=STRANGER)
    await admin.cmd_reload(msg)
    assert "faqat adminlar" in msg.answers[0]


async def test_reload_lists_problems():
    fresh_db()
    rows = [dict(id="a:b", name="Yomon<id>", district="Chilonzor", lat="41", lon="69", price_month="300000", active="ha")]
    with patch.object(sheets, "_read_rows", lambda: rows):
        msg = FakeMessage(user_id=ADMIN_ID)
        await admin.cmd_reload(msg)
    assert "Muammolar" in msg.answers[0] and "Yomon&lt;id&gt;" in msg.answers[0]   # HTML-xavfsiz


async def test_gyms_command_roles():
    fresh_db()
    set_gyms([sample_gym(), sample_gym(id="g2", name="Boshqa", owner_tg_id=3003)])
    msg = FakeMessage(user_id=OWNER_ID)
    await admin.cmd_gyms(msg)
    assert "Power Gym" in msg.answers[0] and "Boshqa" not in msg.answers[0]
    msg = FakeMessage(user_id=ADMIN_ID)
    await admin.cmd_gyms(msg)
    assert "Boshqa" in msg.answers[0] and "Power Gym" in msg.answers[0]
    msg = FakeMessage(user_id=STRANGER)
    await admin.cmd_gyms(msg)
    assert "faqat zal egalari" in msg.answers[0]


async def test_unanswered_filtered_by_owner():
    fs = fresh_db()
    set_gyms([sample_gym(), sample_gym(id="g2", name="Boshqa", owner_tg_id=3003)])
    fs.data("unanswered").update({
        "1": {"gym_id": "g1", "question": "Hammom bormi? <b>", "created_at": ago(hours=1)},
        "2": {"gym_id": "g2", "question": "Boshqa zal savoli", "created_at": ago(hours=2)},
    })
    msg = FakeMessage(user_id=OWNER_ID)
    await admin.cmd_unanswered(msg)
    assert "Hammom bormi? &lt;b&gt;" in msg.answers[0] and "Boshqa zal savoli" not in msg.answers[0]
    msg = FakeMessage(user_id=ADMIN_ID)
    await admin.cmd_unanswered(msg)
    assert "Boshqa zal savoli" in msg.answers[0]


async def test_myleads_sends_cards_with_crm_buttons():
    fs, _ = seed()
    fs.data("leads")["done"] = sample_lead(lead_id="done", status="won")        # ochiq emas
    sent = []

    async def fake_send(bot, chat_id, text, markup=None, **kw):
        sent.append((chat_id, text, markup))
        return "ok"

    with patch.object(admin, "safe_send", fake_send):
        msg = FakeMessage(user_id=OWNER_ID)
        await admin.cmd_myleads(msg)
    assert len(sent) == 1 and sent[0][0] == OWNER_ID and "Ali" in sent[0][1]
    assert any(b.callback_data == "st:abc123:v" for row in sent[0][2].inline_keyboard for b in row)

    msg = FakeMessage(user_id=STRANGER)
    await admin.cmd_myleads(msg)
    assert "faqat zal egalari" in msg.answers[0]


async def test_router_order_in_main():
    import main

    dp = main.build_dispatcher()
    names = [r.name for r in dp.sub_routers]
    assert names.index(admin.router.name) < names.index(__import__("handlers").router.name)
