"""
Персистентный менеджер FSM-состояний на основе PostgreSQL и кэша в памяти
"""

import json
import logging
from sqlalchemy import select, delete
from src.database.database import get_session
from src.database.models import UserStateModel

logger = logging.getLogger(__name__)


CACHE_MAX_SIZE = 10_000


class FSMStateManager:
    def __init__(self):
        self._cache: dict[int, dict] = {}

    def _cache_put(self, vk_id: int, data: dict):
        """Простой кэш с ограничением размера, чтобы память не росла бесконечно."""
        if len(self._cache) >= CACHE_MAX_SIZE and vk_id not in self._cache:
            # Удаляем произвольную (первую) запись
            self._cache.pop(next(iter(self._cache)))
        self._cache[vk_id] = data

    async def get_data(self, vk_id: int) -> dict:
        if vk_id in self._cache:
            return self._cache[vk_id]

        try:
            async with get_session() as session:
                query = select(UserStateModel).where(UserStateModel.vk_id == vk_id)
                res = await session.execute(query)
                obj = res.scalar_one_or_none()
                if obj and obj.data_json:
                    data = json.loads(obj.data_json)
                    self._cache_put(vk_id, data)
                    return data
        except Exception as e:
            logger.error(f"Error reading state for {vk_id}: {e}")

        return {}

    async def get_state(self, vk_id: int) -> str | None:
        data = await self.get_data(vk_id)
        return data.get("state")

    async def set_state(self, vk_id: int, state: str, **kwargs):
        data = {"state": state, **kwargs}
        self._cache_put(vk_id, data)
        data_json = json.dumps(data, ensure_ascii=False)

        try:
            async with get_session() as session:
                query = select(UserStateModel).where(UserStateModel.vk_id == vk_id)
                res = await session.execute(query)
                obj = res.scalar_one_or_none()

                if obj:
                    obj.state = state
                    obj.data_json = data_json
                else:
                    obj = UserStateModel(vk_id=vk_id, state=state, data_json=data_json)
                    session.add(obj)

                await session.commit()
        except Exception as e:
            logger.error(f"Error setting state for {vk_id}: {e}")

    async def update_data(self, vk_id: int, **kwargs):
        data = await self.get_data(vk_id)
        data.update(kwargs)
        state = data.get("state")
        await self.set_state(vk_id, state, **data)

    async def clear_state(self, vk_id: int):
        self._cache.pop(vk_id, None)
        try:
            async with get_session() as session:
                stmt = delete(UserStateModel).where(UserStateModel.vk_id == vk_id)
                await session.execute(stmt)
                await session.commit()
        except Exception as e:
            logger.error(f"Error clearing state for {vk_id}: {e}")

    async def clear_all(self):
        """Полностью очищает все FSM-состояния (используется после удаления пользователей)."""
        self._cache.clear()
        try:
            async with get_session() as session:
                stmt = delete(UserStateModel)
                await session.execute(stmt)
                await session.commit()
        except Exception as e:
            logger.error(f"Error clearing all FSM states: {e}")


state_manager = FSMStateManager()