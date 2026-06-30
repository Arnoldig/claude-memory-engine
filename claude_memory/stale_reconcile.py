"""Страж устаревших уроков: чек-лист памяти на фразу закрытия сессии + бэкстоп на SessionEnd.

Проблема. Когда сессия МЕНЯЕТ поведение/факт, старые уроки, описывавшие прежнее
состояние, остаются жить и противоречат новому. Хук applies_to показывает их во время
правки, но они читаются как «правило-соблюсти», а не как «факт, который мой фикс мог
сделать ЛОЖНЫМ». Текстовое правило «сначала актуализируй старые» игнорируется
(рецидив дважды — владелец ловил оба раза).

Решение. Тот же сигнал, но в надёжный момент и в нужной рамке. Когда сообщение пользователя
совпадает с session_close_pattern (фраза закрытия сессии), на UserPromptSubmit в контекст
подаётся чек-лист итогов памяти: уроки, привязанные к правленым файлам и НЕ актуализированные
(«не устарели ли?»), смысловой список связанных, статус сроков/архива и список включённых/
выключенных стражей. Это надёжнее прежней привязки к закрывающему коммиту: триггер — явная
фраза пользователя, не зависит от коммита/PR/скилла. Плюс бэкстоп: те же кандидаты пишутся в
_stale_pending на SessionEnd (показ на следующем старте) — на случай, если фразу не написали.

Источник сигнала уже есть: pre-edit-guard на каждой правке файла-с-уроками пишет метку
applies-gate. Здесь метка обогащается именами показанных уроков; плюс ведётся метка «урок
реально отредактирован в этой сессии». Кандидаты = показанные МИНУС тронутые.

Всё session-scoped в tmpdir, без ИИ и без сети. Fail-open на любой ошибке: страж памяти
не должен мешать работе.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .config import MemoryConfig, get_config
from .messages import msg
from .stop_check import last_commit_msg

# Префиксы session-scoped меток в tmpdir. APPLIES_GATE_PREFIX — единый источник имени:
# его импортирует hooks_cli.ev_pre_edit_guard (чтобы метка «уроки показаны» и сбор
# кандидатов читали один и тот же путь). Совпадает со старым значением в hooks_cli.
APPLIES_GATE_PREFIX = "claude-applies-gate-"
EDITED_LESSON_PREFIX = "claude-lesson-edited-"
EDITED_FILE_PREFIX = "claude-edited-file-"


def _sid(session_id: str) -> str:
    return session_id or "nosess"


def _digest(s: str) -> str:
    """sha256 (НЕ встроенный hash(): он рандомизируется per-process через PYTHONHASHSEED —
    каждый запуск хука = свой процесс = другое имя метки → разовость сломалась бы)."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ── метка «уроки по файлу показаны» (обогащённая именами уроков) ──────────────

def applies_gate_dir(session_id: str, tmpdir: str) -> Path:
    return Path(tmpdir) / f"{APPLIES_GATE_PREFIX}{_sid(session_id)}"


def applies_marker_path(session_id: str, file_path: str, tmpdir: str) -> Path:
    """Путь метки для (сессия, файл): <tmpdir>/claude-applies-gate-<sid>/<sha256(abspath)>."""
    return applies_gate_dir(session_id, tmpdir) / _digest(os.path.abspath(file_path))


def write_applies_marker(marker: Path, file_path: str, lessons: List[str]) -> None:
    """Записать метку с именами показанных уроков (JSON {file, lessons}). Fail-silent —
    tmp недоступен → разовость applies-gate всё равно отработает как существование пути."""
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"file": file_path, "lessons": list(lessons)}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


# ── метка «урок реально отредактирован в этой сессии» ─────────────────────────

def edited_lesson_dir(session_id: str, tmpdir: str) -> Path:
    return Path(tmpdir) / f"{EDITED_LESSON_PREFIX}{_sid(session_id)}"


def record_edited_lesson(session_id: str, file_path: str, tmpdir: str) -> None:
    """Отметить, что файл-урок реально отредактирован в этой сессии (по basename). Fail-silent."""
    base = os.path.basename(file_path)
    if not base:
        return
    marker = edited_lesson_dir(session_id, tmpdir) / _digest(base)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(base, encoding="utf-8")
    except OSError:
        pass


# ── метка «проектный файл правлен в этой сессии» (для смыслового поиска) ───────

def edited_file_dir(session_id: str, tmpdir: str) -> Path:
    return Path(tmpdir) / f"{EDITED_FILE_PREFIX}{_sid(session_id)}"


def record_edited_file(session_id: str, file_path: str, tmpdir: str) -> None:
    """Запомнить путь правленого ПРОЕКТНОГО файла (с уроками или без). Из этих путей плюс
    темы закрывающего коммита строится запрос к смысловому поисковику — он находит
    СВЯЗАННЫЕ по смыслу уроки, даже не привязанные к пути. Fail-silent."""
    if not file_path:
        return
    marker = edited_file_dir(session_id, tmpdir) / _digest(os.path.abspath(file_path))
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(file_path, encoding="utf-8")
    except OSError:
        pass


