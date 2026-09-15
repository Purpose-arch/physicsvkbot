"""
User service for managing users with 0ms in-memory caching
"""

from typing import Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from src.config import settings
from src.database.models import User

USER_CACHE: dict[int, User] = {}


class UserService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_by_vk_id(self, vk_id: int) -> Optional[User]:
        if vk_id in USER_CACHE:
            return USER_CACHE[vk_id]

        query = select(User).where(User.vk_id == vk_id)
        result = await self.session.execute(query)
        user = result.scalar_one_or_none()
        if user:
            USER_CACHE[vk_id] = user
        return user

    async def get_or_create_user(self, vk_id: int, first_name: str = "Ученик", grade: int = 11) -> User:
        """Мгновенно возвращает или создает пользователя с кэшированием в памяти"""
        if vk_id in USER_CACHE:
            cached_user = USER_CACHE[vk_id]
            if vk_id in settings.admin_ids_list and not cached_user.is_admin:
                cached_user.is_admin = True
            return cached_user

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
        else:
            if vk_id in settings.admin_ids_list and not user.is_admin:
                user.is_admin = True
                await self.session.commit()

        USER_CACHE[vk_id] = user
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

        USER_CACHE[vk_id] = user
        return user

    async def add_solved_task(self, vk_id: int, task_id: int):
        user = await self.get_or_create_user(vk_id)

        solved_tasks = user.solved_tasks or ""
        solved_task_ids = set(solved_tasks.split(",")) if solved_tasks else set()
        solved_task_ids.add(str(task_id))

        new_solved_tasks = ",".join(solved_task_ids)
        user.solved_tasks = new_solved_tasks
        USER_CACHE[vk_id] = user

        query = (
            update(User)
            .where(User.vk_id == vk_id)
            .values(solved_tasks=new_solved_tasks)
        )
        await self.session.execute(query)
        await self.session.commit()

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
            USER_CACHE[vk_id].is_admin = should_be_admin

        return should_be_admin