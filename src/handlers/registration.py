"""
Обработчик регистрации новых пользователей VK
"""

import logging
from src.database.database import get_session
from src.handlers.state_manager import state_manager
from src.keyboards.reply_keyboards import get_main_menu_keyboard
from src.services.user_service import UserService
from src.vk_client import Message, vk_client

logger = logging.getLogger(__name__)


async def process_registration(message: Message):
    vk_id = message.from_id
    text = message.text.strip() if message.text else ""
    state_data = await state_manager.get_data(vk_id)
    current_state = state_data.get("state")

    try:
        if current_state == "waiting_for_first_name":
            if not text or len(text) > 50:
                await vk_client.send_message(message.peer_id, "Пожалуйста, введи корректное имя (не длиннее 50 символов).")
                return

            await state_manager.set_state(vk_id, "waiting_for_grade", first_name=text)
            await vk_client.send_message(
                message.peer_id,
                f"Приятно познакомиться, {text}! 🎓\n\n"
                "В каком классе ты учишься? Введи 10 или 11:"
            )

        elif current_state == "waiting_for_grade":
            if text not in ("10", "11"):
                await vk_client.send_message(message.peer_id, "Пожалуйста, введи 10 или 11.")
                return

            first_name = state_data.get("first_name", "Ученик")
            grade = int(text)

            async with get_session() as session:
                user_service = UserService(session)
                # get_or_create: защита от unique-конфликта при повторной регистрации
                user = await user_service.get_or_create_user(vk_id)
                if user.first_name != first_name or user.grade != grade:
                    user.first_name = first_name
                    user.grade = grade
                    await session.commit()
                keyboard = get_main_menu_keyboard(user.is_admin)

                await vk_client.send_message(
                    message.peer_id,
                    f"✅ Регистрация завершена!\n\n"
                    f"Добро пожаловать, {user.first_name}!\n"
                    "Выбирай номер задания или нажми  Случайное задание, чтобы начать.",
                    keyboard=keyboard,
                )

            await state_manager.clear_state(vk_id)
    except Exception as e:
        logger.error(f"Error in process_registration for VK ID {vk_id}: {e}", exc_info=True)
        await vk_client.send_message(message.peer_id, "⚠️ Ошибка регистрации. Введите имя заново:")