import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    bot_token: str = os.getenv("BOT_TOKEN", "")
    port: int = int(os.getenv("PORT", "8080"))
    database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bot.db")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

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