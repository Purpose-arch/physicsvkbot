"""
Рендеринг текста задания с LaTeX-формулами в PNG-изображение.

Использует matplotlib mathtext: не требует установленного LaTeX-дистрибутива.
Формулы пишутся между $...$, остальной текст выводится обычным шрифтом.

Ключевой момент: строка с формулами собирается из отдельных сегментов
(обычный текст и $...$-формулы) через offsetbox (HPacker + TextArea), чтобы
mathtext парсил ТОЛЬКО формулы, а кириллица рисовалась обычным шрифтом.
"""

import io
import logging
import re

import matplotlib

matplotlib.use("Agg")  # без GUI
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.offsetbox import (  # noqa: E402
    AnchoredOffsetbox,
    HPacker,
    TextArea,
    VPacker,
)

logger = logging.getLogger(__name__)

# Разбиваем текст на токены: формулы $...$ и обычный текст
_TOKEN_RE = re.compile(r"(\$[^$]+\$)")

# Ширина строки в символах обычного текста (примерно)
_TEXT_WRAP = 72

# mathtext не поддерживает некоторые команды LaTeX — заменяем на эквиваленты
_MATHTEXT_REPLACEMENTS = {
    r"\dfrac": r"\frac",
    r"\tfrac": r"\frac",
    r"\displaystyle": "",
    r"\left": "",
    r"\right": "",
    r"\quad": r"\ \ ",
    r"\qquad": r"\ \ \ \ ",
    r"\text{": r"\mathrm{",
}

# Псевдонимы команд, которые нужно чинить по границе слова (чтобы \le не ломало \leq)
_MATHTEXT_REGEX_REPLACEMENTS = [
    (re.compile(r"\\le(?![a-zA-Z])"), r"\\leq"),
    (re.compile(r"\\ge(?![a-zA-Z])"), r"\\geq"),
]


def _sanitize_formula(formula: str) -> str:
    """Заменяет неподдерживаемые mathtext'ом команды LaTeX на эквиваленты."""
    body = formula[1:-1]  # убираем обрамляющие $
    for pattern, dst in _MATHTEXT_REGEX_REPLACEMENTS:
        body = pattern.sub(dst, body)
    for src, dst in _MATHTEXT_REPLACEMENTS.items():
        body = body.replace(src, dst)
    return f"${body}$"


def _wrap_plain_text(text: str, width: int = _TEXT_WRAP) -> list[str]:
    """Переносит длинные строки без формул по словам."""
    lines: list[str] = []
    for raw_line in text.split("\n"):
        if len(raw_line) <= width:
            lines.append(raw_line)
            continue
        words = raw_line.split(" ")
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) > width and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines


def _line_segments(raw_line: str) -> list[str]:
    """
    Разбивает строку на сегменты: формулы оставляет в $...$,
    обычный текст — как есть. Порядок сохраняется.
    """
    segments: list[str] = []
    for part in _TOKEN_RE.split(raw_line):
        if not part:
            continue
        if part.startswith("$") and part.endswith("$") and len(part) > 2:
            segments.append(_sanitize_formula(part))
        else:
            segments.append(part)
    return segments


def _build_lines(task_text: str, title: str) -> list[list[str]]:
    """Готовит список строк, каждая — список сегментов (текст/формула)."""
    lines: list[list[str]] = []

    if title:
        lines.append([title])
        lines.append([" "])  # пустая строка-разделитель

    for raw_line in task_text.split("\n"):
        if not raw_line.strip():
            lines.append([" "])
            continue

        if "$" in raw_line:
            # Строку с формулами не переносим автоматически:
            # разбивку на строки задаёт LLM на этапе подготовки.
            lines.append(_line_segments(raw_line))
        else:
            for wrapped in _wrap_plain_text(raw_line):
                lines.append([wrapped])

    return lines


def render_task_text(task_text: str, title: str = "") -> bytes:
    """
    Рендерит текст задания (с $...$-формулами) в PNG.

    :param task_text: текст задания, формулы в $...$
    :param title: заголовок (например "Задание №5")
    :return: байты PNG-изображения
    """
    lines = _build_lines(task_text, title)
    if not lines:
        raise ValueError("Пустой текст задания")

    font_size = 14
    line_sep = 8

    # Каждая строка — горизонтальный стек сегментов; строки — вертикальный стек.
    line_boxes = []
    for segments in lines:
        seg_boxes = [
            TextArea(segment, textprops={"fontsize": font_size, "color": "black"})
            for segment in segments
        ]
        line_boxes.append(
            HPacker(children=seg_boxes, align="baseline", pad=0, sep=0)
        )

    vbox = VPacker(children=line_boxes, align="left", pad=0, sep=line_sep)

    fig = plt.figure(figsize=(10, 10), dpi=150)
    fig.patch.set_facecolor("white")

    anchored = AnchoredOffsetbox(
        loc="upper left",
        child=vbox,
        pad=0,
        frameon=False,
        bbox_to_anchor=(0.0, 1.0),
        bbox_transform=fig.transFigure,
        borderpad=0.0,
    )
    anchored.set_clip_on(False)
    fig.add_artist(anchored)

    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        bbox_inches="tight",
        facecolor="white",
        pad_inches=0.3,
    )
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()