import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    bot_token: str = os.getenv("BOT_TOKEN", "")
    port: int = int(os.getenv("PORT", "8080"))
    database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bot.db")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # --- Настройки пула соединений БД (актуальны для PostgreSQL) ---
    # Явный размер пула вместо дефолтных 5+10=15. Значения безопасны по умолчанию.
    db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "10"))
    db_max_overflow: int = int(os.getenv("DB_MAX_OVERFLOW", "20"))
    db_pool_timeout: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    db_pool_recycle: int = int(os.getenv("DB_POOL_RECYCLE", "1800"))

    # --- Защита от перегрузки ---
    # Максимум одновременно обрабатываемых апдейтов (семафор в main.py).
    max_concurrent_updates: int = int(os.getenv("MAX_CONCURRENT_UPDATES", "64"))
    # Скорость исходящих сообщений VK (msg/сек) и размер залпа — сглаживают лимиты VK.
    vk_send_rate: float = float(os.getenv("VK_SEND_RATE", "20"))
    vk_send_burst: float = float(os.getenv("VK_SEND_BURST", "20"))

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        # Разрешаем лишние переменные в .env (например, VK_USER_TOKEN для
        # служебного скрипта upload_tasks.py) — иначе pydantic_settings
        # падает с ошибкой extra_forbidden.
        extra = "ignore"

settings = Settings()


def validate_settings() -> None:
    """Быстро падает с понятным сообщением при некорректной конфигурации."""
    if not settings.bot_token:
        raise RuntimeError(
            "BOT_TOKEN is not set. Provide it in .env or environment variables."
        )