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
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from . import (
    catalog_generate,
    issue_close_watch,
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
from .applies_to import _frontmatter, find_lessons_for_path, format_lines
from .config import MemoryConfig, get_config
from .lesson_files import is_lesson_path
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
    """SessionStart: проектные ноты + реестр моделей + размер инструкций + CATALOG +
    индекс прецедентов + пульс + долг устаревания."""
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
    # размер файла инструкций проекта — бэкстоп к хуку правки (правки мимо Write/Edit)
    try:
        ins = instructions_session_start(cfg, cwd=str(event.get("cwd") or ""))
        if ins:
            out_lines.append(ins)
    except Exception:  # noqa: BLE001 — fail-open
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

    # 3) уроки по пути файла (applies_to) — разово на (сессию, файл), вне каталога памяти.
    # Служебное `.claude/` больше НЕ пропускается скопом: правило переехало в
    # `find_lessons_for_path` (там оно одно на оба канала — хук и ретривер) и стало точным:
    # внутри `.claude/` матчат только глобы, которые сами адресуют `.claude/`. Широкий
    # `*.py` туда по-прежнему не лезет, а явная привязка к стражу выкладки — работает.
    if not in_memory:
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


def ev_post_record(event: dict, cfg: MemoryConfig, session_id: str, tmpdir: str) -> Optional[str]:
    """PostToolUse Read|Write|Edit|MultiEdit на файле памяти: запомнить on-disk версию (CAS),
    и, если это ПРАВКА урока, отметить «урок тронут в этой сессии» (для stale_reconcile)
    + пожаловаться, если у только что записанного урока поле frontmatter задано, но не
    разобрано (`applies_to` без глобов, дата не в ISO).

    Возвращает текст жалобы (в контекст через additionalContext) или None.

    Почему жалоба ЗДЕСЬ, а не в страже правки: сломанный урок не привязан к правимому
    файлу — из `ev_pre_edit_guard` он сыпался бы на каждую правку любого файла проекта
    (шум не по делу). Момент записи урока — единственный, где дефект и его автор рядом:
    ты сам только что это написал и починишь сразу, не через сессию. Сводку по всей
    памяти отдельно копит `staleness.scan_unparsed` → `_stale_pending` (там же живут
    протухшие привязки). Троттлинга нет намеренно: жалоба идёт ровно на свою правку,
    а если после починки значение всё ещё не разобрано — сказать надо снова.
    """
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None
    file_path = str(tool_input.get("file_path") or "")
    if not file_path:
        return None
    try:
        in_memory = os.path.abspath(file_path).startswith(os.path.abspath(cfg.memory_dir))
    except (OSError, ValueError):
        return None
    if not in_memory:
        return None
    memory_concurrency.record_seen(session_id, file_path, tmpdir)
    # правка (не чтение) файла-урока → пометить тронутым: stale_reconcile исключит его из
    # кандидатов «показан, но не актуализирован».
    if str(event.get("tool_name") or "") not in ("Write", "Edit", "MultiEdit"):
        return None
    stale_reconcile.record_edited_lesson(session_id, file_path, tmpdir)
    bad = staleness.unparsed_fields(_frontmatter(file_path))
    if not bad:
        return None
    return "\n".join(
        msg(cfg, "frontmatter.unparsed_warning",
            filename=os.path.basename(file_path), field=field, value=value,
            hint=msg(cfg, "frontmatter.unparsed_hint_applies_to" if field == "applies_to"
                     else "frontmatter.unparsed_hint_date"))
        for field, value in bad
    )


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


# ── Размер файла инструкций проекта (CLAUDE.md), заявка #16 ─────────────────
# Разбор дизайна — в комментарии к `instructions_budget_chars` (config.py): почему одна
# точка срабатывания, почему знаки, и почему предупреждение, а не блокировка.

_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
# Имя внутреннего файла-маркера троттлинга SessionStart-замера (приставка `_` — приватная,
# такие файлы вне глобов движка, как `_retrieve_cache.sqlite3` и маркер пульса здоровья).
INSTRUCTIONS_MARKER_NAME = "_instructions_size.json"


def loaded_instructions_text(raw: str) -> str:
    """Текст файла инструкций таким, каким его ВИДИТ модель.

    Claude Code вырезает БЛОЧНЫЕ HTML-комментарии из файлов инструкций до подачи в
    контекст (документация Claude Code, сверено 2026-07-19: «Block-level HTML comments
    in CLAUDE.md files are stripped before the content is injected into Claude's context»);
    комментарии ВНУТРИ блоков кода при этом сохраняются.

    Почему это не мелочь. Меряя сырой файл, страж мерил бы не тот объект: у шаблона
    инструкций шапка-комментарий тянет на пару тысяч знаков, которые в контекст не
    попадают НИКОГДА. Человек, честно вынесший пояснения для сопровождающих в комментарий
    (ровно то поведение, которого мы хотим), получал бы за это предупреждение — то есть
    страж наказывал бы за правильное. Это домашний класс дефектов движка: «разбор молча
    возвращает не то», и он тем опаснее, что расхождение видно только при сверке с
    первоисточником.

    Блочным считается комментарий, ЗАНИМАЮЩИЙ строку целиком (строка начинается с `<!--`).
    Комментарий посреди прозы блочным не является — его не трогаем, как и Claude Code.
    """
    out, in_fence, in_comment = [], False, False
    for line in raw.splitlines(keepends=True):
        if in_comment:
            if "-->" in line:
                # хвост после закрытия — обычный текст, он в контекст попадёт
                in_comment = False
                tail = line.split("-->", 1)[1]
                if tail.strip():
                    out.append(tail)
            continue
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence and line.lstrip().startswith("<!--"):
            body = line.split("<!--", 1)[1]
            if "-->" not in body:
                in_comment = True   # многострочный: глотаем до закрывающей строки
                continue
            # Однострочный. Выбрасываем ТОЛЬКО сам комментарий: если за ним на той же
            # строке остался текст, строка блочным комментарием не является, и модель
            # этот текст увидит. Вырезав её целиком, замер занизил бы размер — а страж,
            # который занижает, молчит там, где обязан говорить.
            rest = body.split("-->", 1)[1]
            if rest.strip():
                out.append(rest)
            continue
        out.append(line)
    return "".join(out)


def instructions_roots(cfg: MemoryConfig, cwd: Optional[str] = None) -> list:
    """Корни, от которых разрешаются пути `instructions_files`: корень из конфига и
    рабочий каталог сессии.

    Второй корень — не перестраховка, а WORKTREE. В worktree-сессии Claude Code читает
    файл инструкций РАБОЧЕЙ КОПИИ (он лежит под её корнем), а `project_root` в конфиге
    указывает на главный checkout. Считай мы только от конфига — страж мерил бы чужой
    файл и молчал бы о том, который сессия реально видит; обе ошибки бесшумны. Ровно эту
    же граблю движок уже обходит в привязке уроков к путям (там — через git-toplevel).
    Здесь хватает `cwd` из события: он и есть корень worktree, и стоит ноль подпроцессов.
    """
    roots, seen = [], set()
    for root in (cfg.project_root, cwd):
        if not root:
            continue
        try:
            key = os.path.abspath(root)
        except (OSError, ValueError):
            continue
        if key not in seen:
            seen.add(key)
            roots.append(key)
    return roots


def instructions_oversize(
    cfg: MemoryConfig, only: Optional[str] = None, cwd: Optional[str] = None
) -> list:
    """[(путь-как-в-конфиге, абсолютный путь, размер в знаках)] для файлов СВЕРХ ориентира.

    `only` — абсолютный путь: проверить ровно его (путь правки), а не весь список.
    Пустой бюджет (0) выключает стража целиком, и это единственный способ его заглушить.

    Абсолютный путь возвращается наряду с относительным намеренно: у двух корней (см.
    `instructions_roots`) относительные пути СОВПАДАЮТ, и опознавать файл по имени значило
    бы склеить два разных файла в один — маркер троттлинга потерял бы половину.
    """
    budget = cfg.instructions_budget_chars
    if not budget:
        return []
    found, seen = [], set()
    for root in instructions_roots(cfg, cwd):
        for rel in cfg.instructions_files:
            path = os.path.abspath(os.path.join(root, rel))
            if only is not None and path != only:
                continue
            if path in seen:        # тот же файл через второй корень — не дублируем
                continue
            seen.add(path)
            try:
                size = len(loaded_instructions_text(Path(path).read_text(encoding="utf-8")))
            except OSError:
                continue    # файла нет / не читается — не наше дело, молчим
            if size > budget:
                found.append((rel, path, size))
    return found


def _instructions_message(cfg: MemoryConfig, found: list) -> str:
    return "\n".join(
        msg(cfg, "bloat.instructions_large", filename=rel, size=size,
            unit=_unit_word(cfg, "chars"), budget=cfg.instructions_budget_chars)
        for rel, _abs, size in found
    )


def ev_instructions_check(event: dict, cfg: MemoryConfig) -> str:
    """PostToolUse Write|Edit|MultiEdit: правится ли файл инструкций и не вырос ли он.

    Живёт РЯДОМ с `ev_bloat_check`, а не внутри: та функция построена вокруг инварианта
    «файл лежит в memory_dir» и первым же делом отсекает всё прочее. Файл инструкций лежит
    в корне ПРОЕКТА, то есть заведомо вне памяти, и подмешивать его в чужой инвариант
    значило бы размыть обе проверки.

    Сверка путей — ТОЧНЫМ совпадением, а не по приставке: приставочная сверка ошибается на
    соседях (`CLAUDE.md.bak`), а список стерегомых путей и без того задан явно.
    """
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return ""
    file_path = str(tool_input.get("file_path") or "")
    if not file_path:
        return ""
    # Относительный путь резолвим от cwd СОБЫТИЯ, а не процесса: рабочий каталог хука не
    # обязан совпадать с каталогом сессии, и промах здесь был бы совершенно бесшумным.
    if not os.path.isabs(file_path):
        file_path = os.path.join(str(event.get("cwd") or cfg.project_root), file_path)
    try:
        target = os.path.abspath(file_path)
    except (OSError, ValueError):
        return ""
    return _instructions_message(
        cfg, instructions_oversize(cfg, only=target, cwd=str(event.get("cwd") or ""))
    )


def instructions_session_start(cfg: MemoryConfig, cwd: Optional[str] = None) -> str:
    """SessionStart: тот же замер, но как БЭКСТОП по результату, а не по каналу.

    Зачем второй канал. Хук правки видит только Write|Edit|MultiEdit. Файл инструкций
    правят и мимо них — `sed` в терминале, внешний редактор, чужая сессия, слияние ветки, —
    и на всех этих путях страж молчит, причём молчание неотличимо от «файл в порядке». Урок
    движка ровно об этом: где каналов события много и часть невидима, сторожи РЕЗУЛЬТАТ.

    Троттлинг — по ИЗМЕНЕНИЮ размера, а не по времени. Это и есть ответ на возражение из
    заявки («сообщение о файле, который никто не менял, быстро перестают читать»): пока
    файл не трогали, мы молчим, сколько бы сессий ни прошло; вырос — сказали один раз.
    Молчание при неизменном размере честно: решение «оставляю как есть» уже принято, и
    повторять вопрос значит учить человека пролистывать наши предупреждения.
    """
    found = instructions_oversize(cfg, cwd=cwd)
    marker = Path(cfg.memory_dir) / INSTRUCTIONS_MARKER_NAME
    try:
        seen = json.loads(marker.read_text(encoding="utf-8"))
        seen = seen if isinstance(seen, dict) else {}
    except (OSError, ValueError):
        seen = {}
    fresh = [item for item in found if seen.get(item[1]) != item[2]]
    # Маркер переписываем ВСЕГДА (в т.ч. когда файл ушёл под ориентир и его больше нет в
    # `found`): иначе запись о старом размере пережила бы починку, и после следующего роста
    # до ровно того же числа страж промолчал бы.
    try:
        marker.write_text(
            json.dumps({abs_: size for _rel, abs_, size in found}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass
    return _instructions_message(cfg, fresh)


# Прежний (до 0.11.0) русский дефолт слова-опознавателя прецедента и дефолт нынешний.
# Нужны детектору «проект полагался на старый дефолт» — см. ev_bloat_check.
_PRECEDENT_KEYWORD_DEFAULT = MemoryConfig.__dataclass_fields__["precedent_keyword"].default
_LEGACY_PRECEDENT_RE = re.compile(r"\*\*Прецедент\s+\d{4}-\d{2}-\d{2}")


def _is_precedent_file(name: str, cfg: MemoryConfig) -> bool:
    """Файл-накопитель прецедентов (кандидат на авто-архивацию старых карточек).

    `precedent_files` — явные имена (или их приставки). None → историческое поведение:
    первый элемент `lesson_prefixes`. Фолбэк сохранён бит-в-бит, чтобы обновление 0.10.0
    не переселило карточки у существующих потребителей."""
    if cfg.precedent_files is not None:
        return any(name.startswith(pref) for pref in cfg.precedent_files)
    return bool(cfg.lesson_prefixes) and name.startswith(cfg.lesson_prefixes[0])


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
    # — авто-архив прецедентов —
    # ЕДИНСТВЕННОЕ место, где определение НАМЕРЕННО остаётся узким: это путь ЗАПИСИ (он
    # вырезает карточки из файла и переносит в архив), а не нудж. Расширь его на «любой
    # урок» — и kebab-урок со словом-маркером прецедента молча переехал бы в архив.
    # `precedent_files` задаёт файлы явно; None → историческое поведение (первый префикс
    # уроков) бит-в-бит. Позиционный контракт «lesson_prefixes[0] == файл прецедентов»
    # нигде не документировался — проект с иным порядком префиксов целился не в тот файл.
    if _is_precedent_file(name, cfg):
        try:
            memory_archive.archive_old_precedents(
                p, today=today, threshold_days=cfg.precedent_archive_days, cfg=cfg
            )
        except OSError:
            pass
    # — урок с пустым `name`: заголовок не заполнен/обнулён (name весит ×2 в retrieve) —
    # ловим В МОМЕНТ записи, а не только в SessionStart-пульсе следующей сессии.
    # Признак урока — общий (`lesson_files`), а не приставка: до 0.10.0 урок без приставки
    # рос без единого предупреждения, потому что нудж его не замечал (та же слепота, что
    # у стража).
    # `is_lesson_path`, а НЕ `is_lesson_file`: у нас на руках ПУТЬ, а имя — половина
    # признака. Файл в подпапке (`drafts/`) носит законное имя урока, но уроком не является
    # — корпус памяти обходит только корень memory_dir. В 0.10.0 здесь стоял basename-вариант,
    # и нудж прилетал на черновики.
    if is_lesson_path(file_path, cfg) and name not in cfg.size_exempt:
        try:
            raw = p.read_text(encoding="utf-8")
        except OSError:
            raw = ""
        if raw.startswith("---"):
            fields = catalog_generate.parse_frontmatter(raw)
            if not fields.get("name", "").strip():
                warnings.append(msg(cfg, "bloat.empty_name", filename=name))
            # — раздутое `description`: это КРАТКОЕ содержание, а не тело урока —
            # Ловим в момент записи, потому что цена платится потом и не автором: страж
            # правки и ретривер печатают description ЦЕЛИКОМ, и четыре урока по абзацу
            # превращаются в стену, которую перестают читать (тогда вся привязка зря).
            # Обрезать при показе нельзя — потеряется контекст, ради которого урок и писан;
            # значит чинить надо источник. Длинное описание почти всегда значит одно из
            # двух: тело урока запихнули в краткое содержание ЛИБО склеили два урока
            # (живой пример: описание на 1209 знаков, где вторая половина начинается с
            # «CSS issue #3» — то есть в файле два разных урока).
            desc = fields.get("description", "")
            limit = cfg.description_warn_chars
            if limit and len(desc) > limit:
                warnings.append(msg(cfg, "bloat.description_long",
                                    filename=name, size=len(desc), limit=limit))
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
    # — обычный урок: байты, не exempt —
    # `size_warn_prefixes` остаётся СУЖАЮЩЕЙ ручкой (проект может ограничить warning'и
    # частью корпуса). Но её ОТСУТСТВИЕ (None) теперь значит «все уроки», а не «уроки с
    # приставкой»: иначе kebab-урок рос бы без предупреждений.
    if (
        (any(name.startswith(pref) for pref in cfg.size_warn_prefixes)
         if cfg.size_warn_prefixes is not None else is_lesson_path(file_path, cfg))
        and name not in cfg.size_exempt
    ):
        size = p.stat().st_size
        limit = cfg.size_override.get(name, cfg.feedback_warn_bytes)
        if size > limit:
            warnings.append(msg(cfg, "bloat.lesson_over", filename=name, size=size, unit=_unit_word(cfg, "bytes"), limit=limit))
        if cfg.precedent_count_warn:
            try:
                raw_p = p.read_text(encoding="utf-8")
            except OSError:
                raw_p = ""
            cnt = memory_archive.count_real_precedents(raw_p, cfg=cfg) if raw_p else 0
            if cnt >= cfg.precedent_count_warn:
                warnings.append(msg(cfg, "bloat.precedent_count", filename=name, count=cnt, days=cfg.precedent_archive_days))
            # Детектор ломающей смены дефолта 0.11.0: карточки написаны ПРЕЖНИМ русским
            # словом, а `precedent_keyword` остался дефолтным (английским) — значит проект
            # полагался на старый дефолт, и авто-архивация у него молча перестала видеть
            # карточки. Ровно тот класс, против которого весь релиз: смена, о которой
            # пострадавший узнаёт из CHANGELOG или не узнаёт никогда.
            # Стоит НОЛЬ лишнего IO: файл уже прочитан строкой выше. Границу self_check не
            # трогаем — та читает только конфиги и метаданные, а здесь путь записи.
            if cfg.precedent_keyword == _PRECEDENT_KEYWORD_DEFAULT and _LEGACY_PRECEDENT_RE.search(raw_p):
                warnings.append(msg(cfg, "bloat.precedent_legacy_keyword", filename=name))
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
    задачу) → закрытие командой `gh issue close` без урока (метка от PostToolUse, см.
    issue_close_watch) → общий (свежий коммит без записанного позже урока). Страж
    устаревших уроков из Stop убран — он теперь срабатывает на фразу закрытия сессии
    (UserPromptSubmit), см. stale_reconcile.reconcile_on_close. session_id/tmpdir
    сохранены в сигнатуре для совместимости вызова диспетчера.

    Коммит-путь стоит ПЕРВЫМ намеренно: он старше, его текст точнее (номер задачи взят
    из шаблона проекта), и порядок гарантирует, что добавление второго источника не
    может подменить собой существующее срабатывание — только добавить новое."""
    return (
        stop_check.closure_reminder(cfg, cwd)
        or issue_close_watch.pending_reminder(cfg, cwd, now_ts)
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
            _emit_post_context(ev_post_record(data, cfg, session_id, tmpdir) or "")
        elif event_name == "bloat-check":
            # Два независимых замера на одном событии: файлы ПАМЯТИ (ev_bloat_check) и файл
            # ИНСТРУКЦИЙ проекта (ev_instructions_check). Регистрация хука одна и та же,
            # второй процесс на каждую правку заводить незачем.
            _emit_post_context("\n".join(
                p for p in (ev_bloat_check(data, cfg), ev_instructions_check(data, cfg)) if p
            ))
        elif event_name == "issue-close-watch":
            _emit_post_context(issue_close_watch.record_close(
                data, cfg, os.getcwd(), time.time(), session_id
            ) or "")
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
