"""
Обработчики практики: решение заданий и просмотр статистики
"""

import re
import logging

from src.config import settings
from src.database.database import get_session
from src.handlers.state_manager import state_manager
from src.keyboards.inline_keyboards import get_task_action_keyboard
from src.keyboards.reply_keyboards import get_main_menu_keyboard, get_task_keyboard
from src.services.task_service import TaskService
from src.services.user_service import UserService
from src.utils import clean_input
from src.vk_client import Message, vk_client


def _normalize_answer(answer: str) -> str:
    """Приводит ответ к сопоставимому виду: запятая -> точка, без пробелов,
    без хвостовых нулей в десятичной дроби. Числовые ответы вида «5», «5.0»,
    «5,00» считаются одинаковыми."""
    s = clean_input(answer).replace(",", ".").replace(" ", "")
    # Убираем хвостовые нули: 5.0 -> 5, 5.500 -> 5.5
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s

logger = logging.getLogger(__name__)


async def send_vk_message(
    message: Message,
    text: str,
    keyboard: str | None = None,
    attachment: str | None = None,
):
    """Безопасно отправляет сообщение VK; клиент повторит без вложения, если VK его отклонит."""
    peer_id = getattr(message, "peer_id", getattr(message, "from_id", None))
    await vk_client.send_message(
        peer_id=peer_id,
        text=text,
        keyboard=keyboard,
        attachment=attachment,
    )


async def send_next_task(
    message: Message,
    vk_id: int,
    task_mode: str,
    selected_number: int | None = None,
):
    """Получает и отправляет задание пользователю."""
    async with get_session() as session:
        user_service = UserService(session)
        task_service = TaskService(session)

        user = await user_service.get_or_create_user(vk_id)
        caption = ""

        if task_mode == "number" and selected_number:
            task = await task_service.get_task_by_number(selected_number, user.solved_tasks)
            if task:
                stats = await task_service.get_task_number_stats(selected_number, user.vk_id)
                caption = f"📝 Задание №{selected_number} ({stats['solved']}/{stats['total']})"
        else:
            task = await task_service.get_random_task(user.solved_tasks)
            if task:
                caption = f"📝 Задание №{task.task_number}"

        if not task:
            await state_manager.clear_state(vk_id)
            is_admin = user.is_admin or (vk_id in settings.admin_ids_list)
            kb = get_main_menu_keyboard(is_admin)
            if task_mode == "number" and selected_number:
                await vk_client.send_message(
                    message.peer_id,
                    f"🎉 В базе нет доступных нерешенных заданий №{selected_number}!\n"
                    "Зайдите в '⚙️ Админ панель' -> '➕ Добавить задание' или выберите другой номер.",
                    keyboard=kb,
                )
            else:
                await vk_client.send_message(
                    message.peer_id,
                    "🎉 В базе пока нет доступных заданий!\n"
                    "Перейдите в '⚙️ Админ панель' -> '➕ Добавить задание', чтобы занести задачи.",
                    keyboard=kb,
                )
            return

        # Надежное сохранение состояния ожидания ответа в БД
        await state_manager.set_state(
            vk_id,
            "waiting_for_answer",
            current_task_id=task.id,
            expected_answer=task.answer,
            task_mode=task_mode,
            selected_number=selected_number or task.task_number,
        )

        msg_text = f"{caption}\n\nНапиши свой ответ:"
        kb = get_task_keyboard()

        await send_vk_message(
            message=message,
            text=msg_text,
            keyboard=kb,
            attachment=task.image_attachment,
        )


