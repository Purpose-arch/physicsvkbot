"""Persistence operations for bot users."""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.models import User


class UserService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_by_vk_id(self, vk_id: int) -> Optional[User]:
        result = await self.session.execute(select(User).where(User.vk_id == vk_id))
        return result.scalar_one_or_none()

    async def ensure_user(self, vk_id: int, first_name: str = "Ученик", grade: int = 11) -> tuple[User, bool]:
        """Return the user, inserting it if necessary, without committing.

        The insert runs in a savepoint.  If two requests create the same VK user
        concurrently, PostgreSQL resolves the unique constraint and the losing
        transaction can safely load the committed row.
        """
        user = await self.get_user_by_vk_id(vk_id)
        if user is not None:
            return user, False

        try:
            async with self.session.begin_nested():
                user = User(vk_id=vk_id, first_name=first_name, grade=grade)
                self.session.add(user)
                await self.session.flush()
            return user, True
        except IntegrityError:
            user = await self.get_user_by_vk_id(vk_id)
            if user is None:
                raise
            return user, False

    async def get_or_create_user(self, vk_id: int, first_name: str = "Ученик", grade: int = 11) -> User:
        user, created = await self.ensure_user(vk_id, first_name, grade)
        if created:
            await self.session.commit()
        return user
