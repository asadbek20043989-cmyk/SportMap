"""Testlar uchun soxta Firestore (xotirada) va soxta bot.

FakeClient `db._client` o'rniga qo'yiladi, shunda haqiqiy db.py kodi (so'rovlar, hisoblagichlar,
navbatlar) Firestore'siz ishlaydi. Firestore xatti-harakatlari taqlid qilinadi:
  * tengsizlik (<=, >=) faqat bir xil turdagi maydonlarni qaytaradi (None va yo'q maydonlar chiqmaydi);
  * set(merge=True) ichma-ich lug'atlarni birlashtiradi va Increment ni qo'shadi;
  * update() mavjud bo'lmagan hujjatda xato beradi.
"""
import copy
from datetime import timedelta
from types import SimpleNamespace

from aiogram.exceptions import TelegramForbiddenError
from google.cloud.firestore_v1 import Increment

import db
import sheets
from utils import now_utc


def _merge(dst: dict, src: dict) -> None:
    for key, value in src.items():
        if isinstance(value, Increment):
            dst[key] = (dst.get(key) or 0) + value.value
        elif isinstance(value, dict) and isinstance(dst.get(key), dict):
            _merge(dst[key], value)
        elif isinstance(value, dict):
            dst[key] = {}
            _merge(dst[key], value)
        else:
            dst[key] = value


class FakeSnap:
    def __init__(self, ref, data):
        self.reference = ref
        self.id = ref.id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


class FakeDoc:
    def __init__(self, store: dict, doc_id: str):
        self._store = store
        self.id = doc_id

    def get(self):
        return FakeSnap(self, self._store.get(self.id))

    def set(self, data, merge=False):
        if merge and self.id in self._store:
            _merge(self._store[self.id], data)
        else:
            fresh = {}
            _merge(fresh, data)
            self._store[self.id] = fresh

    def update(self, fields):
        if self.id not in self._store:
            raise KeyError(f"hujjat yo'q: {self.id}")
        _merge(self._store[self.id], fields)

    def delete(self):
        self._store.pop(self.id, None)


def _compatible(field_value, value) -> bool:
    if field_value is None:
        return False
    if isinstance(value, bool):
        return isinstance(field_value, bool)
    if isinstance(value, (int, float)):
        return isinstance(field_value, (int, float)) and not isinstance(field_value, bool)
    return isinstance(field_value, type(value))


class FakeQuery:
    def __init__(self, store: dict, filters=None, limit=None, order=None):
        self._store = store
        self._filters = filters or []
        self._limit = limit
        self._order = order

    def where(self, field, op, value):
        return FakeQuery(self._store, self._filters + [(field, op, value)], self._limit, self._order)

    def limit(self, n):
        return FakeQuery(self._store, self._filters, n, self._order)

    def order_by(self, field, direction=None):
        return FakeQuery(self._store, self._filters, self._limit, (field, direction))

    def _match(self, data) -> bool:
        for field, op, value in self._filters:
            fv = data.get(field)
            if op == "==":
                if field not in data or fv != value or isinstance(fv, bool) != isinstance(value, bool):
                    return False
            elif op == "in":
                if fv not in value:
                    return False
            elif op in ("<=", ">=", "<", ">"):
                if not _compatible(fv, value):
                    return False
                if not {"<=": fv <= value, ">=": fv >= value, "<": fv < value, ">": fv > value}[op]:
                    return False
            else:
                raise NotImplementedError(op)
        return True

    def stream(self):
        rows = [(doc_id, data) for doc_id, data in self._store.items() if self._match(data)]
        if self._order:
            field, direction = self._order
            rows.sort(key=lambda r: r[1].get(field) or 0, reverse=bool(direction))
        if self._limit is not None:
            rows = rows[: self._limit]
        return [FakeSnap(FakeDoc(self._store, doc_id), data) for doc_id, data in rows]

    def count(self, alias=None):
        n = len([1 for d in self._store.values() if self._match(d)])
        return SimpleNamespace(get=lambda: [[SimpleNamespace(value=n)]])


class FakeCollection(FakeQuery):
    def __init__(self, store: dict):
        super().__init__(store)
        self._counter = 0

    def document(self, doc_id):
        return FakeDoc(self._store, str(doc_id))

    def add(self, data):
        self._counter += 1
        doc_id = f"auto{self._counter}"
        FakeDoc(self._store, doc_id).set(data)
        return None, FakeDoc(self._store, doc_id)


class FakeClient:
    def __init__(self):
        self.stores: dict[str, dict] = {}
        self._cols: dict[str, FakeCollection] = {}

    def collection(self, name):
        if name not in self._cols:
            self._cols[name] = FakeCollection(self.stores.setdefault(name, {}))
        return self._cols[name]

    def data(self, name) -> dict:
        return self.stores.setdefault(name, {})


def fresh_db() -> FakeClient:
    """Har test uchun toza soxta Firestore ulaydi."""
    client = FakeClient()
    db._client = client
    db._lang_cache.clear()
    return client


class FakeBot:
    """send_message chaqiruvlarini yozib boradi. blocked/failing to'plamlari xatolikni taqlid qiladi."""

    def __init__(self):
        self.sent: list[dict] = []
        self.blocked: set[int] = set()
        self.failing: set[int] = set()

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        if chat_id in self.blocked:
            raise TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")
        if chat_id in self.failing:
            raise RuntimeError("tarmoq xatosi")
        self.sent.append({"chat_id": chat_id, "text": text, "markup": reply_markup})

    def to(self, chat_id) -> list[dict]:
        return [m for m in self.sent if m["chat_id"] == chat_id]


def buttons(markup) -> list[str]:
    """Tugmalarning callback_data ro'yxati (url tugmalar o'tkazib yuboriladi)."""
    if markup is None:
        return []
    return [b.callback_data for row in markup.inline_keyboard for b in row if getattr(b, "callback_data", None)]


def ago(**kw):
    return now_utc() - timedelta(**kw)


def ahead(**kw):
    return now_utc() + timedelta(**kw)


def sample_gym(**over) -> dict:
    gym = {
        "id": "g1", "name": "Power Gym", "district": "Chilonzor", "district_key": "chilonzor", "district_ok": True,
        "address": "Chilonzor 9", "lat": 41.27, "lon": 69.2, "phone": "+998901000001", "tg_username": "",
        "owner_tg_id": 2002, "hours": "06-23", "prices": "1 oy: 350 000 so'm", "price_month": 350000,
        "amenities": "kardio", "photos": [], "free_trial": True, "notes": "", "active": True,
    }
    gym.update(over)
    return gym


def sample_lead(**over) -> dict:
    now = now_utc()
    lead = {
        "lead_id": "abc123", "gym_id": "g1", "gym": "Power Gym", "name": "Ali", "phone": "+998901234567",
        "phone_verified": True, "goal": "lose", "type": "trial", "visit_day": now.date().isoformat(),
        "visit_slot": "day", "lang": "uz", "source": "browse", "tg_id": 5005, "tg_username": "ali",
        "owner_id": 2002, "status": "new", "created_at": now, "status_at": now, "sla_at": None,
        "remind_at": None, "confirm_at": None, "owner_notified": True, "sheet_ok": True,
    }
    lead.update(over)
    return lead


def set_gyms(gyms: list[dict]) -> None:
    """sheets keshiga zallarni to'g'ridan-to'g'ri qo'yadi (Google Sheets'ga murojaat qilinmaydi)."""
    import time

    sheets._cache.update(gyms=list(gyms), problems=[], loaded=True, ts=time.time() + 10**6)
