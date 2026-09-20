import json
import logging
import asyncio
import time
import re
from aiohttp import web

from src.config import settings, validate_settings
from src.database.database import create_tables, get_session
from src.handlers.state_manager import state_manager
from src.handlers.start import handle_start
from src.handlers.registration import process_registration
from src.handlers.practice import handle_practice_flow, handle_my_stats, send_next_task
from src.handlers.admin import handle_admin_flow, is_admin_user
from src.keyboards.reply_keyboards import get_main_menu_keyboard, get_admin_panel_keyboard
from src.services.user_service import UserService
from src.services.stats_service import StatsService
from src.vk_client import Message, VKAPIError, vk_client

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

# --- Защита от флуда уведомлениями об ошибках ---
ERROR_NOTIFY_COOLDOWN = 300  # не чаще 1 сообщения об ошибке в 5 минут на пользователя
_user_error_notify_ts: dict[int, float] = {}

# --- Дедупликация входящих сообщений (защита от двойной обработки кнопок) ---
DEDUP_TTL = 300  # секунд хранения ключей обработанных сообщений
_processed_messages: dict[tuple[int, int], float] = {}


def _is_duplicate(message: Message) -> bool:
    """True, если такое сообщение уже обрабатывалось недавно."""
    msg_id = message._raw.get("id") or message._raw.get("conversation_message_id") or 0
    if not msg_id:
        return False
    key = (message.from_id, msg_id)
    now = time.monotonic()
    if key in _processed_messages:
        return True
    _processed_messages[key] = now
    # Периодическая очистка устаревших ключей
    if len(_processed_messages) > 10000:
        cutoff = now - DEDUP_TTL
        for k in [k for k, ts in _processed_messages.items() if ts < cutoff]:
            _processed_messages.pop(k, None)
    return False

# Ключевые слова ошибок VK API, связанных с токеном (не имеет смысла ретраить часто)
TOKEN_ERROR_RE = re.compile(
    r"invalid access token|access token|user authorization failed|token[ _-]?invalid|error code:? ?(5|28)\b",
    re.IGNORECASE,
)


async def notify_user_error(message: Message, user_id: int):
    """Отправляет сообщение об ошибке пользователю не чаще, чем раз в ERROR_NOTIFY_COOLDOWN секунд."""
    now = time.monotonic()
    last = _user_error_notify_ts.get(user_id, 0)
    if now - last < ERROR_NOTIFY_COOLDOWN:
        logger.warning(f"Suppressed duplicate error notification for user {user_id} (cooldown)")
        return
    _user_error_notify_ts[user_id] = now
    try:
        await vk_client.send_message(message.peer_id, "⚠️ Произошла ошибка при обработке сообщения. Попробуйте ещё раз.")
    except Exception as e:
        logger.error(f"Failed to send error notification to user {user_id}: {e}")


def clean_input(text: str) -> str:
    """Удаляет скрытые юникод-символы разных мобильных ОС для 100% совпадения кнопок."""
    if not text:
        return ""
    clean = text.replace("\ufe0f", "").replace("\u200b", "").strip().lower()
    return " ".join(clean.split())


async def process_update(message: Message):
    """Маршрутизирует одно входящее сообщение по цепочке обработчиков."""
    try:
        # Отсекаем дубликаты (VK может доставить одно сообщение дважды)
        if _is_duplicate(message):
            logger.info(f"Duplicate message from VK ID {message.from_id} ignored")
            return

        vk_id = message.from_id
        raw_text = message.text.strip() if message.text else ""
        norm_text = clean_input(raw_text)
        current_state = await state_manager.get_state(vk_id)

        logger.info(f"Incoming message from VK ID {vk_id}: raw='{raw_text}', norm='{norm_text}', state='{current_state}'")

        # 1. Инлайн-кнопки
        payload_data = {}
        if message.payload:
            try:
                payload_data = json.loads(message.payload) if isinstance(message.payload, str) else message.payload
            except Exception:
                payload_data = {}

        action = payload_data.get("action")

        if action == "next_task":
            state_data = await state_manager.get_data(vk_id)
            task_mode = state_data.get("task_mode", "random")
            selected_number = state_data.get("selected_number")
            await send_next_task(message, vk_id, task_mode=task_mode, selected_number=selected_number)
            return

        if action == "exit_to_menu" or "главное меню" in norm_text:
            await state_manager.clear_state(vk_id)
            async with get_session() as session:
                user_service = UserService(session)
                user = await user_service.get_or_create_user(vk_id)
                is_admin = user.is_admin or (vk_id in settings.admin_ids_list)

            await vk_client.send_message(message.peer_id, "Главное меню 👇", keyboard=get_main_menu_keyboard(is_admin))
            return

        if action == "confirm_clear_data":
            if await is_admin_user(message):
                async with get_session() as session:
                    stats_service = StatsService(session)
                    counts = await stats_service.clear_all_data_except_tasks()

                await vk_client.send_message(
                    message.peer_id,
                    "✅ Данные успешно очищены:\n\n"
                    f"👥 Пользователей удалено: {counts['users']}\n"
                    f"📝 Записей истории удалено: {counts['answer_history']}",
                    keyboard=get_admin_panel_keyboard()
                )
            return

        if action == "cancel_clear_data":
            await vk_client.send_message(message.peer_id, "❌ Очистка данных отменена.", keyboard=get_admin_panel_keyboard())
            return

        # 2. Старт / Сброс
        if norm_text in ("начать", "start", "/start"):
            await handle_start(message)
            return

        # 3. Приоритетные кнопки переключения меню (Мгновенное срабатывание с 1-го клика)
        if "админ панель" in norm_text and not ("добавление" in norm_text or "шаг" in norm_text):
            await handle_admin_flow(message)
            return

        if "моя статистика" in norm_text:
            await state_manager.clear_state(vk_id)
            await handle_my_stats(message)
            return

        # 4. Проверка состояний FSM
        # Регистрация
        if current_state in ("waiting_for_first_name", "waiting_for_grade"):
            await process_registration(message)
            return

        # Админ состояния
        if current_state in ("waiting_for_task_number", "waiting_for_task_photo", "waiting_for_task_answer", "waiting_for_bulk_tasks") or (
            "добавить задание" in norm_text or "массовое добавление" in norm_text or "очистить данные" in norm_text
        ):
            await handle_admin_flow(message)
            return

        # 5. Практика и проверка ответов
        await handle_practice_flow(message)

    except VKAPIError as e:
        if e.code == 912:
            # Сообщество не разрешает боту писать в этот чат.
            logger.warning(
                f"VKAPIError 912 for user {message.from_id}: bot is not allowed to "
                "message this chat. Enable 'Возможности ботов' in the VK community settings."
            )
        else:
            logger.error(f"VK API error {e.code} for user {message.from_id}: {e}")
    except Exception as e:
        logger.exception(f"Error in process_update for user {message.from_id}: {e}")
        await notify_user_error(message, message.from_id)


