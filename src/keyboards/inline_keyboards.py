"""
Инлайн-клавиатуры
"""

import json


class ButtonColor:
    PRIMARY = "primary"
    SECONDARY = "secondary"
    POSITIVE = "positive"
    NEGATIVE = "negative"


class VKKeyboard:
    """
    Генератор инлайн-клавиатур ВКонтакте по официальной спецификации VK API
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


def get_task_action_keyboard() -> str:
    """Генерирует инлайн-клавиатуру действий с заданием"""
    kb = VKKeyboard(inline=True)
    kb.add("⏭️ Следующее задание", color=ButtonColor.PRIMARY, payload={"action": "next_task"})
    kb.row()
    kb.add("⬅️ Главное меню", color=ButtonColor.SECONDARY, payload={"action": "exit_to_menu"})
    return kb.get_json()
