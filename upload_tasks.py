#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Разовая заливка заданий в базу данных.

Скрипт НЕ является частью бота и не содержит команд — запускается один раз
вручную из терминала (важно использовать интерпретатор проекта из .venv,
иначе будет ModuleNotFoundError)::

    .venv/bin/python upload_tasks.py

Либо активируйте окружение и запустите ``python upload_tasks.py``.

Как работает:
  1. Проходит по всем папкам вида ``task_XX`` (поддерживается и ``taskXX``)
     внутри каталога ``OUTPUT_DIR`` (по умолчанию — папка с подготовленными
     картинками заданий).
  2. Внутри каждой папки берёт файлы ``variant_YY_ответ(ANSWER).png``.
     Из имени папки извлекается номер задания, из имени файла — ответ
     (то, что в скобках после ``ответ``). Номер варианта ``YY`` игнорируется.
     Файлы без ``ответ(...)`` в имени просто пропускаются.
  3. Загружает картинку на серверы ВКонтакте и получает строку вложения
     вида ``photo{owner_id}_{id}_{access_key}``.
  4. Сохраняет запись в существующую таблицу ``tasks`` (поля
     ``task_number``, ``answer``, ``image_attachment``). Схема БД не меняется.

Повторный запуск безопасен: уже залитые файлы пропускаются по служебному
файлу прогресса ``uploaded_tasks.json``. Чтобы залить всё заново с нуля —
удалите этот файл.