async def handle_practice_flow(message: Message):
    """Основной обработчик практики: выбор задания и проверка ответов."""
    vk_id = message.from_id
    raw_text = message.text.strip() if message.text else ""
    norm_text = clean_input(raw_text)

    state_data = await state_manager.get_data(vk_id)
    current_state = state_data.get("state")

    # 1. Кнопка возврата в меню
    if "главное меню" in norm_text:
        await state_manager.clear_state(vk_id)
        async with get_session() as session:
            user_service = UserService(session)
            user = await user_service.get_or_create_user(vk_id)
            is_admin = user.is_admin or (vk_id in settings.admin_ids_list)

        await vk_client.send_message(message.peer_id, "Главное меню 👇", keyboard=get_main_menu_keyboard(is_admin))
        return

    # 2. ПРИОРИТЕТНАЯ ПРОВЕРКА ОТВЕТА ПОЛЬЗОВАТЕЛЯ
    if current_state == "waiting_for_answer":
        expected = state_data.get("expected_answer")
        task_id = state_data.get("current_task_id")

        try:
            if expected is None or not task_id:
                # Повреждённое состояние — сбрасываем и просим начать заново
                await vk_client.send_message(
                    message.peer_id,
                    "⚠️ Состояние потеряно. Выбери задание заново.",
                    keyboard=get_task_action_keyboard(),
                )
                return

            async with get_session() as session:
                user_service = UserService(session)
                task_service = TaskService(session)

                await user_service.get_or_create_user(vk_id)

                user_answer = _normalize_answer(raw_text)
                expected_clean = _normalize_answer(str(expected))
                is_correct = user_answer == expected_clean

                await user_service.add_solved_task(vk_id, task_id)
                await task_service.add_answer_history(vk_id, task_id, is_correct)

                result_text = "✅ Верно!" if is_correct else "❌ Неправильно"
                await vk_client.send_message(
                    message.peer_id,
                    f"{result_text}\nПравильный ответ: {expected}",
                    keyboard=get_task_action_keyboard(),
                )
        except Exception:
            logger.exception(f"Error checking answer for VK ID {vk_id}")
            await vk_client.send_message(
                message.peer_id,
                "⚠️ Ошибка при проверке ответа. Попробуйте отправить его ещё раз.",
            )
        finally:
            # Ожидание ответа сбрасываем, чтобы пользователь не застрял, но
            # сохраняем режим (конкретный номер / случайное), чтобы кнопка
            # «⏭️ Следующее задание» выдавала задание того же типа.
            task_mode = state_data.get("task_mode", "random")
            selected_number = state_data.get("selected_number")
            await state_manager.set_state(
                vk_id,
                "after_answer",
                task_mode=task_mode,
                selected_number=selected_number,
            )
        return

    # 3. Выбор случайного задания или конкретного номера (1-20)
    if "случайное задание" in norm_text or re.match(r"^\d+$", raw_text):
        if "случайное задание" in norm_text:
            await send_next_task(message, vk_id, task_mode="random")
        else:
            num = int(raw_text)
            if not 1 <= num <= 20:
                await vk_client.send_message(message.peer_id, "Пожалуйста, выбери номер задания от 1 до 20")
                return
            await send_next_task(message, vk_id, task_mode="number", selected_number=num)
        return


async def handle_my_stats(message: Message):
    """Показывает личную статистику пользователя с полной защитой от сбоев."""
    vk_id = message.from_id
    try:
        async with get_session() as session:
            user_service = UserService(session)
            task_service = TaskService(session)

            user = await user_service.get_or_create_user(vk_id)
            stats = await task_service.get_user_stats(vk_id)

            if stats["total"] == 0:
                await vk_client.send_message(
                    message.peer_id,
                    "📈 У тебя пока нет решённых заданий.\n"
                    "Нажми на цифру или 🎲 Случайное задание, чтобы начать!"
                )
                return

            accuracy = (stats["correct"] / stats["total"] * 100) if stats["total"] else 0
            lines = [
                "📈 Твоя статистика\n",
                f"🆔 ID: {user.id}",
                f"✅ Верно: {stats['correct']}",
                f"❌ Неверно: {stats['incorrect']}",
                f"📊 Всего ответов: {stats['total']}",
                f"🎯 Точность: {accuracy:.1f}%",
            ]

            if stats.get("breakdown"):
                lines.append("\nПо номерам заданий:")
                for num in sorted(stats["breakdown"].keys()):
                    b = stats["breakdown"][num]
                    pct = (b["correct"] / b["total"] * 100) if b["total"] else 0
                    lines.append(f"  №{num}: {b['correct']}/{b['total']} ({pct:.0f}%)")

            await vk_client.send_message(message.peer_id, "\n".join(lines))
    except Exception as e:
        logger.error(f"Error in handle_my_stats for VK ID {vk_id}: {e}", exc_info=True)
        await vk_client.send_message(message.peer_id, "📈 Статистика временно недоступна. Попробуйте еще раз позже.")