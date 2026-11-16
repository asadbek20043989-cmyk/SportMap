"""Foydalanuvchi oqimlari: til, tuman, zal kartasi, savol (AI), ariza, tavsiya, yaqin zallar, tashrif."""
import asyncio
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InputMediaPhoto,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

import ai
import common
import config
import db
import recommend
import sheets
from common import (
    answer_cb,
    btn,
    contact_of,
    dates_kb,
    district_label,
    district_name,
    gym_buttons,
    gym_card_kb,
    gym_card_text,
    gym_list,
    kb,
    main_menu_kb,
    safe_send,
    show,
    slot_label,
    slots_kb,
)
from texts import BUDGET_KEYS, DISTRICTS, GOAL_KEYS, LANGS, CHOOSE_LANG, DISTRICT_NAMES, t
from utils import (
    available_slots,
    esc,
    fmt_km,
    fmt_money,
    norm_phone,
    now_utc,
    parse_day,
)

log = logging.getLogger("handlers")

router = Router()
router.message.filter(F.chat.type == "private")

_bg_tasks: set[asyncio.Task] = set()


def bg(coro) -> None:
    """Muhim bo'lmagan ishni (statistika, follow-up yozuvi) javobni kutdirmasdan bajaradi."""
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


# ------------------------------------------------------------------ holatlar

class Ask(StatesGroup):
    question = State()


class Join(StatesGroup):
    name = State()
    phone = State()
    goal = State()
    date = State()
    slot = State()


class Rec(StatesGroup):
    goal = State()
    budget = State()
    where = State()
    district = State()
    location = State()


class Near(StatesGroup):
    location = State()


class Resched(StatesGroup):
    date = State()
    slot = State()


# ------------------------------------------------------------------ kichik yordamchilar

def lang_kb():
    return kb([[btn(name, f"lang:{code}")] for code, name in LANGS.items()])


def menu_only_kb(lang: str):
    return kb([[btn(t(lang, "btn_menu"), "menu")]])


def location_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(lang, "btn_send_location"), request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def phone_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(lang, "btn_share_phone"), request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def lang_of(user_id: int) -> str:
    return await db.get_lang(user_id)


async def send_menu(message: Message, lang: str, first: bool = False) -> None:
    await message.answer(t(lang, "welcome" if first else "pick_district"), reply_markup=main_menu_kb(lang))


async def reset_flow(state: FSMContext, user_id: int) -> None:
    await state.clear()
    await db.clear_abandon(user_id)


async def need_gym(cb: CallbackQuery, gym_id: str, lang: str) -> dict | None:
    gym = await sheets.get_gym(gym_id)
    if not gym:
        await answer_cb(cb, t(lang, "gym_missing"), alert=True)
    return gym


def split_cb(data: str, parts: int) -> list[str]:
    items = data.split(":")
    return (items + [""] * parts)[:parts]


# ------------------------------------------------------------------ buyruqlar

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    uid = message.from_user.id
    await reset_flow(state, uid)
    if not await db.has_lang(uid):
        await message.answer(CHOOSE_LANG, reply_markup=lang_kb())
        return
    lang = await lang_of(uid)
    await message.answer(".", reply_markup=ReplyKeyboardRemove())
    await send_menu(message, lang, first=True)


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    uid = message.from_user.id
    await reset_flow(state, uid)
    lang = await lang_of(uid)
    await message.answer(t(lang, "pick_district"), reply_markup=ReplyKeyboardRemove())
    await message.answer(t(lang, "welcome"), reply_markup=main_menu_kb(lang))


@router.message(Command("lang", "language"))
async def cmd_lang(message: Message):
    await message.answer(CHOOSE_LANG, reply_markup=lang_kb())


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(t(await lang_of(message.from_user.id), "help"))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    uid = message.from_user.id
    await reset_flow(state, uid)
    lang = await lang_of(uid)
    await message.answer(t(lang, "cancelled"), reply_markup=ReplyKeyboardRemove())
    await send_menu(message, lang)


@router.message(Command("privacy"))
async def cmd_privacy(message: Message):
    await message.answer(t(await lang_of(message.from_user.id), "privacy"))


