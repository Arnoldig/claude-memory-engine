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
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from . import (
    catalog_generate,
    memory_archive,
    memory_concurrency,
    memory_retrieve,
    llm_actuality,
    precedent_index,
    self_check,
    session_marker_guard,
    stale_reconcile,
    staleness,
    stop_check,
    subagent_efficiency_log,
    subagent_model_guard,
)
from .applies_to import find_lessons_for_path, format_lines
from .config import MemoryConfig, get_config
from .messages import msg


def _read_event() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        return {}


def _deny(reason: str) -> None:
    print(reason, file=sys.stderr)
    sys.exit(2)


def _emit(text: str) -> None:
    """Прямой stdout → контекст (UserPromptSubmit / SessionStart добавляют stdout в контекст)."""
    if text:
        print(text)
    sys.exit(0)


def _emit_post_context(text: str) -> None:
    """PostToolUse: чтобы текст попал в контекст модели, нужен hookSpecificOutput.additionalContext
    (голый stdout PostToolUse в контекст НЕ инжектится)."""
    if text:
        print(json.dumps({
            "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}
        }, ensure_ascii=False))
    sys.exit(0)


def _emit_system_message(text: str) -> None:
    """PreCompact / прочее: системное сообщение пользователю/модели."""
    if text:
        print(json.dumps({"systemMessage": text}, ensure_ascii=False))
    sys.exit(0)


# ── Отдельные события (чистые, тестируемые) ──────────────────────────────────

def ev_retrieve(event: dict, cfg: MemoryConfig) -> str:
    """UserPromptSubmit: релевантные уроки в контекст (или тишина)."""
    query = str(event.get("prompt") or "")
    if not query.strip():
        return ""
    return memory_retrieve.run(query, hook_mode=True, cfg=cfg)


def ev_session_start(event: dict, cfg: MemoryConfig) -> str:
    """SessionStart: проектные ноты + реестр моделей + CATALOG + индекс прецедентов + пульс + долг устаревания."""
    out_lines = []
    # проектные операционные ноты (печатаются как есть; по умолчанию пусто)
    out_lines.extend(n for n in cfg.session_start_notes if n)
    # самодиагностика конфигурации (битые плейсхолдеры) — КАЖДЫЙ старт, не throttle:
    # битая настройка тихо портит весь сеанс, должна быть видна сразу и пока не исправят.
    try:
        sc = self_check.run(cfg)
        if sc:
            out_lines.append(sc)
    except Exception:  # noqa: BLE001 — fail-open
        pass
    # страж актуальности LLM: реактивная «незнакомая модель» + суточная просьба сверить линейку
    try:
        la = llm_actuality.session_start_nudge(event, cfg)
        if la:
            out_lines.append(la)
    except Exception:  # noqa: BLE001 — fail-open: подстраховка не должна мешать старту
        pass
    try:
        text, diag = catalog_generate.build_catalog(cfg.memory_dir, cfg)
        cat = Path(cfg.memory_dir) / cfg.catalog_file
        tmp = cat.with_name(cfg.catalog_file + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, cat)
        # пульс с тем же троттлингом, что CLI --report (раз/день + при смене долга)
        pulse = catalog_generate.throttle_pulse(
            catalog_generate.format_health_pulse(diag, cfg), diag, cfg
        )
        if pulse:
            out_lines.append(pulse)
    except OSError:
        pass
    # индекс прецедентов + шапка-предупреждение по каждому архиву
    arc_dir = Path(cfg.memory_dir) / cfg.archive_dir_name
    if arc_dir.is_dir():
        for arc in sorted(arc_dir.glob("precedents-*.md")):
            if arc.name.endswith("-INDEX.md"):
                continue
            try:
                raw = arc.read_text(encoding="utf-8")
                arc.write_text(precedent_index.add_warning_header(raw, cfg), encoding="utf-8")
                idx = precedent_index.render_index(precedent_index.parse_cards(raw, cfg), arc.name, cfg)
                idx_path = arc.with_name(arc.stem + "-INDEX.md")
                idx_path.write_text(idx, encoding="utf-8")
            except Exception:  # noqa: BLE001 — один битый архив не должен рвать весь SessionStart
                continue
    # показать накопленный SessionEnd-сканом долг устаревания (если есть)
    stale_path = Path(cfg.memory_dir) / staleness.STALE_FILE
    if stale_path.is_file():
        try:
            body = stale_path.read_text(encoding="utf-8").strip()
            if body:
                out_lines.append(body)
        except OSError:
            pass
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

    # 3) уроки по пути файла (applies_to) — разово на (сессию, файл), вне памяти/.claude.
    # Worktree-aware: служебное .claude/ пропускаем, НО правки проектных файлов в
    # .claude/worktrees/<wt>/ — НЕ служебные, страж по ним обязан срабатывать
    # (проект может работать из worktree). Вне worktree проектные файлы
    # и так не под .claude/ — не задеты.
    norm = file_path.replace("\\", "/")
    in_claude_tooling = "/.claude/" in norm and "/.claude/worktrees/" not in norm
    if not in_claude_tooling and not in_memory:
        # запомнить правленый проектный файл (с уроками или без) — для смыслового поиска
        # связанных уроков на закрытии задачи (stale_reconcile.related_lessons).
        stale_reconcile.record_edited_file(session_id, file_path, tmpdir)
        marker = stale_reconcile.applies_marker_path(session_id, file_path, tmpdir)
        if not marker.exists():
            matches = find_lessons_for_path(file_path, cfg)
            if matches:
                # метку обогащаем именами показанных уроков — stale_reconcile собирает их
                # на закрытии задачи как кандидатов «показан, но не актуализирован».
                stale_reconcile.write_applies_marker(marker, file_path, [n for n, _ in matches])
                return (
                    msg(cfg, "applies_to.gate.header")
                    + format_lines(matches)
                    + msg(cfg, "applies_to.gate.footer")
                )
    return None


