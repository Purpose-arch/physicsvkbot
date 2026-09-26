"""Persistence and analytics operations for practice tasks."""

import logging
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import AnswerHistory, Task, User, UserSolvedTask

logger = logging.getLogger(__name__)


class TaskService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_random_task(self, user_id: int, task_number: int | None = None) -> Optional[Task]:
        solved_ids = select(UserSolvedTask.task_id).where(UserSolvedTask.user_id == user_id)
        conditions = [Task.is_active.is_(True), ~Task.id.in_(solved_ids)]
        if task_number is not None:
            conditions.append(Task.task_number == task_number)
        query = select(Task).where(and_(*conditions)).order_by(func.random()).limit(1)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def get_task_number_stats(self, task_number: int, user_id: int) -> dict:
        total = await self.session.scalar(select(func.count()).select_from(Task).where(Task.task_number == task_number, Task.is_active.is_(True)))
        solved = await self.session.scalar(
            select(func.count(UserSolvedTask.task_id))
            .join(Task, Task.id == UserSolvedTask.task_id)
            .where(UserSolvedTask.user_id == user_id, Task.task_number == task_number)
        )
        return {"total": total or 0, "solved": solved or 0}

    async def record_answer(self, user: User, task_id: int, is_correct: bool) -> None:
        """Record an attempt and progress in one transaction; safe to retry."""
        self.session.add(AnswerHistory(user_id=user.id, task_id=task_id, is_correct=is_correct))
        if is_correct:
            statement = insert(UserSolvedTask).values(user_id=user.id, task_id=task_id)
            statement = statement.on_conflict_do_nothing(index_elements=["user_id", "task_id"])
            await self.session.execute(statement)
        await self.session.commit()

    async def get_user_stats(self, user_id: int) -> dict:
        result = await self.session.execute(
            select(
                func.count(AnswerHistory.id),
                func.count(AnswerHistory.id).filter(AnswerHistory.is_correct.is_(True)),
                func.count(func.distinct(UserSolvedTask.task_id)),
            )
            .select_from(AnswerHistory)
            .outerjoin(UserSolvedTask, UserSolvedTask.user_id == AnswerHistory.user_id)
            .where(AnswerHistory.user_id == user_id)
        )
        total, correct, solved = result.one()
        breakdown_rows = await self.session.execute(
            select(Task.task_number, func.count(AnswerHistory.id), func.count(AnswerHistory.id).filter(AnswerHistory.is_correct.is_(True)))
            .join(Task, Task.id == AnswerHistory.task_id)
            .where(AnswerHistory.user_id == user_id)
            .group_by(Task.task_number)
            .order_by(Task.task_number)
        )
        breakdown = {number: {"total": total, "correct": correct} for number, total, correct in breakdown_rows}
        return {"total": total or 0, "correct": correct or 0, "incorrect": (total or 0) - (correct or 0), "solved": solved or 0, "breakdown": breakdown}