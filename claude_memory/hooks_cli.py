"""Единый диспетчер хук-логики движка памяти.

Раньше логика хуков была раскидана по bash-скриптам (каждый со своей Python-вставкой).
Здесь она собрана в один Python-модуль: тонкие bash-обёртки (hooks/*.sh) лишь задают
окружение (PYTHONPATH к движку, путь к конфигу) и вызывают `python3 -m claude_memory.hooks_cli <event>`.
Это тестируемо и не дублирует логику.

Протокол хуков Claude Code, который мы используем:
- stdin — JSON события (`session_id`, `tool_name`, `tool_input`, `prompt`, …);
- инъекция в контекст (UserPromptSubmit / SessionStart) — печать в stdout, exit 0;
- блокировка инструмента (PreToolUse-страж) — печать причины в stderr, exit 2;
- обслуживание/замер (PostToolUse / SessionEnd) — тихо, exit 0.

Любая ошибка — fail-open (exit 0, ничего не ломаем): память не должна мешать работе.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from . import (
    catalog_generate,
    memory_archive,
    memory_concurrency,
    memory_retrieve,
    precedent_index,
    session_marker_guard,
    subagent_efficiency_log,
    subagent_model_guard,
)
from .applies_to import find_lessons_for_path, format_lines
from .config import MemoryConfig, get_config

_APPLIES_MARKER = "claude-applies-gate-"  # маркер «урок по пути показан» (сессия+файл)


def _read_event() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        return {}


def _deny(reason: str) -> None:
    print(reason, file=sys.stderr)
    sys.exit(2)


def _emit(text: str) -> None:
    if text:
        print(text)
    sys.exit(0)


# ── Отдельные события (чистые, тестируемые) ──────────────────────────────────

def ev_retrieve(event: dict, cfg: MemoryConfig) -> str:
    """UserPromptSubmit: релевантные уроки в контекст (или тишина)."""
    query = str(event.get("prompt") or "")
    if not query.strip():
        return ""
    return memory_retrieve.run(query, hook_mode=True, cfg=cfg)


def ev_session_start(cfg: MemoryConfig) -> str:
    """SessionStart: пересобрать CATALOG, освежить индекс прецедентов, вернуть пульс здоровья."""
    out_lines = []
    try:
        text, diag = catalog_generate.build_catalog(cfg.memory_dir, cfg)
        cat = Path(cfg.memory_dir) / cfg.catalog_file
        tmp = cat.with_name(cfg.catalog_file + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, cat)
        pulse = catalog_generate.format_health_pulse(diag, cfg)
        if pulse:
            out_lines.append(pulse)
    except OSError:
        pass
    # индекс прецедентов + шапка-предупреждение по каждому архиву
    arc_dir = Path(cfg.memory_dir) / "archive"
    if arc_dir.is_dir():
        for arc in sorted(arc_dir.glob("precedents-*.md")):
            if arc.name.endswith("-INDEX.md"):
                continue
            try:
                raw = arc.read_text(encoding="utf-8")
                arc.write_text(precedent_index.add_warning_header(raw), encoding="utf-8")
                idx = precedent_index.render_index(precedent_index.parse_cards(raw, cfg), arc.name)
                idx_path = arc.with_name(arc.stem + "-INDEX.md")
                idx_path.write_text(idx, encoding="utf-8")
            except OSError:
                continue
    return "\n".join(out_lines)


def ev_pre_edit_guard(event: dict, cfg: MemoryConfig, session_id: str, tmpdir: str) -> Optional[str]:
    """PreToolUse Edit|Write|MultiEdit: формат маркера → конфликт версий → уроки по пути.

    Возвращает причину deny или None. Проверки по убыванию строгости; первая сработавшая
    блокирует.
    """
    tool_name = str(event.get("tool_name") or "")
    tool_input = event.get("tool_input") or {}

    # 1) формат session-маркера (deny не разовый — пока не исправят)
    reason = session_marker_guard.violation_reason(tool_name, tool_input, cfg)
    if reason:
        return reason

    file_path = str(tool_input.get("file_path") or "") if isinstance(tool_input, dict) else ""
    if not file_path:
        return None

    # 2) конфликт параллельных сессий — только для файлов памяти
    try:
        in_memory = os.path.abspath(file_path).startswith(os.path.abspath(cfg.memory_dir))
    except (OSError, ValueError):
        in_memory = False
    if in_memory:
        c = memory_concurrency.conflict_reason(session_id, file_path, tmpdir)
        if c:
            return c

    # 3) уроки по пути файла (applies_to) — разово на (сессию, файл), вне памяти/.claude
    norm = file_path.replace("\\", "/")
    if "/.claude/" not in norm and not in_memory:
        # sha256 (НЕ встроенный hash()): hash() строк рандомизируется per-process
        # (PYTHONHASHSEED) → каждый запуск хука = свой процесс = другое имя маркера →
        # разовость «раз на файл за сессию» сломалась бы (как в memory_concurrency.marker_path).
        digest = hashlib.sha256(os.path.abspath(file_path).encode("utf-8")).hexdigest()
        marker = Path(tmpdir) / f"{_APPLIES_MARKER}{session_id or 'nosess'}" / digest
        if not marker.exists():
            matches = find_lessons_for_path(file_path, cfg)
            if matches:
                try:
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    marker.write_text("1", encoding="utf-8")
                except OSError:
                    pass
                return (
                    "Уроки, привязанные к этому файлу (applies_to) — прочитай ДО первой "
                    "правки (показывается один раз за сессию на файл):\n"
                    + format_lines(matches)
                    + "\nПосле прочтения повтори правку — она пройдёт. [applies-to-gate]"
                )
    return None


def ev_post_record(event: dict, cfg: MemoryConfig, session_id: str, tmpdir: str) -> None:
    """PostToolUse Read|Write|Edit|MultiEdit: запомнить on-disk версию файла памяти (CAS)."""
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return
    file_path = str(tool_input.get("file_path") or "")
    if not file_path:
        return
    try:
        if os.path.abspath(file_path).startswith(os.path.abspath(cfg.memory_dir)):
            memory_concurrency.record_seen(session_id, file_path, tmpdir)
    except (OSError, ValueError):
        return


def ev_bloat_check(event: dict, cfg: MemoryConfig, today: Optional[datetime.date] = None) -> str:
    """PostToolUse Write|Edit на файле памяти: авто-архив старого + предупреждение о размере."""
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return ""
    file_path = str(tool_input.get("file_path") or "")
    if not file_path:
        return ""
    p = Path(file_path)
    try:
        if not os.path.abspath(file_path).startswith(os.path.abspath(cfg.memory_dir)):
            return ""
    except (OSError, ValueError):
        return ""
    if not p.is_file():
        return ""
    warnings = []
    # авто-архив маркеров в транзитном session-файле
    if p.name == cfg.session_lessons_file:
        try:
            memory_archive.archive_old_session_markers(p, today=today, threshold_days=cfg.marker_archive_days)
        except OSError:
            pass
    # авто-архив прецедентов в любом feedback-файле
    if p.name.startswith(cfg.lesson_prefixes[0]):
        try:
            memory_archive.archive_old_precedents(
                p, today=today, threshold_days=cfg.precedent_archive_days, cfg=cfg
            )
        except OSError:
            pass
    # предупреждение о размере: ядро vs обычный урок
    try:
        size = p.stat().st_size
    except OSError:
        return ""
    if p.name == cfg.core_file and size > cfg.core_budget_bytes:
        warnings.append(
            f"[память] {cfg.core_file} = {size}б > бюджета {cfg.core_budget_bytes}б — "
            "ужми горячее ядро (вынеси детали в обычные уроки)."
        )
    elif p.name != cfg.core_file and size > cfg.feedback_warn_bytes:
        warnings.append(
            f"[память] {p.name} = {size}б > {cfg.feedback_warn_bytes}б — "
            "крупный урок, рассмотри разбиение."
        )
    return "\n".join(warnings)


def ev_agent_guard(event: dict, cfg: MemoryConfig, session_id: str, tmpdir: str) -> Optional[str]:
    """PreToolUse Agent: страж выбора модели суб-агента (разовый нудж)."""
    return subagent_model_guard.gate(
        session_id, str(event.get("tool_name") or ""), event.get("tool_input") or {}, tmpdir, cfg
    )


def ev_agent_log(event: dict, cfg: MemoryConfig, session_id: str, now_iso: str) -> None:
    """PostToolUse Agent: записать строку в журнал эффективности делегирования."""
    line = subagent_efficiency_log.format_record(session_id, event.get("tool_input") or {}, now_iso)
    log = os.path.join(cfg.memory_dir, "_subagent_efficiency.jsonl")
    subagent_efficiency_log.append_record(log, line)


def ev_pre_compact(cfg: MemoryConfig) -> str:
    """PreCompact: напомнить про бюджет горячего ядра перед сжатием контекста."""
    core = Path(cfg.memory_dir) / cfg.core_file
    try:
        size = core.stat().st_size
    except OSError:
        return ""
    if size > cfg.core_budget_bytes:
        return (
            f"[память] перед компактом: {cfg.core_file} {size}б > {cfg.core_budget_bytes}б — "
            "хороший момент ужать ядро."
        )
    return ""


# ── Диспетчер ────────────────────────────────────────────────────────────────

def main() -> None:
    event_name = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        cfg = get_config()
    except Exception:  # noqa: BLE001 — fail-open: конфиг сломан → не мешаем работе
        sys.exit(0)
    data = _read_event()
    session_id = str(data.get("session_id") or "nosess")
    tmpdir = tempfile.gettempdir()

    try:
        if event_name == "retrieve":
            _emit(ev_retrieve(data, cfg))
        elif event_name == "session-start":
            _emit(ev_session_start(cfg))
        elif event_name == "pre-edit-guard":
            r = ev_pre_edit_guard(data, cfg, session_id, tmpdir)
            if r:
                _deny(r)
        elif event_name == "post-record":
            ev_post_record(data, cfg, session_id, tmpdir)
        elif event_name == "bloat-check":
            _emit(ev_bloat_check(data, cfg))
        elif event_name == "agent-guard":
            r = ev_agent_guard(data, cfg, session_id, tmpdir)
            if r:
                _deny(r)
        elif event_name == "agent-log":
            ev_agent_log(data, cfg, session_id, datetime.datetime.now().isoformat() + "Z")
        elif event_name == "pre-compact":
            _emit(ev_pre_compact(cfg))
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 — любая иная ошибка хука: fail-open
        sys.exit(0)
    sys.exit(0)


if __name__ == "__main__":
    main()