@router.message(Command("stop"))
async def cmd_stop(message: Message):
    uid = message.from_user.id
    await db.set_followups(uid, False)
    await message.answer(t(await lang_of(uid), "fu_stopped"))


@router.message(Command("resume"))
async def cmd_resume(message: Message):
    uid = message.from_user.id
    await db.set_followups(uid, True)
    await message.answer(t(await lang_of(uid), "fu_resumed"))


@router.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(f"Telegram ID: <code>{message.from_user.id}</code>")


# ------------------------------------------------------------------ til, bosh menyu, tuman

@router.callback_query(F.data.startswith("lang:"))
async def cb_lang(cb: CallbackQuery, state: FSMContext):
    code = cb.data.split(":", 1)[1]
    await answer_cb(cb)
    if code not in LANGS:
        return
    uid = cb.from_user.id
    first_time = not await db.has_lang(uid)
    await db.set_lang(uid, code, name=cb.from_user.first_name or "", username=cb.from_user.username or "")
    await reset_flow(state, uid)
    await show(cb, t(code, "welcome" if first_time else "pick_district"), main_menu_kb(code))


@router.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery, state: FSMContext):
    await answer_cb(cb)
    uid = cb.from_user.id
    await reset_flow(state, uid)
    lang = await lang_of(uid)
    await show(cb, t(lang, "welcome"), main_menu_kb(lang))


@router.callback_query(F.data == "x")
async def cb_noop(cb: CallbackQuery):
    await answer_cb(cb)


@router.callback_query(F.data.startswith("d:"))
async def cb_district(cb: CallbackQuery):
    await answer_cb(cb)
    _, key, page = split_cb(cb.data, 3)
    lang = await lang_of(cb.from_user.id)
    if key not in DISTRICT_NAMES:
        await show(cb, t(lang, "welcome"), main_menu_kb(lang))
        return
    gyms = await sheets.get_gyms()
    text, markup = gym_list(gyms, lang, key, int(page) if page.isdigit() else 0)
    await show(cb, text, markup)


# ------------------------------------------------------------------ zal kartasi

@router.callback_query(F.data.startswith("g:"))
async def cb_gym(cb: CallbackQuery):
    await answer_cb(cb)
    _, gym_id, src = split_cb(cb.data, 3)
    uid = cb.from_user.id
    lang = await lang_of(uid)
    gym = await need_gym(cb, gym_id, lang)
    if not gym:
        return
    bg(db.record_view(uid, gym["id"], lang))
    bg(db.bump("gym_views", gym["id"]))
    await send_gym_card(cb, gym, lang, src or "b")


async def send_gym_card(cb: CallbackQuery, gym: dict, lang: str, src: str) -> None:
    text = gym_card_text(gym, lang)
    markup = gym_card_kb(gym, lang, src)
    photos = gym["photos"][:6]
    uid = cb.from_user.id
    if photos:
        try:
            if len(photos) == 1 and len(text) <= 1000:
                await cb.bot.send_photo(uid, photos[0], caption=text, reply_markup=markup)
            else:
                await cb.bot.send_media_group(uid, [InputMediaPhoto(media=p) for p in photos])
                await cb.bot.send_message(uid, text, reply_markup=markup)
            try:
                await cb.message.delete()
            except Exception:
                pass
            return
        except TelegramBadRequest:
            log.warning("Zal %s rasmlarini yuborib bo'lmadi (havola yaroqsiz bo'lishi mumkin)", gym["id"])
    await show(cb, text, markup)


# ------------------------------------------------------------------ savol berish (AI)

@router.callback_query(F.data.startswith("ask:"))
async def cb_ask(cb: CallbackQuery, state: FSMContext):
    await answer_cb(cb)
    gym_id = cb.data.split(":", 1)[1]
    uid = cb.from_user.id
    lang = await lang_of(uid)
    gym = await need_gym(cb, gym_id, lang)
    if not gym:
        return
    await reset_flow(state, uid)
    await state.set_state(Ask.question)
    await state.update_data(gym_id=gym["id"])
    bg(db.record_view(uid, gym["id"], lang))
    await cb.message.answer(
        t(lang, "ask_prompt", gym=esc(gym["name"])),
        reply_markup=kb([[btn(t(lang, "back_list"), f"d:{gym['district_key']}:0"), btn(t(lang, "btn_menu"), "menu")]]),
    )


