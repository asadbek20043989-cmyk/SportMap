"""My Gym bot: ishga tushirish nuqtasi (python main.py)."""
import asyncio
import contextlib
import logging
import sys
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, ErrorEvent

import admin
import common
import config
import db
import followup
import handlers
import sheets
from storage import FirestoreStorage
from texts import t
from utils import esc

log = logging.getLogger("main")

# Telegram'da odatiy, zararsiz xatolar: ularni foydalanuvchiga ko'rsatmaymiz va adminga yubormaymiz
HARMLESS_ERRORS = (
    "query is too old",
    "query id is invalid",
    "message is not modified",
    "message to delete not found",
    "message can't be deleted",
    "message to edit not found",
)
ADMIN_ALERT_GAP_SECONDS = 600
_last_alert: dict[str, float] = {}

USER_COMMANDS = {
    "uz": [
        ("menu", "Bosh menyu"),
        ("lang", "Tilni o'zgartirish"),
        ("help", "Yordam"),
        ("cancel", "Joriy amalni bekor qilish"),
        ("privacy", "Maxfiylik"),
        ("stop", "Eslatmalarni o'chirish"),
        ("resume", "Eslatmalarni yoqish"),
    ],
    "ru": [
        ("menu", "Главное меню"),
        ("lang", "Сменить язык"),
        ("help", "Помощь"),
        ("cancel", "Отменить действие"),
        ("privacy", "Конфиденциальность"),
        ("stop", "Отключить напоминания"),
        ("resume", "Включить напоминания"),
    ],
    "en": [
        ("menu", "Main menu"),
        ("lang", "Change language"),
        ("help", "Help"),
        ("cancel", "Cancel current action"),
        ("privacy", "Privacy"),
        ("stop", "Turn off reminders"),
        ("resume", "Turn on reminders"),
    ],
}
STAFF_COMMANDS = [
    ("admin", "Boshqaruv buyruqlari"),
    ("myleads", "Ochiq arizalar"),
    ("gyms", "Zallar ro'yxati"),
    ("unanswered", "Javobsiz savollar"),
]
ADMIN_COMMANDS = STAFF_COMMANDS + [("stats", "Statistika"), ("reload", "Jadvalni qayta o'qish")]


def _commands(rows) -> list[BotCommand]:
    return [BotCommand(command=c, description=d) for c, d in rows]


async def setup_commands(bot: Bot, gyms: list[dict]) -> None:
    """Menyudagi buyruqlar: hamma uchun, zal egalari va adminlar uchun qo'shimcha."""
    try:
        await bot.set_my_commands(_commands(USER_COMMANDS["uz"]), scope=BotCommandScopeDefault())
        for lang in ("ru", "en", "uz"):
            await bot.set_my_commands(_commands(USER_COMMANDS[lang]), scope=BotCommandScopeDefault(), language_code=lang)
    except Exception:
        log.exception("Bot buyruqlarini o'rnatib bo'lmadi")
    base = USER_COMMANDS["uz"]
    owners = {g["owner_tg_id"] for g in gyms if g.get("owner_tg_id")} - common.admin_ids()
    targets = [(uid, base + ADMIN_COMMANDS) for uid in common.admin_ids()]
    targets += [(uid, base + STAFF_COMMANDS) for uid in owners]
    for uid, rows in targets:
        try:
            await bot.set_my_commands(_commands(rows), scope=BotCommandScopeChat(chat_id=uid))
        except Exception:
            # odam botni hali ishga tushirmagan bo'lsa Telegram rad etadi: bu normal
            log.debug("Buyruqlarni %s uchun o'rnatib bo'lmadi", uid, exc_info=True)


async def alert_admins(bot: Bot, text: str, key: str | None = None) -> None:
    """Super adminlarga xabar. Bir xil `key` bilan xabarlar ADMIN_ALERT_GAP_SECONDS da bir martadan ko'p yuborilmaydi."""
    if key:
        now = time.time()
        if now - _last_alert.get(key, 0.0) < ADMIN_ALERT_GAP_SECONDS:
            return
        _last_alert[key] = now
    for admin_id in common.admin_ids():
        await common.safe_send(bot, admin_id, text)


async def on_error(event: ErrorEvent, bot: Bot) -> bool:
    """Handlerlardagi kutilmagan xatolar: log + foydalanuvchiga muloyim xabar + adminga (kamdan-kam) ogohlantirish."""
    exc = event.exception
    if isinstance(exc, TelegramBadRequest) and any(s in str(exc).lower() for s in HARMLESS_ERRORS):
        log.warning("Zararsiz Telegram xatosi: %s", exc)
        return True
    log.error("Handler xatosi: %r", exc, exc_info=exc)

    update = event.update
    user_id = None
    if update.callback_query:
        user_id = update.callback_query.from_user.id
        try:
            await update.callback_query.answer(t(await db.get_lang(user_id), "generic_error"), show_alert=True)
        except Exception:
            pass
    elif update.message and update.message.chat.type == "private":
        user_id = update.message.from_user.id
        await common.safe_send(bot, user_id, t(await db.get_lang(user_id), "generic_error"))

    name = type(exc).__name__
    await alert_admins(bot, f"⚠️ Bot xatosi: <code>{name}</code>: {esc(str(exc))[:300]}\nFoydalanuvchi: {user_id}", key=name)
    return True


async def startup_check(bot: Bot) -> list[dict]:
    """Firestore va Google Sheets'ni tekshiradi, adminlarga qisqa hisobot yuboradi. Zallar ro'yxatini qaytaradi."""
    fs_ok = await db.ping()
    gyms = await sheets.get_gyms(force=True)
    sheet_ok = sheets.loaded_at() > 0
    problems = sheets.problems() if sheet_ok else []

    lines = ["🚀 <b>My Gym bot ishga tushdi</b>"]
    if fs_ok:
        lines.append("Firestore: ✅")
    else:
        lines.append("Firestore: ❌ ulanib bo'lmadi (FSM xotirada saqlanadi, arizalar faqat Sheets'ga yoziladi)")
    if sheet_ok:
        lines.append(f"Google Sheets: ✅ {len(gyms)} ta faol zal")
        if problems:
            lines.append(f"⚠️ Jadvalda {len(problems)} ta muammo — batafsil: /reload")
    else:
        lines.append("Google Sheets: ❌ jadvalni o'qib bo'lmadi (SHEET_ID va ulashishni tekshiring)")
    for line in lines[1:]:
        log.info(line)
    await alert_admins(bot, "\n".join(lines))
    return gyms


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=FirestoreStorage())
    # TARTIB MUHIM: admin avval. handlers.router oxirida hamma narsani ushlaydigan fallback bor.
    dp.include_router(admin.router)
    dp.include_router(handlers.router)
    dp.errors.register(on_error)
    return dp


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    db.init()  # kalit noto'g'ri bo'lsa shu yerda aniq xato bilan to'xtaydi
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    dp = build_dispatcher()

    scheduler = None
    try:
        me = await bot.get_me()
        log.info("Bot: @%s", me.username)
        gyms = await startup_check(bot)
        await setup_commands(bot, gyms)
        await bot.delete_webhook(drop_pending_updates=False)
        scheduler = followup.start(bot)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if scheduler is not None:
            scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await scheduler
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