def gather_edited_files(session_id: str, tmpdir: str) -> List[str]:
    """Список путей проектных файлов, правленных в этой сессии."""
    out: List[str] = []
    d = edited_file_dir(session_id, tmpdir)
    if not d.is_dir():
        return out
    for mf in d.iterdir():
        try:
            p = mf.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if p:
            out.append(p)
    return out


# ── сбор кандидатов «показан, но не тронут» ───────────────────────────────────

def gather_shown(session_id: str, tmpdir: str) -> Dict[str, Set[str]]:
    """{урок -> множество файлов, на правке которых он показан} из applies-gate меток сессии.

    Старые метки с телом «1» (до обогащения) или битый JSON — пропускаем (fail-open)."""
    out: Dict[str, Set[str]] = {}
    d = applies_gate_dir(session_id, tmpdir)
    if not d.is_dir():
        return out
    for mf in d.iterdir():
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        file_path = str(data.get("file") or "")
        for lesson in data.get("lessons") or []:
            out.setdefault(str(lesson), set()).add(file_path)
    return out


def gather_edited(session_id: str, tmpdir: str) -> Set[str]:
    """Множество basename'ов уроков, отредактированных в этой сессии."""
    out: Set[str] = set()
    d = edited_lesson_dir(session_id, tmpdir)
    if not d.is_dir():
        return out
    for mf in d.iterdir():
        try:
            b = mf.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if b:
            out.add(b)
    return out


def _candidates_from(shown: Dict[str, Set[str]], edited: Set[str]) -> Dict[str, List[str]]:
    """Чистая логика «показан минус тронут» — единый источник и для candidates(), и для
    build_session_checklist (он переиспользует shown/edited ещё и для exclude смыслового
    списка). {урок -> отсортированный список файлов, которые его триггерили}."""
    return {
        lesson: sorted(f for f in files if f)
        for lesson, files in shown.items()
        if lesson not in edited
    }


def candidates(session_id: str, tmpdir: str) -> Dict[str, List[str]]:
    """Уроки на пере-проверку: показаны на правках файлов, но сами НЕ тронуты в этой сессии."""
    return _candidates_from(gather_shown(session_id, tmpdir), gather_edited(session_id, tmpdir))


def format_candidates(cands: Dict[str, List[str]], cfg: MemoryConfig) -> str:
    """Точный список кандидатов как строки `- урок (привязан к: файлы)`."""
    lines = []
    for lesson in sorted(cands):
        files = ", ".join(os.path.basename(f) for f in cands[lesson] if f)
        lines.append(msg(cfg, "stale_reconcile.item", lesson=lesson, files=files))
    return "\n".join(lines)


# ── смысловой список: связанные уроки, НЕ привязанные по пути ──────────────────

def related_lessons(
    cfg: MemoryConfig, cwd: str, session_id: str, tmpdir: str, exclude: Set[str]
) -> List[Tuple[str, str]]:
    """Уроки, близкие по СМЫСЛУ к тому, что сессия меняла (запрос = правленые файлы плюс
    тема закрывающего коммита), которых нет в exclude. Закрывает дыру «урок про ту же тему,
    но без applies_to на тронутый код». Шумнее точного списка → подаётся как совет.

    Возвращает [(имя_урока, метка)] выше порога cfg.retrieve_threshold, до cfg.retrieve_top_n."""
    files = gather_edited_files(session_id, tmpdir)
    if not files:
        return []
    query = " ".join(files) + " " + last_commit_msg(cwd)
    from .memory_retrieve import score_files  # ленивый импорт: ретривер нужен только тут
    out: List[Tuple[str, str]] = []
    for score, base, label in score_files(query, cfg):
        if base in exclude:
            continue
        if score < cfg.retrieve_threshold:  # ranked по убыванию → ниже порога дальше не смотрим
            break
        out.append((base, label))
        if len(out) >= cfg.retrieve_top_n:
            break
    return out


def format_related(related: List[Tuple[str, str]], cfg: MemoryConfig) -> str:
    """Блок «возможно связаны по смыслу» или "" (пусто). Ведущий двойной перевод строки
    плюс заголовок — чтобы аккуратно встать в шаблон через плейсхолдер {related}."""
    if not related:
        return ""
    items = [msg(cfg, "stale_reconcile.related_item", lesson=base, label=label) for base, label in related]
    return "\n\n" + msg(cfg, "stale_reconcile.related_header") + "\n" + "\n".join(items)


# ── фраза закрытия сессии + чек-лист итогов (UserPromptSubmit) ─────────────────

