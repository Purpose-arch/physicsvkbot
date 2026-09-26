"""Persistence operations for bot users."""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.models import User


class UserService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_by_vk_id(self, vk_id: int) -> Optional[User]:
        result = await self.session.execute(select(User).where(User.vk_id == vk_id))
        return result.scalar_one_or_none()

    async def ensure_user(self, vk_id: int, first_name: str = "Ученик", grade: int = 11) -> tuple[User, bool]:
        """Upsert and return the current database row without committing."""
        dialect = self.session.bind.dialect.name
        insert = postgresql_insert(User) if dialect == "postgresql" else sqlite_insert(User)
        statement = insert.values(vk_id=vk_id, first_name=first_name, grade=grade)
        statement = statement.on_conflict_do_update(
            index_elements=["vk_id"],
            set_={"vk_id": statement.excluded.vk_id},
        ).returning(User.id)
        user_id = await self.session.scalar(statement)
        user = await self.get_user_by_vk_id(vk_id)
        if user is None or user.id != user_id:
            raise RuntimeError(f"Failed to resolve user by VK ID {vk_id}")
        return user, False

    async def get_or_create_user(self, vk_id: int, first_name: str = "Ученик", grade: int = 11) -> User:
        user, _ = await self.ensure_user(vk_id, first_name, grade)
        await self.session.commit()
        return user
