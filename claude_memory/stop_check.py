"""Напоминание про уроки при завершении (Stop): если есть свежий коммит, после которого
урок в память НЕ записан — блокируем завершение turn'а с просьбой зафиксировать вывод.

Текстовое напоминание «записывай уроки» легко игнорируется; блокирующий страж в точке
завершения — нет. Срабатывает, только если последний коммит свежее самого свежего
файла-урока И не старше окна (по умолчанию 4 часа). Fail-open на любой ошибке.

Это ОБЩИЙ kernel. Проектные расширения (напр. требование записи в архив прецедентов при
коммите-закрытии задачи) сюда НЕ входят — их держат отдельным проектным хуком.
"""
from __future__ import annotations

import glob
import os
import subprocess
from typing import Optional

from .config import MemoryConfig, get_config


def decide(commit_ts: int, feedback_ts: float, now_ts: float, age_limit: int) -> bool:
    """Чистая логика: блокировать ли. True, если коммит свежий (моложе age_limit) И новее урока."""
    return commit_ts > 0 and (now_ts - commit_ts) < age_limit and commit_ts > feedback_ts


def newest_lesson_mtime(cfg: MemoryConfig) -> float:
    """mtime самого свежего файла-урока (по первому префиксу, напр. feedback_*.md). 0, если нет."""
    prefix = cfg.lesson_prefixes[0] if cfg.lesson_prefixes else "feedback"
    return max(
        (os.path.getmtime(f) for f in glob.glob(os.path.join(cfg.memory_dir, f"{prefix}_*.md"))),
        default=0.0,
    )


def last_commit_ts(cwd: str) -> int:
    """Unix-время последнего git-коммита в cwd (0, если не git / нет коммитов / ошибка)."""
    try:
        out = subprocess.check_output(
            ["git", "-C", cwd, "log", "-1", "--format=%ct"],
            stderr=subprocess.DEVNULL, text=True, timeout=5,
        ).strip()
        return int(out) if out else 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def reminder_message(cfg: MemoryConfig) -> str:
    """Generic-текст напоминания (без проектной методологии — её добавляет проектный хук)."""
    return (
        "Завершение заблокировано: есть свежий коммит, но урок/заметка в память после него "
        "не записаны. Зафиксируй вывод сессии файлом-уроком (или отметь «рутина — урока нет» "
        "в журнале уроков), затем заверши — это снимет блок. [stop-lessons]"
    )


def should_remind(cfg: Optional[MemoryConfig], cwd: str, now_ts: float) -> Optional[str]:
    """Текст блокировки или None. Учитывает флаг включения и окно свежести из конфига."""
    cfg = cfg or get_config()
    if not cfg.stop_lessons_enabled:
        return None
    commit_ts = last_commit_ts(cwd)
    feedback_ts = newest_lesson_mtime(cfg)
    if decide(commit_ts, feedback_ts, now_ts, cfg.stop_commit_age_limit_seconds):
        return reminder_message(cfg)
    return None