@router.message(Ask.question, F.text, ~F.text.startswith("/"))
async def on_question(message: Message, state: FSMContext):
    uid = message.from_user.id
    lang = await lang_of(uid)
    data = await state.get_data()
    gym = await sheets.get_gym(data.get("gym_id", ""))
    if not gym:
        await state.clear()
        await message.answer(t(lang, "gym_missing"), reply_markup=main_menu_kb(lang))
        return
    if not ai.rate_ok(uid):
        await message.answer(t(lang, "too_many"))
        return
    try:
        await message.bot.send_chat_action(message.chat.id, "typing")
    except Exception:
        pass
    bg(db.bump("asks_total", gym["id"]))
    try:
        reply, known = await ai.answer(gym, message.text, lang)
    except ai.AIUnavailable:
        await message.answer(t(lang, "ai_error"))
        return
    markup = gym_card_kb(gym, lang)
    if not known:
        bg(db.log_unanswered(uid, gym["id"], message.text, lang))
        await message.answer(t(lang, "no_info", contact=contact_of(gym, lang)), reply_markup=markup)
        return
    await message.answer(reply, reply_markup=markup)


@router.message(Ask.question)
async def on_question_not_text(message: Message):
    await message.answer(t(await lang_of(message.from_user.id), "only_text"))


# ------------------------------------------------------------------ ariza / bepul sinov

async def start_lead_flow(cb: CallbackQuery, state: FSMContext, lead_type: str) -> None:
    await answer_cb(cb)
    _, gym_id, src = split_cb(cb.data, 3)
    uid = cb.from_user.id
    lang = await lang_of(uid)
    gym = await need_gym(cb, gym_id, lang)
    if not gym:
        return
    if lead_type == "trial" and not gym["free_trial"]:
        await answer_cb(cb, t(lang, "stale"), alert=True)
        return
    if await db.find_recent_lead(uid, gym["id"], lead_type):
        await cb.message.answer(
            t(lang, "dup_lead", gym=esc(gym["name"]), contact=contact_of(gym, lang)),
            reply_markup=menu_only_kb(lang),
        )
        return
    if not await db.check_daily_limit(uid):
        await cb.message.answer(t(lang, "lead_limit"), reply_markup=menu_only_kb(lang))
        return
    await state.clear()
    await state.set_state(Join.name)
    await state.update_data(gym_id=gym["id"], lead_type=lead_type, source=src or "b")
    bg(db.start_abandon(uid, gym["id"], lead_type, lang))
    await cb.message.answer(t(lang, "ask_name"))


@router.callback_query(F.data.startswith("join:"))
async def cb_join(cb: CallbackQuery, state: FSMContext):
    await start_lead_flow(cb, state, "join")


@router.callback_query(F.data.startswith("trial:"))
async def cb_trial(cb: CallbackQuery, state: FSMContext):
    await start_lead_flow(cb, state, "trial")


@router.message(Join.name, F.text, ~F.text.startswith("/"))
async def join_name(message: Message, state: FSMContext):
    lang = await lang_of(message.from_user.id)
    name = " ".join(message.text.split())
    if not (2 <= len(name) <= 60) or not any(ch.isalpha() for ch in name):
        await message.answer(t(lang, "bad_name"))
        return
    await state.update_data(name=name)
    await state.set_state(Join.phone)
    await message.answer(t(lang, "ask_phone"), reply_markup=phone_kb(lang))


@router.message(Join.name)
async def join_name_bad(message: Message):
    await message.answer(t(await lang_of(message.from_user.id), "bad_name"))


async def _accept_phone(message: Message, state: FSMContext, phone: str, verified: bool, lang: str) -> None:
    await state.update_data(phone=phone, phone_verified=verified)
    await state.set_state(Join.goal)
    await message.answer(f"✅ {phone}", reply_markup=ReplyKeyboardRemove())
    rows = [[btn(t(lang, f"goal_{k}"), f"goal:{k}")] for k in GOAL_KEYS]
    await message.answer(t(lang, "ask_goal"), reply_markup=kb(rows))


