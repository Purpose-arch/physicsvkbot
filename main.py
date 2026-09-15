import json
import logging
import asyncio
from aiohttp import web

from vkbottle import Bot
from vkbottle.bot import Message

from src.config import settings
from src.database.database import create_tables, get_session
from src.handlers.state_manager import state_manager
from src.handlers.start import handle_start
from src.handlers.registration import process_registration
from src.handlers.practice import handle_practice_flow, handle_my_stats, send_next_task
from src.handlers.admin import handle_admin_flow, is_admin_user
from src.keyboards.reply_keyboards import get_main_menu_keyboard, get_admin_panel_keyboard
from src.services.user_service import UserService
from src.services.stats_service import StatsService

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

bot = Bot(token=settings.bot_token)


def clean_input(text: str) -> str:
    """Удаляет скрытые юникод-символы разных мобильных ОС для 100% совпадения кнопок."""
    if not text:
        return ""
    clean = text.replace("\ufe0f", "").replace("\u200b", "").strip().lower()
    return " ".join(clean.split())


@bot.on.message()
async def global_message_handler(message: Message):
    try:
        vk_id = message.from_id
        raw_text = message.text.strip() if message.text else ""
        norm_text = clean_input(raw_text)
        current_state = await state_manager.get_state(vk_id)

        logger.info(f"Incoming message from VK ID {vk_id}: raw='{raw_text}', norm='{norm_text}', state='{current_state}'")

        # 1. Инлайн-кнопки
        payload_data = {}
        if getattr(message, "payload", None):
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

            await message.answer("Главное меню 👇", keyboard=get_main_menu_keyboard(is_admin))
            return

        if action == "confirm_clear_data":
            if await is_admin_user(message):
                async with get_session() as session:
                    stats_service = StatsService(session)
                    counts = await stats_service.clear_all_data_except_tasks()

                await message.answer(
                    "✅ Данные успешно очищены:\n\n"
                    f"👥 Пользователей удалено: {counts['users']}\n"
                    f"📝 Записей истории удалено: {counts['answer_history']}",
                    keyboard=get_admin_panel_keyboard()
                )
            return

        if action == "cancel_clear_data":
            await message.answer("❌ Очистка данных отменена.", keyboard=get_admin_panel_keyboard())
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
        if current_state in ("waiting_for_task_number", "waiting_for_task_photo", "waiting_for_task_answer") or ("добавить задание" in norm_text or "очистить данные" in norm_text):
            await handle_admin_flow(message)
            return

        # 5. Практика и проверка ответов
        await handle_practice_flow(message)

    except Exception as e:
        logger.exception(f"Error in global_message_handler for user {message.from_id}: {e}")
        await message.answer("⚠️ Произошла ошибка при обработке сообщения. Попробуйте ещё раз.")


async def health_handler(request):
    return web.json_response({"status": "ok", "service": "vk_physics_bot"})


async def main():
    await create_tables()

    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.port)
    await site.start()
    logging.info(f"Healthcheck HTTP-сервер успешно запущен на порту {settings.port}")

    # Самовосстанавливающийся LongPoll цикл
    while True:
        try:
            logger.info("Starting vkbottle LongPoll listener...")
            await bot.run_polling()
        except Exception as e:
            logger.error(f"vkbottle LongPoll error: {e}. Reconnecting in 2 seconds...", exc_info=True)
            await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())