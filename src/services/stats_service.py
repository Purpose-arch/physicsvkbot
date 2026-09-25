from datetime import date, datetime, timedelta
from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.models import AnswerHistory, Task, User


class StatsService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _count(self, query) -> int:
        return (await self.session.execute(query)).scalar() or 0

    async def get_bot_stats(self) -> dict:
        """Базовые агрегаты для текстовой части общей статистики."""
        today_start = datetime.combine(date.today(), datetime.min.time())
        week_ago = datetime.now() - timedelta(days=7)

        users_count = await self._count(select(func.count()).select_from(User))
        total_answers = await self._count(select(func.count()).select_from(AnswerHistory))
        correct_answers = await self._count(
            select(func.count())
            .select_from(AnswerHistory)
            .where(AnswerHistory.is_correct == True)  # noqa: E712
        )
        tasks_total = await self._count(select(func.count()).select_from(Task))
        numbers_filled = await self._count(
            select(func.count(func.distinct(Task.task_number)))
        )
        new_users_today = await self._count(
            select(func.count()).select_from(User).where(User.created_at >= today_start)
        )
        active_users_week = await self._count(
            select(func.count(func.distinct(AnswerHistory.user_id))).where(
                AnswerHistory.answered_at >= week_ago
            )
        )
        answers_today = await self._count(
            select(func.count())
            .select_from(AnswerHistory)
            .where(AnswerHistory.answered_at >= today_start)
        )

        avg_accuracy = (correct_answers / total_answers * 100) if total_answers else 0.0

        return {
            "date": date.today().strftime("%d.%m.%Y"),
            "users_count": users_count,
            "new_users_today": new_users_today,
            "active_users_week": active_users_week,
            "total_answers": total_answers,
            "correct_answers": correct_answers,
            "incorrect_answers": total_answers - correct_answers,
            "answers_today": answers_today,
            "avg_accuracy": avg_accuracy,
            "tasks_total": tasks_total,
            "numbers_filled": numbers_filled,
        }

    async def get_daily_answers(self, days: int = 14) -> list[dict]:
        """Количество ответов и верных из них по дням за последние `days` дней."""
        since = datetime.combine(date.today() - timedelta(days=days - 1), datetime.min.time())
        day = func.date(AnswerHistory.answered_at)
        query = (
            select(
                day.label("day"),
                func.count().label("total"),
                func.sum(func.cast(AnswerHistory.is_correct, Integer)).label("correct"),
            )
            .where(AnswerHistory.answered_at >= since)
            .group_by(day)
            .order_by(day)
        )
        rows = (await self.session.execute(query)).all()
        by_day = {str(r.day): (r.total or 0, r.correct or 0) for r in rows}

        result = []
        for offset in range(days):
            d = date.today() - timedelta(days=days - 1 - offset)
            key = d.isoformat()
            total, correct = by_day.get(key, (0, 0))
            result.append({"date": d, "total": total, "correct": correct})
        return result

    async def get_daily_new_users(self, days: int = 14) -> list[dict]:
        """Количество новых пользователей по дням за последние `days` дней."""
        since = datetime.combine(date.today() - timedelta(days=days - 1), datetime.min.time())
        day = func.date(User.created_at)
        query = (
            select(day.label("day"), func.count().label("total"))
            .where(User.created_at >= since)
            .group_by(day)
            .order_by(day)
        )
        rows = (await self.session.execute(query)).all()
        by_day = {str(r.day): (r.total or 0) for r in rows}

        result = []
        for offset in range(days):
            d = date.today() - timedelta(days=days - 1 - offset)
            result.append({"date": d, "total": by_day.get(d.isoformat(), 0)})
        return result

    async def get_accuracy_by_task_number(self) -> list[dict]:
        """Точность по номерам заданий (только номера, где есть ответы)."""
        query = (
            select(
                Task.task_number.label("number"),
                func.count().label("total"),
                func.sum(func.cast(AnswerHistory.is_correct, Integer)).label("correct"),
            )
            .join(Task, AnswerHistory.task_id == Task.id)
            .group_by(Task.task_number)
            .order_by(Task.task_number)
        )
        rows = (await self.session.execute(query)).all()
        result = []
        for r in rows:
            total = r.total or 0
            correct = r.correct or 0
            result.append(
                {
                    "number": r.number,
                    "total": total,
                    "correct": correct,
                    "accuracy": (correct / total * 100) if total else 0.0,
                }
            )
        return result

    async def get_top_users(self, limit: int = 5) -> list[dict]:
        """Топ пользователей по количеству ответов с их точностью."""
        query = (
            select(
                User.first_name.label("first_name"),
                func.count().label("total"),
                func.sum(func.cast(AnswerHistory.is_correct, Integer)).label("correct"),
            )
            .join(User, AnswerHistory.user_id == User.vk_id)
            .group_by(User.vk_id, User.first_name)
            .order_by(func.count().desc())
            .limit(limit)
        )
        rows = (await self.session.execute(query)).all()
        result = []
        for r in rows:
            total = r.total or 0
            correct = r.correct or 0
            result.append(
                {
                    "first_name": r.first_name,
                    "total": total,
                    "correct": correct,
                    "accuracy": (correct / total * 100) if total else 0.0,
                }
            )
        return result