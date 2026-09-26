"""
Подключение к базе данных и управление сессиями
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.database.models import Base

logger = logging.getLogger(__name__)

# Асинхронный движок с прогревом пула и без кэширования подготовленных запросов
def _build_engine_kwargs() -> dict:
    """Параметры движка зависят от используемого драйвера."""
    kwargs: dict = {
        "echo": False,
        "future": True,
        "pool_pre_ping": True,  # Автоматическая проверка активности соединения
    }
    if settings.database_url.startswith("postgresql"):
        # Отключаем подготовленные выражения asyncpg (важно за pgbouncer)
        kwargs["connect_args"] = {"statement_cache_size": 0}
    return kwargs


engine = create_async_engine(settings.database_url, **_build_engine_kwargs())

# Фабрика асинхронных сессий
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Возвращает сессию БД с автоматическим rollback при ошибке для предотвращения блокировок
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def create_tables():
    """Создать/обновить схему базы и прогреть пул соединений.

    ``metadata.create_all`` не изменяет уже созданные таблицы. Поэтому после
    create_all выполняются безопасные идемпотентные миграции для добавленных
    позднее полей (в частности, ``tasks.is_active`` и
    ``user_fsm_states.updated_at``).
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                """
                ALTER TABLE tasks
                ADD COLUMN IF NOT EXISTS is_active BOOLEAN
                NOT NULL DEFAULT TRUE
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE user_fsm_states
                ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ
                NOT NULL DEFAULT NOW()
                """
            )
        )

    # Прогрев пула соединений БД для устранения задержек при первом запросе
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            logger.info("Database connection pool successfully pre-warmed!")
    except Exception as e:
        logger.warning(f"Failed to pre-warm database connection pool: {e}")