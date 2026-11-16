"""main.py mantig'i: buyruqlar, adminga ogohlantirish, xato ishlovchisi, ishga tushirish tekshiruvi."""
from types import SimpleNamespace
from unittest.mock import patch

from aiogram.exceptions import TelegramBadRequest

import main
import sheets
from fakes import FakeBot, fresh_db, sample_gym, set_gyms

ADMIN_ID, OWNER_ID = 1001, 2002


class CommandBot(FakeBot):
    def __init__(self):
        super().__init__()
        self.commands = []

    async def set_my_commands(self, commands, scope=None, language_code=None):
        self.commands.append((scope, language_code, [c.command for c in commands]))


def _chat_commands(bot, chat_id):
    return [cmds for scope, _, cmds in bot.commands if getattr(scope, "chat_id", None) == chat_id]


async def test_setup_commands_roles():
    bot = CommandBot()
    await main.setup_commands(bot, [sample_gym(owner_tg_id=OWNER_ID), sample_gym(id="g2", owner_tg_id=None)])
    assert _chat_commands(bot, ADMIN_ID)[0][-2:] == ["stats", "reload"]          # admin hammasini ko'radi
    owner = _chat_commands(bot, OWNER_ID)[0]
    assert "myleads" in owner and "stats" not in owner and "reload" not in owner   # egasi: faqat o'z buyruqlari
    langs = {lang for _, lang, _ in bot.commands if lang}
    assert langs == {"uz", "ru", "en"}


async def test_setup_commands_survives_telegram_errors():
    class Boom(CommandBot):
        async def set_my_commands(self, *a, **kw):
            raise RuntimeError("chat not found")

    await main.setup_commands(Boom(), [sample_gym()])    # istisno ko'tarilmasligi kerak


async def test_alert_admins_rate_limit_by_key():
    main._last_alert.clear()
    bot = FakeBot()
    await main.alert_admins(bot, "bir", key="K")
    await main.alert_admins(bot, "ikki", key="K")        # 10 daqiqa ichida: yuborilmaydi
    await main.alert_admins(bot, "uch", key="boshqa")
    await main.alert_admins(bot, "to'rt")                # kalitsiz: doim yuboriladi
    assert [m["text"] for m in bot.to(ADMIN_ID)] == ["bir", "uch", "to'rt"]


def _event(exc, *, callback=None, message=None):
    update = SimpleNamespace(callback_query=callback, message=message)
    return SimpleNamespace(exception=exc, update=update)


async def test_on_error_harmless_telegram_error_is_silent():
    main._last_alert.clear()
    bot = FakeBot()
    exc = TelegramBadRequest(method=None, message="Bad Request: query is too old and response timeout expired")
    assert await main.on_error(_event(exc), bot) is True
    assert bot.sent == []


async def test_on_error_notifies_user_and_admin_once():
    main._last_alert.clear()
    fresh_db()
    bot = FakeBot()
    msg = SimpleNamespace(chat=SimpleNamespace(type="private"), from_user=SimpleNamespace(id=5005))
    for _ in range(2):
        assert await main.on_error(_event(ValueError("<x>"), message=msg), bot) is True
    assert len(bot.to(5005)) == 2                                   # foydalanuvchiga har safar
    alerts = bot.to(ADMIN_ID)
    assert len(alerts) == 1 and "ValueError" in alerts[0]["text"] and "&lt;x&gt;" in alerts[0]["text"]


async def test_on_error_callback_answers_alert():
    main._last_alert.clear()
    fresh_db()
    answers = []

    async def answer(text=None, show_alert=False):
        answers.append((text, show_alert))

    cb = SimpleNamespace(from_user=SimpleNamespace(id=5005), answer=answer)
    assert await main.on_error(_event(RuntimeError("x"), callback=cb), FakeBot()) is True
    assert answers and answers[0][1] is True


async def test_startup_check_reports_ok_and_problems():
    fresh_db()
    rows = [dict(id="ok", name="Zal", district="Chilonzor", lat="41.2", lon="69.2", price_month="300000", active="ha"),
            dict(id="a:b", name="Yomon", district="Chilonzor", lat="41", lon="69", price_month="300000", active="ha")]
    bot = FakeBot()
    with patch.object(sheets, "_read_rows", lambda: rows):
        gyms = await main.startup_check(bot)
    assert [g["id"] for g in gyms] == ["ok"]
    text = bot.to(ADMIN_ID)[0]["text"]
    assert "Firestore: ✅" in text and "1 ta faol zal" in text and "1 ta muammo" in text


async def test_startup_check_reports_sheet_failure():
    fresh_db()
    sheets._cache.update(gyms=[], problems=[], loaded=False, ts=0.0)

    def boom():
        raise RuntimeError("403")

    bot = FakeBot()
    with patch.object(sheets, "_read_rows", boom):
        gyms = await main.startup_check(bot)
    assert gyms == []
    assert "Google Sheets: ❌" in bot.to(ADMIN_ID)[0]["text"]


def test_user_command_languages_have_same_commands():
    base = [c for c, _ in main.USER_COMMANDS["uz"]]
    for lang, rows in main.USER_COMMANDS.items():
        assert [c for c, _ in rows] == base, lang
    for _, desc in main.ADMIN_COMMANDS:
        assert len(desc) <= 256