async def health_handler(request):
    return web.json_response({"status": "ok", "service": "vk_physics_bot"})


async def dispatch_updates(data: dict):
    """Итерирует обновления Long Poll и параллельно обрабатывает события сообщений."""
    for update in data.get("updates", []):
        if update.get("type") != "message_new":
            continue
        obj = update.get("object", {})
        # API 5.103+: message вложено в object; в старых версиях — плоская структура
        msg_raw = obj.get("message", obj)
        if not msg_raw.get("from_id"):
            continue
        message = Message.from_update(msg_raw)
        # Обрабатываем каждое сообщение параллельно, чтобы long poll не блокировался.
        # Храним ссылки на задачи, чтобы GC их не собрал, и логируем исключения.
        task = asyncio.create_task(process_update(message))
        _background_tasks.add(task)
        task.add_done_callback(_background_task_done)


def _background_task_done(task: asyncio.Task):
    _background_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error(f"Background update task failed: {task.exception()}")


_background_tasks: set[asyncio.Task] = set()


async def main():
    validate_settings()
    await create_tables()

    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.port)
    await site.start()
    logging.info(f"Healthcheck HTTP-сервер успешно запущен на порту {settings.port}")

    # Самовосстанавливающийся LongPoll цикл с экспоненциальным backoff
    # и специальной обработкой ошибок токена (чтобы не флудить запросами к VK API).
    consecutive_errors = 0
    MAX_BACKOFF = 600  # максимум 10 минут между попытками
    TOKEN_ERROR_BACKOFF = 1800  # 30 минут при ошибке токена

    while True:
        try:
            logger.info("Starting VK LongPoll listener (aiohttp client)...")
            # Внутренний цикл Long Poll: сетевые сбои обрабатываются внутри wait_event,
            # поэтому сюда доходят только фатальные ошибки (например, невалидный токен).
            while True:
                data = await vk_client.wait_event()
                if data is not None:
                    await dispatch_updates(data)
        except VKAPIError as e:
            error_str = str(e)
            is_token_error = bool(TOKEN_ERROR_RE.search(error_str)) or e.code in (5, 28)
            consecutive_errors += 1

            if is_token_error:
                if consecutive_errors == 1:
                    logger.critical(
                        f"VK API token error detected: {e}. "
                        f"Not retrying with the same token frequently. "
                        f"Check BOT_TOKEN and restart the bot after fixing it."
                    )
                else:
                    logger.warning(f"Token error again (attempt {consecutive_errors}), skipping detailed log.")
                delay = TOKEN_ERROR_BACKOFF
            else:
                # Экспоненциальный backoff: 2s, 4s, 8s, ... до MAX_BACKOFF
                delay = min(2 ** consecutive_errors, MAX_BACKOFF)
                logger.error(
                    f"LongPoll error (attempt {consecutive_errors}): {e}. "
                    f"Reconnecting in {delay} seconds...",
                    exc_info=(consecutive_errors <= 3),
                )

            await asyncio.sleep(delay)
            # Сбрасываем Long Poll сессию для чистого переподключения
            await vk_client.close()
        except Exception as e:
            consecutive_errors += 1
            delay = min(2 ** consecutive_errors, MAX_BACKOFF)
            logger.exception(f"Unexpected error in polling loop (attempt {consecutive_errors}): {e}")
            await asyncio.sleep(delay)
            await vk_client.close()


if __name__ == "__main__":
    asyncio.run(main())