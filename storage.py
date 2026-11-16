"""FSM holatini Firestore'da saqlash: bot qayta ishga tushsa ham ariza yo'qolmaydi.

Tezlik uchun xotirada kesh bor (bitta jarayon). Firestore ishlamay qolsa, bot
to'xtab qolmaydi: holat vaqtincha xotirada saqlanadi.
"""
import logging
from typing import Any, Dict, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

import db
from utils import now_utc

log = logging.getLogger("storage")


def _doc_id(key: StorageKey) -> str:
    parts = [str(key.chat_id), str(key.user_id)]
    if key.thread_id:
        parts.append(f"t{key.thread_id}")
    if key.destiny != "default":
        parts.append(key.destiny)
    return "_".join(parts)


def _state_str(state: StateType) -> Optional[str]:
    if state is None:
        return None
    return state.state if isinstance(state, State) else str(state)


class FirestoreStorage(BaseStorage):
    def __init__(self):
        self._cache: Dict[str, dict] = {}

    async def _load(self, doc_id: str) -> dict:
        if doc_id in self._cache:
            return self._cache[doc_id]
        record = {"state": None, "data": {}}
        try:
            snap = await db.run(lambda: db.fsm_doc(doc_id).get())
            if snap.exists:
                raw = snap.to_dict() or {}
                record = {"state": raw.get("state"), "data": dict(raw.get("data") or {})}
        except Exception:
            log.exception("FSM o'qib bo'lmadi (%s)", doc_id)
        self._cache[doc_id] = record
        return record

    async def _save(self, doc_id: str, record: dict) -> None:
        self._cache[doc_id] = record
        try:
            if record["state"] is None and not record["data"]:
                await db.run(lambda: db.fsm_doc(doc_id).delete())
                self._cache.pop(doc_id, None)
            else:
                payload = {"state": record["state"], "data": record["data"], "updated_at": now_utc()}
                await db.run(lambda: db.fsm_doc(doc_id).set(payload))
        except Exception:
            log.exception("FSM saqlab bo'lmadi (%s)", doc_id)

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        doc_id = _doc_id(key)
        record = dict(await self._load(doc_id))
        record["state"] = _state_str(state)
        await self._save(doc_id, record)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        return (await self._load(_doc_id(key)))["state"]

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        doc_id = _doc_id(key)
        record = dict(await self._load(doc_id))
        record["data"] = dict(data)
        await self._save(doc_id, record)

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        return dict((await self._load(_doc_id(key)))["data"])

    async def close(self) -> None:
        self._cache.clear()
