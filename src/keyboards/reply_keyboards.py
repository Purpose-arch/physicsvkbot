"""
Reply-клавиатуры
"""

import json


class ButtonColor:
    PRIMARY = "primary"      # Синяя
    SECONDARY = "secondary"  # Белая
    POSITIVE = "positive"    # Зеленая
    NEGATIVE = "negative"    # Красная


class VKKeyboard:
    """
    Генератор клавиатур ВКонтакте по официальной спецификации VK API
    """
    def __init__(self, one_time: bool = False, inline: bool = False):
        self.one_time = one_time
        self.inline = inline
        self.buttons = [[]]

    def add(self, label: str, color: str = ButtonColor.SECONDARY, payload: dict = None):
        button_dict = {
            "action": {
                "type": "text",
                "label": label,
            },
            "color": color,
        }
        if payload is not None:
            button_dict["action"]["payload"] = (
                json.dumps(payload, ensure_ascii=False)
                if isinstance(payload, dict)
                else str(payload)
            )

        self.buttons[-1].append(button_dict)
        return self

    def row(self):
        if self.buttons[-1]:
            self.buttons.append([])
        return self

    def get_json(self) -> str:
        valid_rows = [r for r in self.buttons if r]
        data = {
            "one_time": self.one_time,
            "inline": self.inline,
            "buttons": valid_rows,
        }
        return json.dumps(data, ensure_ascii=False)

    def __str__(self):
        return self.get_json()


def get_main_menu_keyboard(is_admin: bool = False) -> str:
    """
    Генерирует главное меню с кнопками заданий 1-20
    """
    kb = VKKeyboard(one_time=False)

    if is_admin:
        kb.add("⚙️ Админ панель", color=ButtonColor.PRIMARY)
        kb.row()

    kb.add("🎲 Случайное задание", color=ButtonColor.POSITIVE)
    kb.add("📈 Моя статистика", color=ButtonColor.SECONDARY)
    kb.row()

    # Сетка заданий 1–20 (по 5 в ряд)
    for i in range(1, 21):
        kb.add(str(i), color=ButtonColor.SECONDARY)
        if i % 5 == 0 and i != 20:
            kb.row()

    return kb.get_json()


def get_admin_panel_keyboard() -> str:
    """
    Генерирует клавиатуру админ-панели с админскими действиями.
    """
    kb = VKKeyboard(one_time=False)
    kb.add("➕ Добавить задание", color=ButtonColor.POSITIVE)
    kb.add("📥 Массовое добавление", color=ButtonColor.POSITIVE)
    kb.row()
    kb.add("🗑️ Очистить данные", color=ButtonColor.NEGATIVE)
    kb.row()
    kb.add("⬅️ Главное меню", color=ButtonColor.PRIMARY)
    kb.add("📊 Статистика", color=ButtonColor.SECONDARY)
    return kb.get_json()


def get_task_number_selection_keyboard() -> str:
    """
    Клавиатура с кнопками 1-20 для админа при выборе номера создаваемого задания
    """
    kb = VKKeyboard(one_time=False)
    kb.add("⬅️ Админ панель", color=ButtonColor.PRIMARY)
    kb.row()

    # Сетка 1–20 для быстрого нажатия
    for i in range(1, 21):
        kb.add(str(i), color=ButtonColor.SECONDARY)
        if i % 5 == 0 and i != 20:
            kb.row()

    return kb.get_json()


def get_bulk_addition_keyboard() -> str:
    """Клавиатура в режиме массового добавления заданий"""
    kb = VKKeyboard(one_time=False)
    kb.add("⬅️ Админ панель", color=ButtonColor.SECONDARY)
    return kb.get_json()


def get_task_keyboard() -> str:
    """Клавиатура во время решения задания — видна только кнопка выхода"""
    kb = VKKeyboard(one_time=False)
    kb.add("⬅️ Главное меню", color=ButtonColor.SECONDARY)
    return kb.get_json()


def get_task_addition_keyboard() -> str:
    """Клавиатура при добавлении задания — видна только кнопка выхода"""
    kb = VKKeyboard(one_time=False)
    kb.add("⬅️ Админ панель", color=ButtonColor.SECONDARY)
    return kb.get_json()