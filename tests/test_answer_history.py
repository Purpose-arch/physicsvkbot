import unittest

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.database.models import AnswerHistory, Base, Task, User, UserSolvedTask
from src.services.task_service import TaskService
from src.services.user_service import UserService


class AnswerHistoryPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.sessions() as session:
            task = Task(task_number=1, answer="9.8")
            session.add(task)
            await session.commit()
            self.task_id = task.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_new_user_wrong_answer_is_recorded_with_valid_parent(self):
        async with self.sessions() as session:
            await TaskService(session).record_answer(12345, self.task_id, False)

        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.vk_id == 12345))
            history = await session.scalar(select(AnswerHistory))
            solved_count = await session.scalar(
                select(func.count()).select_from(UserSolvedTask)
            )

        self.assertIsNotNone(user)
        self.assertIsNotNone(history)
        self.assertEqual(history.user_id, user.id)
        self.assertEqual(history.task_id, self.task_id)
        self.assertFalse(history.is_correct)
        self.assertEqual(solved_count, 0)

    async def test_correct_answer_records_history_and_progress(self):
        async with self.sessions() as session:
            await TaskService(session).record_answer(67890, self.task_id, True)

        async with self.sessions() as session:
            history_count = await session.scalar(
                select(func.count()).select_from(AnswerHistory)
            )
            solved_count = await session.scalar(
                select(func.count()).select_from(UserSolvedTask)
            )
            user = await session.scalar(select(User).where(User.vk_id == 67890))
            solved = await session.get(
                UserSolvedTask, {"user_id": user.id, "task_id": self.task_id}
            )

        self.assertEqual(history_count, 1)
        self.assertEqual(solved_count, 1)
        self.assertIsNotNone(solved)

    async def test_invalid_task_rolls_back_new_user_and_history(self):
        async with self.sessions() as session:
            with self.assertRaises(ValueError):
                await TaskService(session).record_answer(11111, 999999, False)

        async with self.sessions() as session:
            user_count = await session.scalar(select(func.count()).select_from(User))
            history_count = await session.scalar(
                select(func.count()).select_from(AnswerHistory)
            )

        self.assertEqual(user_count, 0)
        self.assertEqual(history_count, 0)

    async def test_repeated_creation_reuses_same_user(self):
        async with self.sessions() as session:
            first = await UserService(session).get_or_create_user(22222)

        async with self.sessions() as session:
            second = await UserService(session).get_or_create_user(22222)
            user_count = await session.scalar(select(func.count()).select_from(User))

        self.assertEqual(first.id, second.id)
        self.assertEqual(user_count, 1)


if __name__ == "__main__":
    unittest.main()