@router.message(Join.phone, F.contact)
async def join_phone_contact(message: Message, state: FSMContext):
    lang = await lang_of(message.from_user.id)
    contact = message.contact
    if contact.user_id != message.from_user.id:
        await message.answer(t(lang, "not_own_contact"), reply_markup=phone_kb(lang))
        return
    phone = norm_phone(contact.phone_number, verified=True)
    if not phone:
        await message.answer(t(lang, "bad_phone"))
        return
    await _accept_phone(message, state, phone, True, lang)


@router.message(Join.phone, F.text, ~F.text.startswith("/"))
async def join_phone_text(message: Message, state: FSMContext):
    lang = await lang_of(message.from_user.id)
    phone = norm_phone(message.text)
    if not phone:
        await message.answer(t(lang, "bad_phone"))
        return
    await _accept_phone(message, state, phone, False, lang)


@router.message(Join.phone)
async def join_phone_other(message: Message):
    lang = await lang_of(message.from_user.id)
    await message.answer(t(lang, "phone_hint"), reply_markup=phone_kb(lang))


@router.callback_query(Join.goal, F.data.startswith("goal:"))
async def join_goal(cb: CallbackQuery, state: FSMContext):
    await answer_cb(cb)
    key = cb.data.split(":", 1)[1]
    lang = await lang_of(cb.from_user.id)
    if key not in GOAL_KEYS:
        await answer_cb(cb, t(lang, "use_buttons"), alert=True)
        return
    await state.update_data(goal=key)
    data = await state.get_data()
    if data.get("lead_type") == "trial":
        markup = dates_kb(lang)
        if markup is None:
            await show(cb, t(lang, "no_slots"), menu_only_kb(lang))
            return
        await state.set_state(Join.date)
        await show(cb, t(lang, "ask_date"), markup)
        return
    await finalize(cb, state, lang)


@router.callback_query(Join.date, F.data.startswith("day:"))
async def join_day(cb: CallbackQuery, state: FSMContext):
    lang = await lang_of(cb.from_user.id)
    day = parse_day(cb.data.split(":", 1)[1])
    markup = slots_kb(lang, day) if day else None
    if markup is None:
        await answer_cb(cb, t(lang, "no_slots"), alert=True)
        return
    await answer_cb(cb)
    await state.update_data(day=day.isoformat())
    await state.set_state(Join.slot)
    await show(cb, t(lang, "ask_slot"), markup)


@router.callback_query(Join.slot, F.data.startswith("slot:"))
async def join_slot(cb: CallbackQuery, state: FSMContext):
    lang = await lang_of(cb.from_user.id)
    slot = cb.data.split(":", 1)[1]
    data = await state.get_data()
    day = parse_day(data.get("day"))
    if day is None or slot not in available_slots(day):
        await answer_cb(cb, t(lang, "no_slots"), alert=True)
        markup = dates_kb(lang)
        if markup is not None:
            await state.set_state(Join.date)
            await show(cb, t(lang, "ask_date"), markup)
        return
    await answer_cb(cb)
    await state.update_data(slot=slot)
    await finalize(cb, state, lang)