def ev_post_record(event: dict, cfg: MemoryConfig, session_id: str, tmpdir: str) -> None:
    """PostToolUse Read|Write|Edit|MultiEdit на файле памяти: запомнить on-disk версию (CAS)
    и, если это ПРАВКА урока, отметить «урок тронут в этой сессии» (для stale_reconcile)."""
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return
    file_path = str(tool_input.get("file_path") or "")
    if not file_path:
        return
    try:
        in_memory = os.path.abspath(file_path).startswith(os.path.abspath(cfg.memory_dir))
    except (OSError, ValueError):
        return
    if not in_memory:
        return
    memory_concurrency.record_seen(session_id, file_path, tmpdir)
    # правка (не чтение) файла-урока → пометить тронутым: stale_reconcile исключит его из
    # кандидатов «показан, но не актуализирован».
    if str(event.get("tool_name") or "") in ("Write", "Edit", "MultiEdit"):
        stale_reconcile.record_edited_lesson(session_id, file_path, tmpdir)


def _measure(path: Path, unit: str) -> int:
    """Размер файла: символы (len текста) или байты (st_size). Для не-латиницы честнее
    символы (важна длина контента в контексте, не байты на диске)."""
    if unit == "chars":
        try:
            return len(path.read_text(encoding="utf-8"))
        except OSError:
            return 0
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _unit_word(cfg: MemoryConfig, unit: str) -> str:
    return msg(cfg, "unit.chars" if unit == "chars" else "unit.bytes")


