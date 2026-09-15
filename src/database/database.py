"""
Database connection and session management for Physics EGE VK Bot
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
    """Engine kwargs depend on the underlying driver."""
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
    Get database session with automatic rollback on error to prevent deadlocks
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
    """
    Create all tables in the database and pre-warm DB connections
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Прогрев пула соединений БД для устранения задержек при первом запросе
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            logger.info("Database connection pool successfully pre-warmed!")
    except Exception as e:
        logger.warning(f"Failed to pre-warm database connection pool: {e}")