Требуется пользовательский токен администратора сообщества (User Token)
в переменной окружения ``VK_USER_TOKEN``: с токеном сообщества ВК вернёт
ошибку авторизации.
"""

import asyncio
import json
import logging
import os
import random
import re
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from sqlalchemy import func, select, text

from src.database.database import get_session
from src.database.models import Task
from src.vk_client import VKAPIError, VKClient

# Подхватываем переменные из .env (в проекте секреты хранятся там).
# Вызывается ДО чтения переменных окружения ниже.
load_dotenv()

# --------------------------------------------------------------------------- #
#  Настройки (можно переопределить переменными окружения)
# --------------------------------------------------------------------------- #

# Каталог с папками заданий
OUTPUT_DIR = Path(
    os.getenv("TASKS_OUTPUT_DIR", "/Users/only_serene/Documents/Default Project/output")
)

# Пользовательский токен администратора сообщества (НЕ токен группы)
VK_USER_TOKEN = os.getenv("VK_USER_TOKEN", "")

# Файл прогресса для пропуска уже залитых изображений
PROGRESS_FILE = Path(os.getenv("UPLOAD_PROGRESS_FILE", "uploaded_tasks.json"))

# Пауза между запросами к VK API, чтобы не поймать rate limit (секунды)
REQUEST_DELAY = float(os.getenv("UPLOAD_DELAY", "0.5"))

# Сколько раз повторять полный цикл загрузки одного фото при пустом ответе
# upload-сервера (ВК периодически возвращает пустой photo — это лечится ретраем)
UPLOAD_ATTEMPTS = int(os.getenv("UPLOAD_ATTEMPTS", "5"))

# --------------------------------------------------------------------------- #
#  Регулярные выражения для разбора имён
# --------------------------------------------------------------------------- #

# Папка задания: task_01, task01, task_15 и т.п. -> номер задания в группе 1
TASK_DIR_RE = re.compile(r"^task_?(\d+)$")

# Ответ в имени файла: variant_YY_ответ(6767).png / вариант(-7,5) и т.п.
ANSWER_RE = re.compile(r"ответ\(([^)]+)\)")

logger = logging.getLogger("upload_tasks")


# --------------------------------------------------------------------------- #
#  Работа с файлом прогресса
# --------------------------------------------------------------------------- #

def load_progress() -> set[str]:
    """Читает множество уже залитых файлов из файла прогресса."""
    if not PROGRESS_FILE.exists():
        return set()
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        return set(data.get("uploaded", []))
    except Exception as e:
        # Повреждённый файл прогресса не должен ронять скрипт
        logger.warning(f"Не удалось прочитать {PROGRESS_FILE}: {e}. Начинаем с пустого списка.")
        return set()


def save_progress(uploaded: set[str]) -> None:
    """Сохраняет множество уже залитых файлов в файл прогресса."""
    try:
        PROGRESS_FILE.write_text(
            json.dumps({"uploaded": sorted(uploaded)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"Не удалось сохранить прогресс в {PROGRESS_FILE}: {e}")


# --------------------------------------------------------------------------- #
#  Сбор файлов к обработке
# --------------------------------------------------------------------------- #

def collect_items(output_dir: Path) -> list[tuple[int, str, Path]]:
    """
    Собирает список заданий к загрузке.

    Возвращает список кортежей (номер_задания, ответ, путь_к_файлу),
    отсортированный по номеру задания и имени файла. Папки без ведущего
    ``task`` (например, ``_excluded``) и файлы без ответа в имени игнорируются.
    """
    items: list[tuple[int, str, Path]] = []

    for entry in os.scandir(output_dir):
        if not entry.is_dir():
            continue
        m = TASK_DIR_RE.match(entry.name)
        if not m:
            continue  # служебные папки пропускаем
        task_number = int(m.group(1))

        for f in os.scandir(entry.path):
            if not f.is_file():
                continue
            am = ANSWER_RE.search(f.name)
            if not am:
                continue  # файлы без ответа в имени не рассматриваем
            answer = am.group(1).strip()
            items.append((task_number, answer, Path(f.path)))

    items.sort(key=lambda x: (x[0], x[2].name))
    return items


# --------------------------------------------------------------------------- #
#  Загрузка одного изображения на серверы ВКонтакте
# --------------------------------------------------------------------------- #

async def _upload_photo_once(
    vk: VKClient, http: aiohttp.ClientSession, file_path: Path
) -> str:
    """
    Одна попытка загрузки: получить сервер → залить файл → сохранить фото.

    ВК периодически (особенно при частых запросах) отдаёт от upload-сервера
    пустое поле ``photo`` (пустая строка или ``"[]"``). Такой ответ надо
    распознать и повторить всю цепочку — поэтому здесь мы явно проверяем,
    что ``photo`` непустой, и бросаем исключение для ретрая выше.
    """
    # Шаг 1. Адрес сервера загрузки (получаем каждый раз заново — URL одноразовый)
    upload_server = await vk.call_method("photos.getMessagesUploadServer")
    upload_url = upload_server["upload_url"]

    # Шаг 2. Отправляем файл multipart-запросом (поле "photo")
    with file_path.open("rb") as f:
        form = aiohttp.FormData()
        form.add_field("photo", f, filename=file_path.name, content_type="image/png")
        async with http.post(upload_url, data=form) as resp:
            raw = await resp.text()

    try:
        result = json.loads(raw)
    except Exception:
        raise RuntimeError(f"upload-сервер вернул не-JSON (HTTP {resp.status}): {raw[:200]}")

    # Пустой photo — это не валидный результат, повод для повторной попытки
    photo_field = str(result.get("photo", "")).strip()
    if not all(k in result for k in ("server", "photo", "hash")) or photo_field in ("", "[]"):
        raise RuntimeError(f"upload-сервер вернул пустой photo: {result}")

    # Шаг 3. Сохраняем фото и получаем объект с id/owner_id/access_key
    saved = await vk.call_method(
        "photos.saveMessagesPhoto",
        server=result["server"],
        photo=result["photo"],
        hash=result["hash"],
    )
    photo_obj = saved[0] if isinstance(saved, list) else saved

    owner_id = photo_obj["owner_id"]
    photo_id = photo_obj["id"]
    access_key = photo_obj.get("access_key", "")
    return f"photo{owner_id}_{photo_id}_{access_key}"


async def upload_photo(vk: VKClient, http: aiohttp.ClientSession, file_path: Path) -> str:
    """
    Загружает изображение на серверы ВК с повторами и возвращает строку вложения
    вида ``photo{owner_id}_{id}_{access_key}``.

    Повторяет полный цикл (getMessagesUploadServer → POST → saveMessagesPhoto)
    до ``UPLOAD_ATTEMPTS`` раз с нарастающей паузой: это лечит периодические
    пустые ответы upload-сервера ВК.
    """
    last_error: Exception | None = None
    for attempt in range(1, UPLOAD_ATTEMPTS + 1):
        try:
            return await _upload_photo_once(vk, http, file_path)
        except Exception as e:
            last_error = e
            wait = min(2 ** attempt, 15) + random.random()
            logger.warning(
                f"Попытка {attempt}/{UPLOAD_ATTEMPTS} не удалась для {file_path.name}: {e}. "
                f"Повтор через {wait:.1f}с"
            )
            await asyncio.sleep(wait)

    raise last_error or RuntimeError("Не удалось загрузить фото")


# --------------------------------------------------------------------------- #
#  Основная логика
# --------------------------------------------------------------------------- #

async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Проверка конфигурации до начала работы
    if not VK_USER_TOKEN:
        logger.error(
            "Не задан VK_USER_TOKEN. Нужен ПОЛЬЗОВАТЕЛЬСКИЙ токен администратора "
            "сообщества (не токен группы). Установите его в переменной окружения."
        )
        return

    if not OUTPUT_DIR.exists():
        logger.error(f"Каталог с заданиями не найден: {OUTPUT_DIR}")
        return

    items = collect_items(OUTPUT_DIR)
    if not items:
        logger.warning(f"В каталоге {OUTPUT_DIR} не найдено подходящих файлов заданий.")
        return

    logger.info(f"Найдено файлов к обработке: {len(items)}")
    logger.info(f"Каталог: {OUTPUT_DIR}")
    logger.info(f"Пауза между запросами: {REQUEST_DELAY} сек")

    uploaded = load_progress()
    logger.info(f"Уже залито (из {PROGRESS_FILE}): {len(uploaded)}")

    # Клиент VK с пользовательским токеном (переиспользуем логику ретраев)
    vk = VKClient(token=VK_USER_TOKEN)

    # Ранняя проверка токена, чтобы не «молотить» ошибки по каждому файлу
    try:
        profile = await vk.call_method("users.get")
        user = profile[0] if isinstance(profile, list) and profile else {}
        logger.info(
            f"Токен VK проверен: {user.get('first_name', '')} {user.get('last_name', '')} "
            f"(id={user.get('id')})"
        )
    except VKAPIError as e:
        logger.error(
            f"Ошибка авторизации VK API ({e}). Убедитесь, что используется "
            f"пользовательский токен администратора сообщества."
        )
        await vk.close()
        return

    stats = {"uploaded": 0, "skipped": 0, "errors": 0}

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as http:
        for task_number, answer, path in items:
            rel = str(path.relative_to(OUTPUT_DIR))

            # Пропускаем уже залитые файлы (защита от дублей при повторном запуске)
            if rel in uploaded:
                stats["skipped"] += 1
                logger.info(f"⏭ Пропуск (уже залито): {rel}")
                continue

            logger.info(f"📤 Загрузка: {rel} -> задание №{task_number}, ответ '{answer}'")
            try:
                attachment = await upload_photo(vk, http, path)

                # Сохранение в существующую таблицу tasks. Используем прямой INSERT,
                # потому что в фактической схеме БД у этой таблицы нет колонки is_active,
                # которая присутствует только в Python-модели.
                async with get_session() as session:
                    result = await session.execute(
                        text(
                            "INSERT INTO tasks (task_number, answer, image_attachment) "
                            "VALUES (:task_number, :answer, :image_attachment) "
                            "RETURNING id"
                        ),
                        {
                            "task_number": task_number,
                            "answer": answer,
                            "image_attachment": attachment,
                        },
                    )
                    task_id = result.scalar_one()
                    await session.commit()

                uploaded.add(rel)
                save_progress(uploaded)  # сохраняем прогресс после каждого успеха
                stats["uploaded"] += 1
                logger.info(f"✅ Сохранено: {rel} -> {attachment}")

                # Проверяем реальное количество заданий после каждого добавления.
                async with get_session() as session:
                    total = await session.scalar(select(func.count()).select_from(Task))
                logger.info(f"📊 Заданий в БД после добавления: {total}")
            except Exception as e:
                # Ошибка на одном файле не прерывает обработку остальных
                stats["errors"] += 1
                logger.error(f"❌ Ошибка при обработке {rel}: {e}", exc_info=True)

            # Пауза между запросами к VK API (с небольшим джиттером)
            await asyncio.sleep(REQUEST_DELAY + random.uniform(0, 0.2))

    await vk.close()

    # Итоговая сводка
    logger.info("=" * 60)
    logger.info(
        f"ГОТОВО. Залито: {stats['uploaded']}, "
        f"пропущено: {stats['skipped']}, ошибок: {stats['errors']}"
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())