def matches_close_phrase(prompt: str, cfg: MemoryConfig) -> bool:
    """Совпадает ли сообщение пользователя с session_close_pattern. Пустой шаблон,
    пустой prompt или битый regex → False (fail-open: чек-лист просто не выводится).
    Регистр учитывается по session_close_case_sensitive."""
    pattern = getattr(cfg, "session_close_pattern", "") or ""
    if not pattern or not prompt:
        return False
    flags = 0 if getattr(cfg, "session_close_case_sensitive", False) else re.IGNORECASE
    try:
        return re.search(pattern, prompt, flags) is not None
    except re.error:
        return False


def _guard_states(cfg: MemoryConfig) -> Tuple[List[str], List[str]]:
    """([включённые], [выключенные]) — короткие локализуемые метки стражей по флагам конфига.
    Чтобы пользователь в чек-листе видел, что реально работает, а что нет."""
    guards = [
        ("stale_reconcile.guard.stale_lessons", bool(getattr(cfg, "stale_reconcile_gate", False))),
        ("stale_reconcile.guard.record_lessons", bool(getattr(cfg, "stop_lessons_enabled", False))),
        ("stale_reconcile.guard.task_close", bool(getattr(cfg, "task_close_lesson_gate", False))),
        ("stale_reconcile.guard.archive_age", (getattr(cfg, "archive_stale_months", 0) or 0) > 0),
        ("stale_reconcile.guard.lesson_count", (getattr(cfg, "lesson_count_warn", 0) or 0) > 0),
        ("stale_reconcile.guard.model_registry",
         bool(getattr(cfg, "llm_actuality_enabled", False) or getattr(cfg, "known_model_substrs", ()))),
    ]
    on = [msg(cfg, key) for key, active in guards if active]
    off = [msg(cfg, key) for key, active in guards if not active]
    return on, off


def _shelf_has_pending(cfg: MemoryConfig) -> bool:
    """Есть ли непустой _stale_pending.md (отложенный долг устаревания/архива). Лёгкая
    проверка одного файла — не запускаем тяжёлый скан проекта на каждую фразу закрытия."""
    from .staleness import STALE_FILE  # ленивый импорт: константа имени файла долга
    p = Path(cfg.memory_dir) / STALE_FILE
    try:
        return p.is_file() and bool(p.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def build_session_checklist(
    cfg: MemoryConfig, cwd: str, session_id: str, tmpdir: str
) -> str:
    """Чек-лист итогов памяти для показа пользователю на фразе закрытия. Всегда непустой:
    счётчики (показано/актуализировано/осталось) + (кандидаты на устаревание | «чисто») +
    смысловой список (ВСЕГДА при правленых файлах — отвязан от точного) + статус сроков/архива
    + включённые/выключенные стражи + директива мне (только если есть что чинить)."""
    shown = gather_shown(session_id, tmpdir)
    edited = gather_edited(session_id, tmpdir)
    precise = _candidates_from(shown, edited)
    remaining = len(precise)
    reconciled = max(0, len(shown) - remaining)

    lines = [
        msg(cfg, "stale_reconcile.checklist.header"),
        msg(cfg, "stale_reconcile.checklist.counts",
            shown=len(shown), reconciled=reconciled, remaining=remaining),
    ]
    if precise:
        lines.append(msg(cfg, "stale_reconcile.checklist.candidates_header"))
        lines.append(format_candidates(precise, cfg))
    else:
        lines.append(msg(cfg, "stale_reconcile.checklist.clean"))
    related = related_lessons(cfg, cwd, session_id, tmpdir, exclude=set(shown) | edited)
    rel = format_related(related, cfg)
    if rel:
        lines.append(rel.lstrip("\n"))
    lines.append(msg(cfg, "stale_reconcile.checklist.shelf_pending"
                          if _shelf_has_pending(cfg) else "stale_reconcile.checklist.shelf_clean"))
    from .llm_actuality import checklist_line as _llm_line  # ленивый импорт: статус сверки линейки
    llm = _llm_line(cfg)
    if llm:
        lines.append(llm)
    on, off = _guard_states(cfg)
    lines.append(msg(cfg, "stale_reconcile.checklist.guards_on", guards=", ".join(on) or "—"))
    lines.append(msg(cfg, "stale_reconcile.checklist.guards_off", guards=", ".join(off) or "—"))
    if precise:
        lines.append(msg(cfg, "stale_reconcile.checklist.directive"))
    return "\n".join(line for line in lines if line)


def reconcile_on_close(
    cfg: Optional[MemoryConfig], prompt: str, cwd: str, session_id: str, tmpdir: str
) -> Optional[str]:
    """Чек-лист итогов памяти, если сообщение пользователя — фраза закрытия. Иначе None.

    Заменяет прежний reconcile_reminder (тот зависел от закрывающего коммита и блокировал
    Stop). Триггер теперь — session_close_pattern на UserPromptSubmit: чек-лист подаётся в
    контекст (не блокирует), не разовый. Fail-open. Управляется stale_reconcile_gate."""
    cfg = cfg or get_config()
    if not getattr(cfg, "stale_reconcile_gate", False):
        return None
    if not matches_close_phrase(prompt, cfg):
        return None
    return build_session_checklist(cfg, cwd, session_id, tmpdir)