async def finalize(cb: CallbackQuery, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    result, text = await common.submit_lead(cb.bot, cb.from_user, lang, data)
    if result == "fail":
        # holat saqlanadi: foydalanuvchi oxirgi tugmani qayta bosib urinib ko'ra oladi
        await show(cb, text, menu_only_kb(lang))
        return
    await state.clear()
    await show(cb, text, menu_only_kb(lang))


@router.message(Join.goal)
@router.message(Join.date)
@router.message(Join.slot)
async def join_use_buttons(message: Message):
    await message.answer(t(await lang_of(message.from_user.id), "use_buttons"))


# ------------------------------------------------------------------ tavsiya

def _budget_kb(lang: str):
    rows = [[btn(t(lang, f"bud_{k}"), f"rb:{k}")] for k in BUDGET_KEYS]
    return kb(rows)


def _where_kb(lang: str):
    return kb([
        [btn(t(lang, "where_near"), "rw:near")],
        [btn(t(lang, "where_district"), "rw:district")],
        [btn(t(lang, "where_all"), "rw:all")],
    ])


def _districts_kb(lang: str, prefix: str):
    rows, row = [], []
    for key, names in DISTRICTS:
        row.append(btn(names.get(lang) or names["uz"], f"{prefix}:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([btn(t(lang, "btn_menu"), "menu")])
    return kb(rows)


@router.callback_query(F.data == "rec:start")
async def rec_start(cb: CallbackQuery, state: FSMContext):
    await answer_cb(cb)
    uid = cb.from_user.id
    lang = await lang_of(uid)
    await reset_flow(state, uid)
    await state.set_state(Rec.goal)
    rows = [[btn(t(lang, f"goal_{k}"), f"rg:{k}")] for k in GOAL_KEYS]
    rows.append([btn(t(lang, "btn_menu"), "menu")])
    await show(cb, t(lang, "rec_intro"), kb(rows))


@router.callback_query(Rec.goal, F.data.startswith("rg:"))
async def rec_goal(cb: CallbackQuery, state: FSMContext):
    await answer_cb(cb)
    key = cb.data.split(":", 1)[1]
    if key not in GOAL_KEYS:
        return
    lang = await lang_of(cb.from_user.id)
    await state.update_data(goal=key)
    await state.set_state(Rec.budget)
    await show(cb, t(lang, "rec_budget"), _budget_kb(lang))


@router.callback_query(Rec.budget, F.data.startswith("rb:"))
async def rec_budget(cb: CallbackQuery, state: FSMContext):
    await answer_cb(cb)
    key = cb.data.split(":", 1)[1]
    if key not in BUDGET_KEYS:
        return
    lang = await lang_of(cb.from_user.id)
    await state.update_data(budget=key)
    await state.set_state(Rec.where)
    await show(cb, t(lang, "rec_where"), _where_kb(lang))


@router.callback_query(Rec.where, F.data.startswith("rw:"))
async def rec_where(cb: CallbackQuery, state: FSMContext):
    await answer_cb(cb)
    where = cb.data.split(":", 1)[1]
    lang = await lang_of(cb.from_user.id)
    if where == "near":
        await state.set_state(Rec.location)
        await cb.message.answer(t(lang, "near_prompt"), reply_markup=location_kb(lang))
    elif where == "district":
        await state.set_state(Rec.district)
        await show(cb, t(lang, "pick_district"), _districts_kb(lang, "rd"))
    elif where == "all":
        data = await state.get_data()
        await state.clear()
        title, body, markup = await build_recommendation(lang, data, "all", None, None)
        await show(cb, f"{title}\n\n{body}" if body else title, markup)


@router.callback_query(Rec.district, F.data.startswith("rd:"))
async def rec_district(cb: CallbackQuery, state: FSMContext):
    await answer_cb(cb)
    key = cb.data.split(":", 1)[1]
    if key not in DISTRICT_NAMES:
        return
    lang = await lang_of(cb.from_user.id)
    data = await state.get_data()
    await state.clear()
    title, body, markup = await build_recommendation(lang, data, "district", key, None)
    await show(cb, f"{title}\n\n{body}" if body else title, markup)


@router.message(Rec.location, F.location)
async def rec_location(message: Message, state: FSMContext):
    lang = await lang_of(message.from_user.id)
    data = await state.get_data()
    await state.clear()
    pos = (message.location.latitude, message.location.longitude)  # saqlanmaydi
    title, body, markup = await build_recommendation(lang, data, "near", None, pos)
    await message.answer(title, reply_markup=ReplyKeyboardRemove())
    if body:
        await message.answer(body, reply_markup=markup)
    else:
        await message.answer(t(lang, "pick_district"), reply_markup=markup)


@router.message(Rec.location)
async def rec_need_location(message: Message):
    lang = await lang_of(message.from_user.id)
    await message.answer(t(lang, "need_location"), reply_markup=location_kb(lang))


async def build_recommendation(lang: str, data: dict, where: str, district_key, pos):
    """(sarlavha, tavsiyalar matni, tugmalar). Topilmasa matn bo'sh bo'ladi."""
    gyms = await sheets.get_gyms()
    matches = recommend.recommend(
        gyms,
        goal=data.get("goal", "other"),
        budget=data.get("budget", "any"),
        where=where,
        district_key=district_key,
        user_pos=pos,
    )
    again = [btn(t(lang, "rec_again"), "rec:start")]
    menu = [btn(t(lang, "btn_menu"), "menu")]
    if not matches:
        return t(lang, "rec_none"), "", kb([again, menu])
    lines = []
    for i, m in enumerate(matches, 1):
        g = m.gym
        price = t(lang, "price_per_month", price=fmt_money(g["price_month"])) if g.get("price_month") else ""
        reasons = ", ".join(t(lang, key, **kw) for key, kw in m.reasons)
        line = t(lang, "rec_line", i=i, name=esc(g["name"]), district=esc(district_name(g, lang)), price=price, reasons=reasons)
        lines.append(line if reasons else line.split("\n")[0])
    rows = gym_buttons([(m.gym, "") for m in matches], "r") + [again, menu]
    return t(lang, "rec_results"), "\n\n".join(lines), kb(rows)


# ------------------------------------------------------------------ menga yaqin zallar

@router.callback_query(F.data == "near:start")
async def near_start(cb: CallbackQuery, state: FSMContext):
    await answer_cb(cb)
    uid = cb.from_user.id
    lang = await lang_of(uid)
    await reset_flow(state, uid)
    await state.set_state(Near.location)
    await cb.message.answer(t(lang, "near_prompt"), reply_markup=location_kb(lang))


@router.message(Near.location, F.location)
async def near_location(message: Message, state: FSMContext):
    lang = await lang_of(message.from_user.id)
    await state.clear()
    gyms = await sheets.get_gyms()
    rows = recommend.nearest(gyms, message.location.latitude, message.location.longitude, limit=config.PAGE_SIZE)
    if not rows:
        await message.answer(t(lang, "near_none"), reply_markup=ReplyKeyboardRemove())
        await send_menu(message, lang)
        return
    lines = [
        f"{i}. <b>{esc(g['name'])}</b> ({esc(district_name(g, lang))}) — {fmt_km(km)} km"
        for i, (g, km) in enumerate(rows, 1)
    ]
    await message.answer(f"{t(lang, 'near_title')}\n\n" + "\n".join(lines), reply_markup=ReplyKeyboardRemove())
    buttons = gym_buttons([(g, f"{fmt_km(km)} km") for g, km in rows], "n")
    buttons.append([btn(t(lang, "btn_menu"), "menu")])
    await message.answer(t(lang, "pick_gym"), reply_markup=kb(buttons))


@router.message(Near.location)
async def near_need_location(message: Message):
    lang = await lang_of(message.from_user.id)
    await message.answer(t(lang, "need_location"), reply_markup=location_kb(lang))


# ------------------------------------------------------------------ "keldingizmi?" javobi va vaqtni o'zgartirish

async def _own_lead(cb: CallbackQuery, lead_id: str, lang: str) -> dict | None:
    lead = await db.get_lead(lead_id)
    if not lead or lead.get("tg_id") != cb.from_user.id:
        await answer_cb(cb, t(lang, "stale"), alert=True)
        return None
    return lead


@router.callback_query(F.data.startswith("v:"))
async def cb_visit(cb: CallbackQuery, state: FSMContext):
    _, action, lead_id = split_cb(cb.data, 3)
    uid = cb.from_user.id
    lang = await lang_of(uid)
    lead = await _own_lead(cb, lead_id, lang)
    if not lead:
        return
    await answer_cb(cb)
    gym = await sheets.get_gym(lead["gym_id"])
    status = lead.get("status", "new")

    if action == "y":
        if status in ("new", "contacted", "no_show"):
            await db.update_lead(lead_id, status="visited", status_at=now_utc(), sla_at=None)
        contact = contact_of(gym, lang) if gym else t(lang, "no_contact")
        await show(cb, t(lang, "visit_yes", contact=contact), menu_only_kb(lang))
        if gym:
            await common.notify_owner_note(cb.bot, gym, f"✅ <b>{esc(lead['name'])}</b> ({esc(lead['phone'])}) sinovga keldi.\n#{lead_id}")
    elif action == "n":
        if status in ("new", "contacted"):
            await db.update_lead(lead_id, status="no_show", status_at=now_utc(), sla_at=None)
        markup = kb([[btn(t(lang, "btn_visit_resched"), f"v:r:{lead_id}")], [btn(t(lang, "btn_menu"), "menu")]])
        await show(cb, t(lang, "visit_no"), markup)
        if gym:
            await common.notify_owner_note(cb.bot, gym, f"🚫 <b>{esc(lead['name'])}</b> ({esc(lead['phone'])}) sinovga kelmadi.\n#{lead_id}")
    elif action == "r":
        markup = dates_kb(lang)
        if markup is None:
            await show(cb, t(lang, "no_slots"), menu_only_kb(lang))
            return
        await state.clear()
        await state.set_state(Resched.date)
        await state.update_data(lead_id=lead_id)
        await show(cb, t(lang, "ask_date"), markup)


@router.callback_query(Resched.date, F.data.startswith("day:"))
async def resched_day(cb: CallbackQuery, state: FSMContext):
    lang = await lang_of(cb.from_user.id)
    day = parse_day(cb.data.split(":", 1)[1])
    markup = slots_kb(lang, day) if day else None
    if markup is None:
        await answer_cb(cb, t(lang, "no_slots"), alert=True)
        return
    await answer_cb(cb)
    await state.update_data(day=day.isoformat())
    await state.set_state(Resched.slot)
    await show(cb, t(lang, "ask_slot"), markup)


@router.callback_query(Resched.slot, F.data.startswith("slot:"))
async def resched_slot(cb: CallbackQuery, state: FSMContext):
    lang = await lang_of(cb.from_user.id)
    slot = cb.data.split(":", 1)[1]
    data = await state.get_data()
    day = parse_day(data.get("day"))
    if day is None or slot not in available_slots(day):
        await answer_cb(cb, t(lang, "no_slots"), alert=True)
        return
    await answer_cb(cb)
    lead = await _own_lead(cb, data.get("lead_id", ""), lang)
    await state.clear()
    if not lead:
        return
    text = await common.reschedule_visit(cb.bot, lang, lead["lead_id"], day, slot)
    await show(cb, text or t(lang, "lead_fail"), menu_only_kb(lang))


@router.message(Resched.date)
@router.message(Resched.slot)
async def resched_use_buttons(message: Message):
    await message.answer(t(await lang_of(message.from_user.id), "use_buttons"))


# ------------------------------------------------------------------ eslatmalarni o'chirish tugmasi

@router.callback_query(F.data == "fu:stop")
async def cb_fu_stop(cb: CallbackQuery):
    await answer_cb(cb)
    uid = cb.from_user.id
    await db.set_followups(uid, False)
    await db.clear_abandon(uid)
    await safe_send(cb.bot, uid, t(await lang_of(uid), "fu_stopped"))


# ------------------------------------------------------------------ bot bloklandi / qayta yoqildi

@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated):
    if event.chat.type != "private":
        return
    status = event.new_chat_member.status
    if status == "kicked":
        await db.mark_blocked(event.from_user.id, True)
    elif status == "member":
        await db.mark_blocked(event.from_user.id, False)


# ------------------------------------------------------------------ fallback (eng oxirida)

@router.callback_query()
async def cb_stale(cb: CallbackQuery):
    lang = await lang_of(cb.from_user.id)
    await answer_cb(cb, t(lang, "stale"), alert=True)


@router.message()
async def fallback(message: Message, state: FSMContext):
    uid = message.from_user.id
    if not await db.has_lang(uid):
        await message.answer(CHOOSE_LANG, reply_markup=lang_kb())
        return
    lang = await lang_of(uid)
    if await state.get_state():
        await message.answer(t(lang, "use_buttons"))
        return
    await message.answer(t(lang, "help_choose"), reply_markup=main_menu_kb(lang))


__all__ = ["router", "district_label", "slot_label"]
