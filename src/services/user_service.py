"""
Сервис пользователей с кэшированием в памяти (0 мс на чтение из кэша).

В кэше хранятся простые данные (dict), а НЕ ORM-объекты. Кэширование ORM-объектов
приводит к DetachedInstanceError после закрытия исходной сессии.
"""

from typing import Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from src.config import settings
from src.database.models import User

# Кэшируемые скалярные поля пользователя (безопасно вне сессии)
USER_CACHE: dict[int, dict] = {}

_CACHED_FIELDS = ("vk_id", "first_name", "grade", "solved_tasks", "is_admin")


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
            USER_CACHE[vk_id] = _cache_from_user(user)
        return user

    async def get_or_create_user(self, vk_id: int, first_name: str = "Ученик", grade: int = 11) -> User:
        """Возвращает пользователя из БД (сессия-привязанный объект), используя кэш
        только для быстрых проверок полей, например is_admin."""
        cached = USER_CACHE.get(vk_id)

        if cached is not None and vk_id in settings.admin_ids_list and not cached.get("is_admin"):
            # Повышаем до админа в БД и кэше, не трогая ORM-объекты
            await self.session.execute(
                update(User).where(User.vk_id == vk_id).values(is_admin=True)
            )
            await self.session.commit()
            cached["is_admin"] = True

        query = select(User).where(User.vk_id == vk_id)
        result = await self.session.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            is_admin = vk_id in settings.admin_ids_list
            user = User(
                vk_id=vk_id,
                first_name=first_name,
                grade=grade,
                solved_tasks="",
                is_admin=is_admin,
            )
            self.session.add(user)
            await self.session.commit()
            await self.session.refresh(user)
        elif vk_id in settings.admin_ids_list and not user.is_admin:
            user.is_admin = True
            await self.session.commit()

        USER_CACHE[vk_id] = _cache_from_user(user)
        return user

    async def create_user(self, vk_id: int, first_name: str, grade: int) -> User:
        is_admin = vk_id in settings.admin_ids_list
        user = User(
            vk_id=vk_id,
            first_name=first_name,
            grade=grade,
            solved_tasks="",
            is_admin=is_admin,
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)

        USER_CACHE[vk_id] = _cache_from_user(user)
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
        USER_CACHE[vk_id] = _cache_from_user(user)

    async def sync_admin_status(self, vk_id: int) -> bool:
        should_be_admin = vk_id in settings.admin_ids_list
        query = (
            update(User)
            .where(User.vk_id == vk_id)
            .values(is_admin=should_be_admin)
        )
        await self.session.execute(query)
        await self.session.commit()

        if vk_id in USER_CACHE:
            USER_CACHE[vk_id]["is_admin"] = should_be_admin

        return should_be_admin


def invalidate_user_cache(vk_id: int):
    """Удаляет пользователя из кэша (например, после удаления пользователя)."""
    USER_CACHE.pop(vk_id, None)