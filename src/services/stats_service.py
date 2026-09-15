from datetime import date
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.models import AnswerHistory, User

class StatsService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_current_stats(self) -> dict:
        users_count = (await self.session.execute(select(func.count()).select_from(User))).scalar() or 0
        total_answers = (await self.session.execute(select(func.count()).select_from(AnswerHistory))).scalar() or 0
        correct_answers = (
            await self.session.execute(
                select(func.count())
                .select_from(AnswerHistory)
                .where(AnswerHistory.is_correct == True)  # noqa: E712
            )
        ).scalar() or 0

        avg_accuracy = (correct_answers / total_answers * 100) if total_answers else 0.0

        return {
            "date": date.today().strftime("%d.%m.%Y"),
            "users_count": users_count,
            "avg_accuracy": avg_accuracy,
            "total_answers": total_answers,
            "correct_answers": correct_answers,
        }

    async def clear_all_data_except_tasks(self) -> dict:
        ah_result = await self.session.execute(delete(AnswerHistory))
        u_result = await self.session.execute(delete(User))
        await self.session.commit()

        return {
            "answer_history": ah_result.rowcount,
            "users": u_result.rowcount,
        }