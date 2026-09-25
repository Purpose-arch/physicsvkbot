"""
Сервис пользователей с кэшированием в памяти (0 мс на чтение из кэша).

В кэше хранятся простые данные (dict), а НЕ ORM-объекты. Кэширование ORM-объектов
приводит к DetachedInstanceError после закрытия исходной сессии.
"""

from typing import Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.models import User

# Кэшируемые скалярные поля пользователя (безопасно вне сессии).
# Ограничены по размеру, чтобы память не росла бесконечно.
USER_CACHE: dict[int, dict] = {}
USER_CACHE_MAX_SIZE = 10_000


def _cache_put(vk_id: int, data: dict):
    if len(USER_CACHE) >= USER_CACHE_MAX_SIZE and vk_id not in USER_CACHE:
        USER_CACHE.pop(next(iter(USER_CACHE)), None)
    USER_CACHE[vk_id] = data

_CACHED_FIELDS = ("vk_id", "first_name", "grade", "solved_tasks")


def _cache_from_user(user: User) -> dict:
    return {field: getattr(user, field) for field in _CACHED_FIELDS}


class UserService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_by_vk_id(self, vk_id: int) -> Optional[User]:
        # Всегда загружаем из БД, чтобы получить объект, привязанный к сессии
        query = select(User).where(User.vk_id == vk_id)
        result = await self.session.execute(query)
        user = result.scalar_one_or_none()
        if user:
            _cache_put(vk_id, _cache_from_user(user))
        return user

    async def get_or_create_user(self, vk_id: int, first_name: str = "Ученик", grade: int = 11) -> User:
        """Возвращает пользователя из БД (сессия-привязанный объект), используя кэш
        только для быстрых проверок полей."""
        query = select(User).where(User.vk_id == vk_id)
        result = await self.session.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            user = User(
                vk_id=vk_id,
                first_name=first_name,
                grade=grade,
                solved_tasks="",
            )
            self.session.add(user)
            await self.session.commit()
            await self.session.refresh(user)

        _cache_put(vk_id, _cache_from_user(user))
        return user

    async def create_user(self, vk_id: int, first_name: str, grade: int) -> User:
        user = User(
            vk_id=vk_id,
            first_name=first_name,
            grade=grade,
            solved_tasks="",
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)

        _cache_put(vk_id, _cache_from_user(user))
        return user

    async def add_solved_task(self, vk_id: int, task_id: int):
        user = await self.get_or_create_user(vk_id)

        solved_tasks = user.solved_tasks or ""
        solved_task_ids = set(solved_tasks.split(",")) if solved_tasks else set()
        solved_task_ids.add(str(task_id))

        new_solved_tasks = ",".join(solved_task_ids)
        user.solved_tasks = new_solved_tasks

        # Обновляем dict в кэше, а не ORM-объект
        if vk_id in USER_CACHE:
            USER_CACHE[vk_id]["solved_tasks"] = new_solved_tasks

        query = (
            update(User)
            .where(User.vk_id == vk_id)
            .values(solved_tasks=new_solved_tasks)
        )
        await self.session.execute(query)
        await self.session.commit()

        # Обновляем кэшированный снимок после коммита
        _cache_put(vk_id, _cache_from_user(user))