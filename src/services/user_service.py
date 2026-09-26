"""Persistence operations for bot users."""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.models import User


class UserService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_by_vk_id(self, vk_id: int) -> Optional[User]:
        result = await self.session.execute(select(User).where(User.vk_id == vk_id))
        return result.scalar_one_or_none()

    async def get_or_create_user(self, vk_id: int, first_name: str = "Ученик", grade: int = 11) -> User:
        user = await self.get_user_by_vk_id(vk_id)
        if user is None:
            user = User(vk_id=vk_id, first_name=first_name, grade=grade)
            self.session.add(user)
            await self.session.commit()
            await self.session.refresh(user)
        return user
