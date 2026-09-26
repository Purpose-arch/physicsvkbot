"""
Инлайн-клавиатуры
"""

from src.keyboards.vk_keyboard import ButtonColor, VKKeyboard


def get_task_action_keyboard() -> str:
    """Генерирует инлайн-клавиатуру действий с заданием"""
    kb = VKKeyboard(inline=True)
    kb.add("⏭️ Следующее задание", color=ButtonColor.PRIMARY, payload={"action": "next_task"})
    kb.row()
    kb.add("⬅️ Главное меню", color=ButtonColor.SECONDARY, payload={"action": "exit_to_menu"})
    return kb.get_json()