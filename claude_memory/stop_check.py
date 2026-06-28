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
import re
import subprocess
from pathlib import Path
from typing import Optional

from .config import MemoryConfig, get_config
from .messages import msg


def decide(commit_ts: int, feedback_ts: float, now_ts: float, age_limit: int) -> bool:
    """Чистая логика: блокировать ли. True, если коммит свежий (моложе age_limit) И новее урока."""
    return commit_ts > 0 and (now_ts - commit_ts) < age_limit and commit_ts > feedback_ts


def newest_lesson_mtime(cfg: MemoryConfig) -> float:
    """mtime самого свежего файла-урока по ВСЕМ префиксам (feedback_/reference_/project_). 0, если нет.

    По всем (а не только первому): иначе запись урока reference_*/project_* после коммита
    не снимала бы Stop-блок — рассинхрон с task_lesson_recorded, который перебирает все."""
    prefixes = cfg.lesson_prefixes or ("feedback",)
    files = []
    for prefix in prefixes:
        files += glob.glob(os.path.join(cfg.memory_dir, f"{prefix}_*.md"))
    return max((os.path.getmtime(f) for f in files), default=0.0)


def last_commit_ts(cwd: str) -> int:
    """Unix-время последнего git-коммита в cwd (0, если не git / нет коммитов / ошибка)."""
    return _git(cwd, "%ct", as_int=True)


def last_commit_msg(cwd: str) -> str:
    """Тема последнего git-коммита в cwd ("" если не git / нет коммитов / ошибка)."""
    return _git(cwd, "%s")


def last_commit_sha(cwd: str) -> str:
    """Полный sha последнего git-коммита в cwd ("" если не git / нет коммитов / ошибка).

    Нужен stale_reconcile для разовости нуджа по (сессия, закрывающий коммит)."""
    return _git(cwd, "%H")


def _git(cwd: str, fmt: str, as_int: bool = False):
    try:
        out = subprocess.check_output(
            ["git", "-C", cwd, "log", "-1", f"--format={fmt}"],
            stderr=subprocess.DEVNULL, text=True, timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return 0 if as_int else ""
    if as_int:
        try:
            return int(out) if out else 0
        except ValueError:
            return 0
    return out


def reminder_message(cfg: MemoryConfig) -> str:
    """Generic-текст напоминания (без проектной методологии — её добавляет проектный хук)."""
    return msg(cfg, "stop_check.reminder_message")


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


# ── Привратник закрытия задачи (Closes #N без записанного урока про задачу) ──────

def extract_closed_task(commit_msg: str, pattern: str) -> Optional[str]:
    """Номер закрываемой задачи из коммита по шаблону (группа 1) или None."""
    if not commit_msg:
        return None
    try:
        m = re.search(pattern, commit_msg)
    except re.error:
        return None
    return m.group(1) if m else None


def task_lesson_recorded(cfg: MemoryConfig, task_id: str) -> bool:
    """Есть ли уже запись про задачу `#task_id`: в файле-уроке (любой префикс) или в
    архиве прецедентов. Ищем хэштег-форму `#<id>` с границей справа — точно и без ложных
    совпадений (`#58` НЕ матчит `#580`/`#58-foo`; id бывает числом ИЛИ слагом)."""
    needle_re = re.compile(r"#" + re.escape(task_id) + r"(?![\w-])")
    candidates: list = []
    mem = Path(cfg.memory_dir)
    for prefix in cfg.lesson_prefixes:
        candidates += glob.glob(str(mem / f"{prefix}_*.md"))
    candidates += glob.glob(str(mem / cfg.archive_dir_name / "*.md"))
    for path in candidates:
        try:
            if needle_re.search(Path(path).read_text(encoding="utf-8")):
                return True
        except OSError:
            continue
    return False


def closure_reminder(cfg: Optional[MemoryConfig], cwd: str) -> Optional[str]:
    """Блок-текст, если последний коммит — закрытие задачи, а урока про неё нет. Иначе None."""
    cfg = cfg or get_config()
    if not cfg.task_close_lesson_gate:
        return None
    task_id = extract_closed_task(last_commit_msg(cwd), cfg.task_close_pattern)
    if not task_id:
        return None
    if task_lesson_recorded(cfg, task_id):
        return None
    return msg(cfg, "stop_check.closure_reminder", task_id=task_id)