def ev_bloat_check(event: dict, cfg: MemoryConfig, today: Optional[datetime.date] = None) -> str:
    """PostToolUse Write|Edit на файле памяти: авто-архив старого + предупреждение о размере.

    Ядро (core_file) меряется в core_size_unit (по умолч. символы) и предупреждается уже
    при core_warn_ratio·бюджета. Уроки меряются в байтах, предупреждаются только для
    size_warn_prefixes (без архива и size_exempt), с учётом size_override и счётчика
    «живых» прецедентов (precedent_count_warn).
    """
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
    name = p.name
    in_archive = f"/{cfg.archive_dir_name}/" in file_path.replace("\\", "/")
    # архивные файлы — это и есть архив: ни авто-архива, ни размер-warning
    if in_archive and cfg.size_warn_skip_archive:
        return ""
    warnings = []
    # авто-архив маркеров в транзитном session-файле
    if name == cfg.session_lessons_file:
        try:
            memory_archive.archive_old_session_markers(
                p, today=today, threshold_days=cfg.marker_archive_days, cfg=cfg
            )
        except OSError:
            pass
    # авто-архив прецедентов в feedback-файле (первый префикс уроков)
    if cfg.lesson_prefixes and name.startswith(cfg.lesson_prefixes[0]):
        try:
            memory_archive.archive_old_precedents(
                p, today=today, threshold_days=cfg.precedent_archive_days, cfg=cfg
            )
        except OSError:
            pass
    # — урок с пустым `name`: заголовок не заполнен/обнулён (name весит ×2 в retrieve) —
    # ловим В МОМЕНТ записи, а не только в SessionStart-пульсе следующей сессии.
    if (
        cfg.lesson_prefixes
        and any(name.startswith(pref) for pref in cfg.lesson_prefixes)
        and name not in cfg.size_exempt
    ):
        try:
            raw = p.read_text(encoding="utf-8")
        except OSError:
            raw = ""
        if raw.startswith("---") and not catalog_generate.parse_frontmatter(raw).get("name", "").strip():
            warnings.append(msg(cfg, "bloat.empty_name", filename=name))
    # — горячее ядро: символы/байты + ранний нудж на core_warn_ratio —
    if name == cfg.core_file:
        size = _measure(p, cfg.core_size_unit)
        budget = cfg.core_budget_bytes
        unit = _unit_word(cfg, cfg.core_size_unit)
        pct = round(size / budget * 100) if budget else 0
        if size > budget:
            warnings.append(msg(cfg, "bloat.core_over", core_file=name, size=size, unit=unit, pct=pct, budget=budget))
        elif cfg.core_warn_ratio and size >= cfg.core_warn_ratio * budget:
            warnings.append(msg(cfg, "bloat.core_warn", core_file=name, size=size, unit=unit, pct=pct, budget=budget))
        return "\n".join(warnings)
    # — обычный урок: байты, только для size_warn_prefixes, не exempt —
    prefixes = cfg.size_warn_prefixes if cfg.size_warn_prefixes is not None else cfg.lesson_prefixes
    if any(name.startswith(pref) for pref in prefixes) and name not in cfg.size_exempt:
        size = p.stat().st_size
        limit = cfg.size_override.get(name, cfg.feedback_warn_bytes)
        if size > limit:
            warnings.append(msg(cfg, "bloat.lesson_over", filename=name, size=size, unit=_unit_word(cfg, "bytes"), limit=limit))
        if cfg.precedent_count_warn:
            try:
                cnt = memory_archive.count_real_precedents(p.read_text(encoding="utf-8"), cfg=cfg)
            except OSError:
                cnt = 0
            if cnt >= cfg.precedent_count_warn:
                warnings.append(msg(cfg, "bloat.precedent_count", filename=name, count=cnt, days=cfg.precedent_archive_days))
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
    """PreCompact: напомнить про бюджет горячего ядра перед сжатием (ранний нудж на ratio)."""
    core = Path(cfg.memory_dir) / cfg.core_file
    if not core.is_file():
        return ""
    size = _measure(core, cfg.core_size_unit)
    budget = cfg.core_budget_bytes
    threshold = budget * (cfg.core_warn_ratio if cfg.core_warn_ratio else 1.0)
    if size >= threshold:
        unit = _unit_word(cfg, cfg.core_size_unit)
        pct = round(size / budget * 100) if budget else 0
        return msg(cfg, "compact.core_over", core_file=cfg.core_file, size=size, unit=unit, pct=pct, budget=budget)
    return ""


def ev_session_end(cfg: MemoryConfig, session_id: str, tmpdir: str) -> None:
    """SessionEnd: скан устаревания + бэкстоп stale_reconcile (кандидаты «показан, но не
    актуализирован») → `_stale_pending.md` (покажет следующий SessionStart)."""
    reconcile = (
        stale_reconcile.candidates(session_id, tmpdir)
        if getattr(cfg, "stale_reconcile_gate", False) else None
    )
    staleness.run(cfg, reconcile=reconcile or None)


