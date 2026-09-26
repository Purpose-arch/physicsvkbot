"""Handlers for practice tasks and statistics."""

import logging
import re

from src.database.database import get_session
from src.handlers.state_manager import state_manager
from src.keyboards.inline_keyboards import get_task_action_keyboard
from src.keyboards.reply_keyboards import get_main_menu_keyboard, get_task_keyboard
from src.services.stats_service import StatsService
from src.services.task_service import TaskService
from src.services.user_service import UserService
from src.utils import clean_input
from src.vk_client import Message, vk_client

logger = logging.getLogger(__name__)


def _normalize_answer(answer: str) -> str:
    value = clean_input(answer).replace(",", ".").replace(" ", "")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value


async def send_next_task(message: Message, vk_id: int, task_mode: str, selected_number: int | None = None):
    async with get_session() as session:
        user = await UserService(session).get_or_create_user(vk_id)
        service = TaskService(session)
        task = await service.get_random_task(user.id, selected_number if task_mode == "number" else None)
        if task is None:
            await state_manager.clear_state(vk_id)
            text = f"В базе нет доступных нерешённых заданий №{selected_number}!" if selected_number else "В базе пока нет доступных заданий!"
            await vk_client.send_message(message.peer_id, text, keyboard=get_main_menu_keyboard())
            return

        caption = f"📝 Задание №{task.task_number}"
        if task_mode == "number" and selected_number:
            stats = await service.get_task_number_stats(selected_number, user.id)
            caption += f" ({stats['solved']}/{stats['total']})"

        await state_manager.set_state(
            vk_id,
            "waiting_for_answer",
            current_task_id=task.id,
            expected_answer=task.answer,
            task_mode=task_mode,
            selected_number=selected_number or task.task_number,
        )
        await vk_client.send_message(
            message.peer_id,
            f"{caption}\n\nНапиши свой ответ:",
            keyboard=get_task_keyboard(),
            attachment=task.image_attachment,
        )


async def handle_practice_flow(message: Message):
    vk_id = message.from_id
    raw_text = message.text.strip() if message.text else ""
    normalized = clean_input(raw_text)
    state_data = await state_manager.get_data(vk_id)

    if "главное меню" in normalized:
        await state_manager.clear_state(vk_id)
        await vk_client.send_message(message.peer_id, "Главное меню 👇", keyboard=get_main_menu_keyboard())
        return

    if state_data.get("state") == "waiting_for_answer":
        try:
            expected = state_data.get("expected_answer")
            task_id = state_data.get("current_task_id")
            if expected is None or not task_id:
                await vk_client.send_message(message.peer_id, "⚠️ Состояние потеряно. Выбери задание заново.", keyboard=get_task_action_keyboard())
                return
            async with get_session() as session:
                is_correct = _normalize_answer(raw_text) == _normalize_answer(str(expected))
                await TaskService(session).record_answer(vk_id, int(task_id), is_correct)
            await vk_client.send_message(
                message.peer_id,
                f"{'✅ Верно!' if is_correct else '❌ Неправильно'}\nПравильный ответ: {expected}",
                keyboard=get_task_action_keyboard(),
            )
        except ValueError as exc:
            logger.warning("Cannot record answer for VK ID %s: %s", vk_id, exc)
            await vk_client.send_message(
                message.peer_id,
                "⚠️ Задание больше недоступно. Выбери задание заново.",
                keyboard=get_task_action_keyboard(),
            )
        except Exception:
            logger.exception("Error checking answer for VK ID %s", vk_id)
            await vk_client.send_message(message.peer_id, "⚠️ Ошибка при проверке ответа. Попробуйте ещё раз.")
        finally:
            await state_manager.set_state(
                vk_id,
                "after_answer",
                task_mode=state_data.get("task_mode", "random"),
                selected_number=state_data.get("selected_number"),
            )
        return

    if "случайное задание" in normalized or re.fullmatch(r"\d+", raw_text):
        if "случайное задание" in normalized:
            await send_next_task(message, vk_id, "random")
        else:
            number = int(raw_text)
            if 1 <= number <= 20:
                await send_next_task(message, vk_id, "number", number)
            else:
                await vk_client.send_message(message.peer_id, "Пожалуйста, выбери номер задания от 1 до 20")
        return


async def handle_my_stats(message: Message):
    vk_id = message.from_id
    try:
        async with get_session() as session:
            user = await UserService(session).get_or_create_user(vk_id)
            stats = await TaskService(session).get_user_stats(user.id)
            if stats["total"] == 0:
                await vk_client.send_message(message.peer_id, "📈 У тебя пока нет ответов.\nНачни практику!")
                return
            accuracy = stats["correct"] / stats["total"] * 100
            lines = ["📈 Твоя статистика", f"🆔 ID: {user.id}", f"✅ Верно: {stats['correct']}", f"❌ Неверно: {stats['incorrect']}", f"📊 Ответов: {stats['total']}", f"🎯 Точность: {accuracy:.1f}%"]
            if stats["breakdown"]:
                lines.append("\nПо номерам заданий:")
                for number, item in sorted(stats["breakdown"].items()):
                    pct = item["correct"] / item["total"] * 100 if item["total"] else 0
                    lines.append(f"  №{number}: {item['correct']}/{item['total']} ({pct:.0f}%)")
            await vk_client.send_message(message.peer_id, "\n".join(lines))
    except Exception:
        logger.exception("Error getting user statistics for VK ID %s", vk_id)
        await vk_client.send_message(message.peer_id, "📈 Статистика временно недоступна.")


async def handle_bot_stats(message: Message):
    try:
        async with get_session() as session:
            service = StatsService(session)
            stats = await service.get_bot_stats()
            accuracy_by_task = await service.get_accuracy_by_task_number()
            top_users = await service.get_top_users(5)
        lines = ["📊 Общая статистика бота", f"📅 Данные на {stats['date']}", "", f"👥 Пользователей: {stats['users_count']}", f"• Новых сегодня: {stats['new_users_today']}", f"• Активных за неделю: {stats['active_users_week']}", "", "📝 Ответы", f"• Всего: {stats['total_answers']}", f"• Сегодня: {stats['answers_today']}", f"• Верных: {stats['correct_answers']}", f"• Неверных: {stats['incorrect_answers']}", f"• Средняя точность: {stats['avg_accuracy']:.1f}%", "", f"📚 Заданий: {stats['tasks_total']}"]
        if accuracy_by_task:
            lines.extend(["", "Точность по номерам:"])
            lines.extend(f"• №{item['number']}: {item['accuracy']:.0f}% ({item['correct']}/{item['total']})" for item in accuracy_by_task)
        if top_users:
            lines.extend(["", "🏆 Топ активных"])
            lines.extend(f"{position}. {item['first_name']}: {item['total']} ответов, точность {item['accuracy']:.0f}%" for position, item in enumerate(top_users, 1))
        else:
            lines.extend(["", "Пока нет данных об активности."])
        await vk_client.send_message(message.peer_id, "\n".join(lines))
    except Exception:
        logger.exception("Error getting bot statistics")
        await vk_client.send_message(message.peer_id, " Статистика временно недоступна")