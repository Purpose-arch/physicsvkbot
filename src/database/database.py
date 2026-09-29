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
        # Явный размер пула вместо дефолтных 5 + overflow 10 = 15.
        # Размер подобран так, чтобы выдерживать залповую нагрузку, оставаясь
        # в рамках лимитов пулера Supabase. Все значения задаются через .env.
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
        kwargs["pool_timeout"] = settings.db_pool_timeout
        # Проверяем «свежесть» соединений по таймеру, чтобы не ловить обрывы
        # со стороны пулера Supabase на простаивающих соединениях.
        kwargs["pool_recycle"] = settings.db_pool_recycle
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


async def _legacy_user_fk_name(conn, table: str) -> str | None:
    """Имя FK-констрейнта ``user_id``, если он ещё ссылается на ``users(vk_id)``."""
    result = await conn.execute(
        text(
            """
            SELECT c.conname
              FROM pg_constraint c
              JOIN pg_class ref ON ref.oid = c.confrelid
              JOIN pg_attribute a
                ON a.attrelid = c.confrelid
               AND a.attnum = ANY (c.confkey)
             WHERE c.conrelid = to_regclass(:tbl)
               AND c.contype = 'f'
               AND ref.relname = 'users'
               AND a.attname = 'vk_id'
             LIMIT 1
            """
        ),
        {"tbl": table},
    )
    return result.scalar()


async def _migrate_legacy_user_fks(conn) -> None:
    """Перенаправить устаревший внешний ключ ``user_id`` с ``users(vk_id)`` на ``users(id)``.

    Старая схема хранила в ``*_user_id`` внешний идентификатор ВК
    (``users.vk_id``). Новый код использует внутренний первичный ключ
    ``users.id``. Миграция идемпотентна и безопасна:

    * срабатывает, только пока констрейнт ещё ссылается на ``users(vk_id)``;
    * переводит уже сохранённые значения ``vk_id -> users.id``, не трогая
      строки, где значение уже является корректным ``users.id``;
    * удаляет «осиротевшие» строки и дубли PK (для ``user_solved_tasks``),
      чтобы пересоздание FK гарантированно прошло;
    * пересоздаёт FK на ``users(id)`` с тем же ``ON DELETE CASCADE``.

    Только для PostgreSQL — на SQLite вызывается ветвление до вызова.
    """
    for table in ("answer_history", "user_solved_tasks"):
        conname = await _legacy_user_fk_name(conn, table)
        if not conname:
            continue

        # 1. Снимаем старый констрейнт, чтобы разрешить перезапись значений.
        await conn.execute(text(f'ALTER TABLE "{table}" DROP CONSTRAINT "{conname}"'))

        # 2. Переводим уже сохранённые значения vk_id -> users.id.
        #    Guard `NOT EXISTS users.id = value` оставляет нетронутыми строки,
        #    где значение уже является валидным users.id (защита от пересечения
        #    пространств id и vk_id).
        await conn.execute(
            text(
                f"""
                UPDATE "{table}" t
                   SET user_id = u.id
                  FROM users u
                 WHERE t.user_id = u.vk_id
                   AND t.user_id <> u.id
                   AND NOT EXISTS (SELECT 1 FROM users x WHERE x.id = t.user_id)
                """
            )
        )

        # 3. Удаляем строки без родителя (в норме их быть не может из-за
        #    ON DELETE CASCADE, но это гарантирует успех пересоздания FK).
        await conn.execute(
            text(
                f"""
                DELETE FROM "{table}" t
                 WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = t.user_id)
                """
            )
        )

        # 4. Для составного PK убираем возможные дубли после перемаппинга.
        if table == "user_solved_tasks":
            await conn.execute(
                text(
                    """
                    DELETE FROM user_solved_tasks a
                     USING user_solved_tasks b
                     WHERE a.ctid < b.ctid
                       AND a.user_id = b.user_id
                       AND a.task_id = b.task_id
                    """
                )
            )

        # 5. Пересоздаём FK на users(id) с тем же поведением ON DELETE CASCADE.
        await conn.execute(
            text(
                f"""
                ALTER TABLE "{table}"
                ADD CONSTRAINT {table}_user_id_fkey
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                """
            )
        )
        logger.info("Migrated legacy user_id FK on table %s (was -> users.vk_id).", table)


async def create_tables():
    """Создать/обновить схему базы и прогреть пул соединений.

    ``metadata.create_all`` не изменяет уже созданные таблицы. Поэтому после
    create_all выполняются безопасные идемпотентные миграции: добавление
    позднее введённых полей (``tasks.is_active``, ``user_fsm_states.updated_at``)
    и перенаправление устаревшего внешнего ключа ``user_id`` с ``users(vk_id)``
    на ``users(id)``.
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
        # Перенаправляем устаревший FK user_id -> users(id) (только PostgreSQL).
        if settings.database_url.startswith("postgresql"):
            await _migrate_legacy_user_fks(conn)

    # Прогрев пула соединений БД для устранения задержек при первом запросе
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            logger.info("Database connection pool successfully pre-warmed!")
    except Exception as e:
        logger.warning(f"Failed to pre-warm database connection pool: {e}")