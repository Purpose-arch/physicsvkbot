"""
Start command handler
"""

import logging
from vkbottle.bot import Message
from src.database.database import get_session
from src.handlers.state_manager import state_manager
from src.keyboards.reply_keyboards import get_main_menu_keyboard
from src.services.user_service import UserService

logger = logging.getLogger(__name__)


async def handle_start(message: Message):
    vk_id = message.from_id
    try:
        await state_manager.clear_state(vk_id)

        async with get_session() as session:
            user_service = UserService(session)
            user = await user_service.get_or_create_user(vk_id)
            is_admin = await user_service.sync_admin_status(vk_id)
            keyboard = get_main_menu_keyboard(is_admin)

            await message.answer(
                f"Привет, {user.first_name}! Рад снова тебя видеть! 🎉",
                keyboard=keyboard,
            )
    except Exception as e:
        logger.error(f"Error in handle_start for VK ID {vk_id}: {e}", exc_info=True)
        await message.answer("Привет! Напиши 'Главное меню' для начала работы.")