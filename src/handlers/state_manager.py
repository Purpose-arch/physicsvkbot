"""Persistent FSM state manager with a bounded read-through cache."""

import json
import logging

from sqlalchemy import delete, select
from src.database.database import get_session
from src.database.models import UserStateModel

logger = logging.getLogger(__name__)
CACHE_MAX_SIZE = 10_000


class FSMStateManager:
    def __init__(self):
        self._cache: dict[int, dict] = {}

    def _cache_put(self, vk_id: int, data: dict):
        if len(self._cache) >= CACHE_MAX_SIZE and vk_id not in self._cache:
            self._cache.pop(next(iter(self._cache)))
        self._cache[vk_id] = data

    async def get_data(self, vk_id: int) -> dict:
        if vk_id in self._cache:
            return self._cache[vk_id]
        try:
            async with get_session() as session:
                result = await session.execute(select(UserStateModel).where(UserStateModel.vk_id == vk_id))
                state = result.scalar_one_or_none()
                if state and state.data_json:
                    data = state.data_json if isinstance(state.data_json, dict) else json.loads(state.data_json)
                    self._cache_put(vk_id, data)
                    return data
        except Exception:
            logger.exception("Failed to read FSM state for VK ID %s", vk_id)
        return {}

    async def get_state(self, vk_id: int) -> str | None:
        return (await self.get_data(vk_id)).get("state")

    async def set_state(self, vk_id: int, state: str, **kwargs):
        data = {"state": state, **kwargs}
        data_json = json.dumps(data, ensure_ascii=False)
        try:
            async with get_session() as session:
                result = await session.execute(select(UserStateModel).where(UserStateModel.vk_id == vk_id))
                state_row = result.scalar_one_or_none()
                if state_row is None:
                    state_row = UserStateModel(vk_id=vk_id)
                    session.add(state_row)
                state_row.state = state
                state_row.data_json = data_json
                await session.commit()
            self._cache_put(vk_id, data)
        except Exception:
            logger.exception("Failed to save FSM state for VK ID %s", vk_id)

    async def clear_state(self, vk_id: int):
        self._cache.pop(vk_id, None)
        try:
            async with get_session() as session:
                await session.execute(delete(UserStateModel).where(UserStateModel.vk_id == vk_id))
                await session.commit()
        except Exception:
            logger.exception("Failed to clear FSM state for VK ID %s", vk_id)


state_manager = FSMStateManager()