def ev_stop(
    cfg: MemoryConfig, cwd: str, now_ts: float, session_id: str, tmpdir: str
) -> Optional[str]:
    """Stop: причина блокировки завершения или None.

    По убыванию приоритета: привратник закрытия задачи (коммит `Closes #N` без урока про
    задачу) → общий (свежий коммит без записанного позже урока). Страж устаревших уроков
    из Stop убран — он теперь срабатывает на фразу закрытия сессии (UserPromptSubmit), см.
    stale_reconcile.reconcile_on_close. session_id/tmpdir сохранены в сигнатуре для
    совместимости вызова диспетчера."""
    return (
        stop_check.closure_reminder(cfg, cwd)
        or stop_check.should_remind(cfg, cwd, now_ts)
    )


# ── Диспетчер ────────────────────────────────────────────────────────────────

def main() -> None:
    event_name = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        cfg = get_config()
    except Exception:  # noqa: BLE001 — fail-open: конфиг сломан → не мешаем работе
        sys.exit(0)
    # CLI-режим (НЕ хук-событие): список уроков по пути для ручного вызова на фазе плана.
    # stdin НЕ читаем (иначе в терминале без редиректа зависнем на чтении).
    if event_name == "applies-to":
        target = sys.argv[2] if len(sys.argv) > 2 else ""
        if target:
            out = format_lines(find_lessons_for_path(target, cfg))
            if out:
                print(out)
        sys.exit(0)
    if event_name in ("llm-verified", "llm-changes"):
        # запись итога сверки линейки (моё «подтверждаю» / «есть изменения»). stdin НЕ читаем.
        now = datetime.datetime.now(datetime.timezone.utc)
        if event_name == "llm-verified":
            llm_actuality.record_state(cfg, now, "confirmed")
        else:
            note = sys.argv[2] if len(sys.argv) > 2 else "changed"
            fam = None
            if "--families" in sys.argv:
                idx = sys.argv.index("--families")
                if idx + 1 < len(sys.argv):
                    fam = [x.strip() for x in sys.argv[idx + 1].split(",") if x.strip()]
            llm_actuality.record_state(cfg, now, "changes: " + note, fam)
        sys.exit(0)
    data = _read_event()
    session_id = str(data.get("session_id") or "nosess")
    tmpdir = tempfile.gettempdir()

    try:
        if event_name == "retrieve":
            # UserPromptSubmit: релевантные уроки + (если сообщение = фраза закрытия сессии)
            # чек-лист итогов памяти. Оба идут в контекст одной инъекцией.
            parts = [
                ev_retrieve(data, cfg),
                stale_reconcile.reconcile_on_close(
                    cfg, str(data.get("prompt") or ""), os.getcwd(), session_id, tmpdir
                ),
            ]
            _emit("\n".join(p for p in parts if p))
        elif event_name == "session-start":
            _emit(ev_session_start(data, cfg))
        elif event_name == "pre-edit-guard":
            r = ev_pre_edit_guard(data, cfg, session_id, tmpdir)
            if r:
                _deny(r)
        elif event_name == "post-record":
            ev_post_record(data, cfg, session_id, tmpdir)
        elif event_name == "bloat-check":
            _emit_post_context(ev_bloat_check(data, cfg))
        elif event_name == "agent-guard":
            r = ev_agent_guard(data, cfg, session_id, tmpdir)
            if r:
                _deny(r)
        elif event_name == "agent-log":
            now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            ev_agent_log(data, cfg, session_id, now_iso)
        elif event_name == "pre-compact":
            _emit_system_message(ev_pre_compact(cfg))
        elif event_name == "session-end":
            ev_session_end(cfg, session_id, tmpdir)
        elif event_name == "stop-check":
            # Stop-протокол: блокировка через JSON {"continue": false, "stopReason": …} в stdout.
            reason = ev_stop(cfg, os.getcwd(), time.time(), session_id, tmpdir)
            if reason:
                print(json.dumps({"continue": False, "stopReason": reason}, ensure_ascii=False))
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 — любая иная ошибка хука: fail-open
        sys.exit(0)
    sys.exit(0)


if __name__ == "__main__":
    main()
