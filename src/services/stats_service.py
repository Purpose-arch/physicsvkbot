"""Aggregate statistics queries for the bot."""

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import AnswerHistory, Task, User


class StatsService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _count(self, query) -> int:
        return (await self.session.execute(query)).scalar() or 0

    async def get_bot_stats(self) -> dict:
        today_start = datetime.combine(date.today(), datetime.min.time())
        week_ago = datetime.now() - timedelta(days=7)
        users_count = await self._count(select(func.count()).select_from(User))
        total_answers = await self._count(select(func.count()).select_from(AnswerHistory))
        correct_answers = await self._count(select(func.count()).select_from(AnswerHistory).where(AnswerHistory.is_correct.is_(True)))
        tasks_total = await self._count(select(func.count()).select_from(Task).where(Task.is_active.is_(True)))
        new_users_today = await self._count(select(func.count()).select_from(User).where(User.created_at >= today_start))
        active_users_week = await self._count(select(func.count(func.distinct(AnswerHistory.user_id))).where(AnswerHistory.answered_at >= week_ago))
        answers_today = await self._count(select(func.count()).select_from(AnswerHistory).where(AnswerHistory.answered_at >= today_start))
        accuracy = correct_answers / total_answers * 100 if total_answers else 0.0
        return {
            "date": date.today().strftime("%d.%m.%Y"), "users_count": users_count,
            "new_users_today": new_users_today, "active_users_week": active_users_week,
            "total_answers": total_answers, "correct_answers": correct_answers,
            "incorrect_answers": total_answers - correct_answers, "answers_today": answers_today,
            "avg_accuracy": accuracy, "tasks_total": tasks_total,
        }

    async def get_accuracy_by_task_number(self) -> list[dict]:
        rows = (await self.session.execute(
            select(Task.task_number.label("number"), func.count(AnswerHistory.id).label("total"), func.count(AnswerHistory.id).filter(AnswerHistory.is_correct.is_(True)).label("correct"))
            .join(Task, AnswerHistory.task_id == Task.id).group_by(Task.task_number).order_by(Task.task_number)
        )).all()
        return [{"number": row.number, "total": row.total or 0, "correct": row.correct or 0, "accuracy": (row.correct / row.total * 100 if row.total else 0.0)} for row in rows]

    async def get_top_users(self, limit: int = 5) -> list[dict]:
        rows = (await self.session.execute(
            select(User.first_name.label("first_name"), func.count(AnswerHistory.id).label("total"), func.count(AnswerHistory.id).filter(AnswerHistory.is_correct.is_(True)).label("correct"))
            .join(User, AnswerHistory.user_id == User.id).group_by(User.id, User.first_name)
            .order_by(func.count(AnswerHistory.id).desc()).limit(limit)
        )).all()
        return [{"first_name": row.first_name, "total": row.total or 0, "correct": row.correct or 0, "accuracy": (row.correct / row.total * 100 if row.total else 0.0)} for row in rows]