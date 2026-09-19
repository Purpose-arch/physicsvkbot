"""
Обработчик команды «Начать»
"""

import logging
from src.config import settings
from src.database.database import get_session
from src.handlers.state_manager import state_manager
from src.keyboards.reply_keyboards import get_main_menu_keyboard
from src.services.user_service import UserService
from src.vk_client import Message, VKAPIError, vk_client

logger = logging.getLogger(__name__)


async def handle_start(message: Message):
    vk_id = message.from_id
    try:
        await state_manager.clear_state(vk_id)

        async with get_session() as session:
            user_service = UserService(session)
            # Ищем пользователя, НЕ создавая его автоматически,
            # чтобы новым юзерам предлагалась регистрация.
            user = await user_service.get_user_by_vk_id(vk_id)
            is_admin = vk_id in settings.admin_ids_list

            if user is None or user.grade not in (10, 11) or not user.first_name or user.first_name == "Ученик":
                # Новый или некорректно зарегистрированный пользователь — запускаем регистрацию
                await state_manager.set_state(vk_id, "waiting_for_first_name")
                await vk_client.send_message(
                    message.peer_id,
                    "Привет! 👋 Добро пожаловать в бота по физике!\n\n"
                    "Давай познакомимся: как тебя зовут?",
                )
                return

            # Синхронизируем админ-статус только для существующих пользователей
            is_admin = is_admin or await user_service.sync_admin_status(vk_id)
            keyboard = get_main_menu_keyboard(is_admin)

            await vk_client.send_message(
                message.peer_id,
                f"Привет, {user.first_name}! Рад снова тебя видеть! 🎉",
                keyboard=keyboard,
            )
    except VKAPIError as e:
        # Не пытаемся отправить fallback-сообщение: если VK API запретил
        # отправку (например, ошибка 912 «Возможности ботов»), повторная
        # отправка тоже упадёт. Пробрасываем ошибку в глобальный обработчик.
        logger.error(f"VK API error {e.code} in handle_start for VK ID {vk_id}: {e}")
        raise
    except Exception as e:
        logger.error(f"Error in handle_start for VK ID {vk_id}: {e}", exc_info=True)
        try:
            await vk_client.send_message(message.peer_id, "Привет! Напиши 'Главное меню' для начала работы.")
        except Exception as send_err:
            logger.error(f"Failed to send fallback message to VK ID {vk_id}: {send_err}")