"""
Сервис заданий: управление задачами по физике с полностью отказоустойчивыми запросами
"""

import logging
from typing import List, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.models import AnswerHistory, Task

logger = logging.getLogger(__name__)


class TaskService:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _parse_solved_ids(self, solved_tasks: str) -> set[int]:
        """Разбирает строку solved_tasks в множество целочисленных ID заданий"""
        if not solved_tasks or not solved_tasks.strip():
            return set()
        return {int(x.strip()) for x in solved_tasks.split(",") if x.strip().isdigit()}

    async def get_random_task(self, solved_tasks: str) -> Optional[Task]:
        """Возвращает случайное задание, исключая уже решённые."""
        try:
            solved_ids = self._parse_solved_ids(solved_tasks)
            query = select(Task)
            if solved_ids:
                query = query.where(~Task.id.in_(solved_ids))

            query = query.order_by(func.random()).limit(1)
            result = await self.session.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error getting random task: {e}")
            return None

    async def get_task_by_number(self, task_number: int, solved_tasks: str) -> Optional[Task]:
        """Возвращает случайное задание по номеру, исключая решённые."""
        try:
            solved_ids = self._parse_solved_ids(solved_tasks)
            query = select(Task).where(Task.task_number == task_number)
            if solved_ids:
                query = query.where(~Task.id.in_(solved_ids))

            query = query.order_by(func.random()).limit(1)
            result = await self.session.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error getting task by number {task_number}: {e}")
            return None

    async def get_task_number_stats(self, task_number: int, vk_id: int) -> dict:
        """Возвращает статистику по конкретному номеру задания для пользователя."""
        try:
            total_query = select(func.count()).where(Task.task_number == task_number)
            total_result = await self.session.execute(total_query)
            total = total_result.scalar() or 0

            if total == 0:
                return {"total": 0, "solved": 0}

            solved_query = (
                select(func.count())
                .select_from(AnswerHistory)
                .join(Task)
                .where(AnswerHistory.user_id == vk_id, Task.task_number == task_number)
            )
            solved_result = await self.session.execute(solved_query)
            solved = solved_result.scalar() or 0

            return {"total": total, "solved": solved}
        except Exception as e:
            logger.error(f"Error getting task stats for number {task_number}: {e}")
            return {"total": 0, "solved": 0}

    async def add_tasks_batch(self, tasks: List[Task]) -> int:
        """Сохраняет список заданий одной транзакцией (быстрая массовая загрузка)."""
        try:
            self.session.add_all(tasks)
            await self.session.commit()
            return len(tasks)
        except Exception as e:
            logger.error(f"Error adding tasks batch: {e}")
            await self.session.rollback()
            raise

    async def add_answer_history(self, vk_id: int, task_id: int, is_correct: bool):
        """Безопасно добавляет ответ в историю."""
        try:
            history = AnswerHistory(user_id=vk_id, task_id=task_id, is_correct=is_correct)
            self.session.add(history)
            await self.session.commit()
        except Exception as e:
            logger.error(f"Error adding answer history: {e}")
            await self.session.rollback()

    async def get_user_stats(self, vk_id: int) -> dict:
        """
        Возвращает детальную статистику пользователя с отказоустойчивой разбивкой по номерам.
        """
        try:
            query = (
                select(AnswerHistory)
                .where(AnswerHistory.user_id == vk_id)
                .order_by(AnswerHistory.answered_at.desc())
            )
            result = await self.session.execute(query)
            history: List[AnswerHistory] = result.scalars().all()

            total = len(history)
            correct = sum(1 for h in history if h.is_correct)
            incorrect = total - correct

            task_ids = list({h.task_id for h in history if h.task_id})
            breakdown: dict[int, dict] = {}

            if task_ids:
                tasks_query = select(Task).where(Task.id.in_(task_ids))
                tasks_result = await self.session.execute(tasks_query)
                tasks_map = {t.id: t.task_number for t in tasks_result.scalars().all()}

                for h in history:
                    num = tasks_map.get(h.task_id, 0)
                    if num > 0:
                        if num not in breakdown:
                            breakdown[num] = {"total": 0, "correct": 0}
                        breakdown[num]["total"] += 1
                        if h.is_correct:
                            breakdown[num]["correct"] += 1

            return {
                "total": total,
                "correct": correct,
                "incorrect": incorrect,
                "breakdown": breakdown,
            }
        except Exception as e:
            logger.error(f"Error calculating user stats for VK ID {vk_id}: {e}")
            return {"total": 0, "correct": 0, "incorrect": 0, "breakdown": {}}