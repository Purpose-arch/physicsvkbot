"""
Reply-клавиатуры
"""

from src.keyboards.vk_keyboard import ButtonColor, VKKeyboard


def get_main_menu_keyboard() -> str:
    """
    Генерирует главное меню с кнопками заданий 1-20
    """
    kb = VKKeyboard(one_time=False)

    kb.add("🎲 Случайное задание", color=ButtonColor.POSITIVE)
    kb.add("📈 Моя статистика", color=ButtonColor.SECONDARY)
    kb.row()
    kb.add("📊 Общая статистика", color=ButtonColor.PRIMARY)
    kb.row()

    # Сетка заданий 1–20 (по 5 в ряд)
    for i in range(1, 21):
        kb.add(str(i), color=ButtonColor.SECONDARY)
        if i % 5 == 0 and i != 20:
            kb.row()

    return kb.get_json()


def get_task_keyboard() -> str:
    """Клавиатура во время решения задания — видна только кнопка выхода"""
    kb = VKKeyboard(one_time=False)
    kb.add("⬅️ Главное меню", color=ButtonColor.SECONDARY)
    return kb.get_json()