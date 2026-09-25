"""
Построение PNG-диаграмм общей статистики (matplotlib, backend Agg).

Возвращает путь к временному файлу с картинкой либо None, если данных нет
или matplotlib недоступен. Все ошибки глушатся, чтобы статистика никогда
не ломалась из-за рендера.
"""

import logging
import os
import tempfile
from datetime import date

logger = logging.getLogger(__name__)

_BG = "#1e1e2e"
_PANEL_BG = "#27293d"
_ACCENT = "#4f8cff"
_ACCENT2 = "#3fbf7f"
_ACCENT3 = "#ff6b6b"
_TEXT = "#e6e6f0"
_GRID = "#3a3d52"

MIN_DAYS = 7


def _trim_daily(series: list[dict], min_days: int = MIN_DAYS) -> list[dict]:
    """Убирает ведущие дни без активности, оставляя не меньше min_days дней."""
    if not series:
        return series
    first_active = None
    for idx, row in enumerate(series):
        if row.get("total", 0) > 0:
            first_active = idx
            break
    if first_active is None:
        return series[-min_days:] if len(series) > min_days else series
    keep_from = max(0, min(first_active, len(series) - min_days))
    return series[keep_from:]


def _style_axes(ax, title: str):
    ax.set_facecolor(_PANEL_BG)
    ax.set_title(title, color=_TEXT, fontsize=12, fontweight="bold", pad=10)
    ax.tick_params(colors=_TEXT, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(_GRID)
    ax.grid(True, color=_GRID, linewidth=0.6, alpha=0.5)
    ax.set_axisbelow(True)


def _thin_labels(ax):
    for i, tick in enumerate(ax.get_xticklabels()):
        if i % 2 == 1:
            tick.set_visible(False)


def _build_figure(stats, daily_answers, daily_users, accuracy_by_number, top_users):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = "DejaVu Sans"

    daily_answers = _trim_daily(daily_answers)
    daily_users = _trim_daily(daily_users)

    panels = []
    if daily_answers:
        panels.append("answers")
    if daily_users:
        panels.append("users")
    if accuracy_by_number:
        panels.append("accuracy")
    if top_users:
        panels.append("top")
    if not panels:
        return None

    n = len(panels)
    ncols = 2 if n > 1 else 1
    nrows = (n + ncols - 1) // ncols

    fig, axes = plt.subplots(nrows, ncols, figsize=(13, 4.4 * nrows), dpi=120)
    fig.patch.set_facecolor(_BG)
    axes_flat = axes.flatten() if hasattr(axes, "flatten") else [axes]
    for ax in axes_flat[n:]:
        ax.set_visible(False)
    plot_map = {name: axes_flat[i] for i, name in enumerate(panels)}

    if "answers" in plot_map:
        ax = plot_map["answers"]
        labels = [row["date"].strftime("%d.%m") for row in daily_answers]
        totals = [row["total"] for row in daily_answers]
        correct = [row["correct"] for row in daily_answers]
        x = list(range(len(labels)))
        ax.bar(x, totals, color=_ACCENT, alpha=0.85, label="Всего")
        ax.bar(x, correct, color=_ACCENT2, alpha=0.9, label="Верно")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45, ha="right")
        if len(labels) > 16:
            _thin_labels(ax)
        ax.legend(facecolor=_PANEL_BG, edgecolor=_GRID, labelcolor=_TEXT, fontsize=9)
        _style_axes(ax, "Ответы по дням")

    if "users" in plot_map:
        ax = plot_map["users"]
        labels = [row["date"].strftime("%d.%m") for row in daily_users]
        totals = [row["total"] for row in daily_users]
        x = list(range(len(labels)))
        ax.bar(x, totals, color=_ACCENT2, alpha=0.9)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45, ha="right")
        if len(labels) > 16:
            _thin_labels(ax)
        for xi, val in zip(x, totals):
            if val:
                ax.text(xi, val, str(val), ha="center", va="bottom", color=_TEXT, fontsize=8)
        _style_axes(ax, "Новые пользователи по дням")

    if "accuracy" in plot_map:
        ax = plot_map["accuracy"]
        numbers = [f"№{row['number']}" for row in accuracy_by_number]
        accuracy = [row["accuracy"] for row in accuracy_by_number]
        y = list(range(len(numbers)))
        colors = [_ACCENT3 if a < 50 else (_ACCENT2 if a >= 80 else _ACCENT) for a in accuracy]
        ax.barh(y, accuracy, color=colors, alpha=0.9)
        ax.set_yticks(y)
        ax.set_yticklabels(numbers)
        ax.invert_yaxis()
        ax.set_xlim(0, 100)
        for yi, val in zip(y, accuracy):
            ax.text(min(val + 1.5, 96), yi, f"{val:.0f}%", va="center", color=_TEXT, fontsize=8)
        _style_axes(ax, "Точность по номерам заданий")

    if "top" in plot_map:
        ax = plot_map["top"]
        names = [u["first_name"][:18] for u in top_users][::-1]
        totals = [u["total"] for u in top_users][::-1]
        accuracy = [u["accuracy"] for u in top_users][::-1]
        y = list(range(len(names)))
        ax.barh(y, totals, color=_ACCENT, alpha=0.9)
        ax.set_yticks(y)
        ax.set_yticklabels(names)
        for yi, val, acc in zip(y, totals, accuracy):
            ax.text(val, yi, f" {val} ({acc:.0f}%)", va="center", color=_TEXT, fontsize=9)
        ax.set_xlim(0, max(totals) * 1.3 if totals else 1)
        _style_axes(ax, "Топ активных учеников")

    title_date = stats.get("date", date.today().strftime("%d.%m.%Y"))
    fig.suptitle(
        f"Общая статистика бота · {title_date}",
        color=_TEXT, fontsize=15, fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))

    fd, path = tempfile.mkstemp(suffix=".png", prefix="bot_stats_")
    os.close(fd)
    fig.savefig(path, facecolor=_BG)
    plt.close(fig)
    return path


def build_stats_chart(
    stats: dict,
    daily_answers: list[dict],
    daily_users: list[dict],
    accuracy_by_number: list[dict],
    top_users: list[dict],
) -> str | None:
    """Строит диаграмму и возвращает путь к PNG, либо None при отсутствии данных/ошибке."""
    try:
        if not any([daily_answers, daily_users, accuracy_by_number, top_users]):
            return None
        return _build_figure(stats, daily_answers, daily_users, accuracy_by_number, top_users)
    except Exception as e:
        logger.error(f"Failed to build stats chart: {e}", exc_info=True)
        return None


def cleanup_chart(path: str | None) -> None:
    """Удаляет временный файл диаграммы."""
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass