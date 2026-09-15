"""Admin handlers for the VK Physics EGE Bot"""

import logging

from src.config import settings
from src.database.database import get_session
from src.database.models import Task
from src.handlers.state_manager import state_manager
from src.keyboards.inline_keyboards import get_clear_data_confirmation_keyboard
from src.keyboards.reply_keyboards import (
    get_admin_panel_keyboard,
    get_task_addition_keyboard,
    get_task_number_selection_keyboard,
)
from src.services.stats_service import StatsService
from src.services.user_service import UserService
from src.vk_client import Message, vk_client

logger = logging.getLogger(__name__)


async def is_admin_user(message: Message) -> bool:
    """Check if the sender is an admin by ADMIN_IDS list or DB status."""
    vk_id = message.from_id
    if vk_id in settings.admin_ids_list:
        return True

    async with get_session() as session:
        user_service = UserService(session)
        user = await user_service.get_user_by_vk_id(vk_id)
        if user and user.is_admin:
            return True

    await vk_client.send_message(message.peer_id, "У тебя нет доступа к этой функции.")
    return False


def extract_photo_attachment(message: Message) -> str | None:
    """Extract VK photo attachment string from a message."""
    try:
        att_strings = message.get_attachment_strings() or []
    except Exception:
        att_strings = []

    for att in att_strings:
        if att.startswith("photo"):
            logger.info(f"Extracted attachment string: {att}")
            return att

    return None


async def handle_admin_flow(message: Message):
    """Main admin handler managing buttons and FSM states with flexible matching."""
    vk_id = message.from_id
    raw_text = message.text.strip() if message.text else ""
    norm_text = raw_text.replace("\ufe0f", "").replace("\u200b", "").strip().lower()

    if not await is_admin_user(message):
        return

    # Кнопки панели управления (гибкое нормализованное сравнение)
    if "админ панель" in norm_text and not ("добавление" in norm_text or "шаг" in norm_text):
        await state_manager.set_state(vk_id, "admin_panel")
        await vk_client.send_message(
            message.peer_id,
            "⚙️ Админ панель\n\nВыбери действие:",
            keyboard=get_admin_panel_keyboard(),
        )
        return

    if "статистика" in norm_text and "моя" not in norm_text:
        async with get_session() as session:
            stats_service = StatsService(session)
            stats = await stats_service.get_current_stats()

        stats_text = (
            "📊 СТАТИСТИКА БОТА\n\n"
            f"👥 Всего пользователей: {stats['users_count']}\n"
            f"🎯 Средняя точность: {stats['avg_accuracy']:.1f}%\n"
            f"✅ Верных ответов: {stats['correct_answers']}\n"
            f"📝 Всего ответов: {stats['total_answers']}\n"
            f"📅 Обновлено: {stats['date']}\n"
        )
        await vk_client.send_message(message.peer_id, stats_text, keyboard=get_admin_panel_keyboard())
        return

    if "очистить данные" in norm_text:
        await vk_client.send_message(
            message.peer_id,
            "⚠️ Внимание!\n\n"
            "Это действие удалит всех пользователей, всю историю ответов и статистику.\n"
            "Задания НЕ будут удалены.\n\nПродолжить?",
            keyboard=get_clear_data_confirmation_keyboard(),
        )
        return

    if "добавить задание" in norm_text:
        await state_manager.set_state(vk_id, "waiting_for_task_number")
        await vk_client.send_message(
            message.peer_id,
            "Выбери номер создаваемого задания на клавиатуре ниже (1-20):",
            keyboard=get_task_number_selection_keyboard(),
        )
        return

    # Цепочка состояний
    state_data = await state_manager.get_data(vk_id)
    current_state = state_data.get("state")

    # Шаг 1: Нажата кнопка номера задания (1-20)
    if current_state == "waiting_for_task_number":
        if not raw_text.isdigit() or not (1 <= int(raw_text) <= 20):
            await vk_client.send_message(
                message.peer_id,
                "Пожалуйста, выбери номер задания на клавиатуре ниже (1-20):",
                keyboard=get_task_number_selection_keyboard(),
            )
            return

        task_num = int(raw_text)
        await state_manager.set_state(vk_id, "waiting_for_task_photo", task_number=task_num)
        await vk_client.send_message(
            message.peer_id,
            f"📸 Выбрано задание №{task_num}.\nОтправь фото с условием задачи (можно сразу написать ответ в подписи к фото):",
            keyboard=get_task_addition_keyboard(),
        )
        return

    # Шаг 2: Отправка фото (и возможный ответ в подписи)
    if current_state == "waiting_for_task_photo":
        photo_att = extract_photo_attachment(message)
        if not photo_att:
            await vk_client.send_message(
                message.peer_id,
                "⚠️ Пожалуйста, прикрепи фото к сообщению.",
                keyboard=get_task_addition_keyboard(),
            )
            return

        task_num = state_data.get("task_number")

        # Если ответ указан прямо в подписи к фото
        if raw_text:
            answer = raw_text.strip()
            try:
                async with get_session() as session:
                    task = Task(task_number=task_num, answer=answer, image_attachment=photo_att)
                    session.add(task)
                    await session.commit()
            except Exception as e:
                logger.exception(f"Error saving task: {e}")
                await vk_client.send_message(
                    message.peer_id,
                    "⚠️ Ошибка при сохранении задачи в БД. Попробуйте еще раз.",
                    keyboard=get_task_addition_keyboard(),
                )
                return

            logger.info(f"Task #{task_num} successfully added: answer='{answer}', att='{photo_att}'")
            await state_manager.set_state(vk_id, "waiting_for_task_number")
            await vk_client.send_message(
                message.peer_id,
                f"✅ Задание №{task_num} с ответом «{answer}» успешно сохранено!\n\n"
                "Выбери номер следующего задания на клавиатуре:",
                keyboard=get_task_number_selection_keyboard(),
            )
            return

        # Если отправлена только картинка без подписи
        await state_manager.set_state(
            vk_id,
            "waiting_for_task_answer",
            task_number=task_num,
            image_attachment=photo_att,
        )
        await vk_client.send_message(
            message.peer_id,
            f"✍️ Фото для задания №{task_num} успешно получено!\nТеперь введи правильный ответ текстом или числом:",
            keyboard=get_task_addition_keyboard(),
        )
        return

    # Шаг 3: Ввод ответа отдельным сообщением
    if current_state == "waiting_for_task_answer":
        if not raw_text:
            await vk_client.send_message(
                message.peer_id,
                "Введи ответ к заданию текстом:",
                keyboard=get_task_addition_keyboard(),
            )
            return

        task_number = state_data.get("task_number")
        image_att = state_data.get("image_attachment")
        answer = raw_text.strip()

        try:
            async with get_session() as session:
                task = Task(task_number=task_number, answer=answer, image_attachment=image_att)
                session.add(task)
                await session.commit()
        except Exception as e:
            logger.exception(f"Error saving task: {e}")
            await vk_client.send_message(
                message.peer_id,
                "⚠️ Ошибка при сохранении задачи в БД. Попробуйте еще раз.",
                keyboard=get_task_addition_keyboard(),
            )
            return

        logger.info(f"Task #{task_number} successfully added: answer='{answer}', att='{image_att}'")
        await state_manager.set_state(vk_id, "waiting_for_task_number")
        await vk_client.send_message(
            message.peer_id,
            f"✅ Задание №{task_number} с ответом «{answer}» успешно сохранено!\n\n"
            "Выбери номер следующего задания на клавиатуре:",
            keyboard=get_task_number_selection_keyboard(),
        )
        return