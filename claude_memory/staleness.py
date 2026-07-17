"""Скан устаревания памяти (SessionEnd): без ИИ, без сети.

Механические проверки → результат в `_stale_pending.md` (его показывает следующий
SessionStart для повторной проверки):
  (1) уроки с `reverify_after:` < сегодня — просроченные time-bound правила;
  (2) ПРОТУХШИЕ `applies_to`-привязки — glob больше не матчит ни один файл проекта
      (файл переехал/переименован → урок молча перестал всплывать);
  (3) НЕПОНЯТЫЕ `applies_to`-значения — поле задано, но глобов из него не вышло
      (`scan_unparsed`). Соседняя полка к (2): там привязка мертва, здесь её вовсе
      не разобрали — оба случая молча выглядят как «привязок нет».

Stdout у SessionEnd идёт только в debug-лог, поэтому это чистый side-effect через файл,
а показ — на старте следующей сессии.
"""
from __future__ import annotations

import datetime
import fnmatch
import glob
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from .applies_to import (
    _applies_globs,
    _frontmatter,
    field_value,
    iso_date_or_none,
    strip_scalar,
    unparsed_applies_to,
)
from .lesson_files import lesson_paths
from .config import MemoryConfig, get_config
from .messages import msg

_DESC_RE = re.compile(r"^description:[ \t]*(.*)$", re.MULTILINE)  # [ \t]* не \s*: пустое поле не хватает следующую строку
STALE_FILE = "_stale_pending.md"
DATE_FIELDS = ("reverify_after", "archived_on")
# Потолок секции непонятых значений в _stale_pending: после массового импорта/переразметки
# их могут быть десятки, а секция печатается в контекст на КАЖДОМ старте и конкурирует за
# внимание с самой памятью. Показываем первые N + счётчик остатка — сигнал сохраняется,
# контекст не топим.
UNPARSED_CAP = 20


def _date_or_complaint(fm: str, key: str):
    """(дата, жалоба) для поля-даты: ровно ТРИ состояния, без четвёртого.

    (None, None)     — поля нет (не дефект);
    (date, None)     — поле есть и разобрано;
    (None, "сырое")  — поле ЕСТЬ, но датой не является → об этом надо СКАЗАТЬ.

    Раньше здесь стоял строгий regex `(\\d{4}-\\d{2}-\\d{2})`, который сливал 1-е и 3-е
    состояния: `reverify_after: "01.01.2026"` (естественная русская запись) молча не
    существовал — урок выглядел срочным и жил вечно, а `archived_on` навсегда проходил
    мимо срока хранения. Детектор и парсер здесь ОДИН, поэтому разъехаться не могут.
    """
    raw = field_value(fm, key)
    if raw is None:
        return None, None
    d = iso_date_or_none(raw)
    return (d, None) if d else (None, raw)


def _months_elapsed(a: datetime.date, today: datetime.date) -> int:
    """Полных месяцев между датами (целое). today раньше a → отрицательное."""
    m = (today.year - a.year) * 12 + (today.month - a.month)
    if today.day < a.day:
        m -= 1
    return m


def scan_archive_stale(
    cfg: Optional[MemoryConfig] = None, today: Optional[datetime.date] = None
) -> List[Tuple[str, str, int, str]]:
    """Архивные уроки с истёкшим сроком хранения → [(archived_on, имя, мес_в_архиве, описание)].

    Кандидат = файл в `cfg.archive_dir_name` (рекурсивно) с полем `archived_on: YYYY-MM-DD`,
    пролежавший ≥ `cfg.archive_stale_months` месяцев. Поле обязательно → файлы-агрегаты
    (precedents/markers/audits без `archived_on`) НЕ попадают. `archive_stale_months<=0` → выкл,
    пустой список. Только формирует список — удаление делает человек (archive_prune)."""
    cfg = cfg or get_config()
    today = today or datetime.date.today()
    n = cfg.archive_stale_months
    if not n or n <= 0:
        return []
    arc_root = os.path.join(cfg.memory_dir, cfg.archive_dir_name)
    out: List[Tuple[str, str, int, str]] = []
    for mf in sorted(glob.glob(os.path.join(arc_root, "**", "*.md"), recursive=True)):
        fm = _frontmatter(mf)
        if not fm:
            continue
        a, _bad = _date_or_complaint(fm, "archived_on")
        if a is None:
            continue  # поля нет ЛИБО оно не дата — про второе скажет scan_unparsed
            # (архив он обходит отдельно и рекурсивно — здесь молчать нельзя, это и есть
            # мотивирующий случай: непонятая дата = урок вечно мимо срока хранения)
        elapsed = _months_elapsed(a, today)
        if elapsed >= n:
            dm = _DESC_RE.search(fm)
            out.append((a.isoformat(), os.path.basename(mf), elapsed, strip_scalar(dm.group(1)) if dm else ""))
    out.sort()
    return out


def _repo_files(cfg: MemoryConfig) -> List[str]:
    """Относительные пути файлов проекта (для проверки applies_to), без тяжёлых каталогов."""
    root = cfg.project_root
    skip = set(cfg.staleness_skip_dirs)
    files: List[str] = []
    if not os.path.isdir(root):
        return files
    for cur, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in fs:
            files.append(os.path.relpath(os.path.join(cur, f), root))
    return files


def _glob_matches_disk(root: str, pattern: str) -> bool:
    """True, если applies_to-glob (относительно root) матчит хоть один реальный путь на диске.

    Фолбэк к прямой проверке: `_repo_files` намеренно НЕ обходит staleness_skip_dirs
    (.git/.venv/node_modules/.claude/...) ради скорости, но applies_to законно ведёт в
    .claude/ (уроки самого движка памяти). Без этой проверки такие живые привязки ложно
    помечались бы «протухшими» на каждом SessionStart."""
    if not root or not pattern:
        return False
    try:
        return bool(glob.glob(os.path.join(root, pattern), recursive=True))
    except (OSError, ValueError):
        return False


def scan(
    cfg: Optional[MemoryConfig] = None, today: Optional[datetime.date] = None
) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, List[str]]]]:
    """Возвращает (stale, broken).

    stale  — [(дата, имя_урока, описание)] для просроченных reverify_after (отсортировано).
    broken — [(имя_урока, [мёртвые globs])] для applies_to, не нашедших файл в проекте.
    Если список файлов проекта пуст (нет доступа к корню) — проверку applies_to пропускаем
    (не шумим ложными срабатываниями).
    """
    cfg = cfg or get_config()
    today = today or datetime.date.today()
    repo_files = _repo_files(cfg)

    stale: List[Tuple[str, str, str]] = []
    broken: List[Tuple[str, List[str]]] = []
    # Общий набор уроков (`lesson_files`), а не голый glob `*.md`: до 0.10.0 здесь жил
    # третий вариант определения урока — без исключения ядра/указателя/приватных.
    for mf in lesson_paths(cfg):
        fm = _frontmatter(mf)
        if not fm:
            continue
        name = os.path.basename(mf)
        d, _bad = _date_or_complaint(fm, "reverify_after")
        if d is not None and d < today:
            dm = _DESC_RE.search(fm)
            stale.append((d.isoformat(), name, strip_scalar(dm.group(1)) if dm else ""))
        if repo_files:
            dead = [
                g for g in _applies_globs(fm)
                if not any(c == g or fnmatch.fnmatch(c, g) for c in repo_files)
                and not _glob_matches_disk(cfg.project_root, g)
            ]
            if dead:
                broken.append((name, dead))
    stale.sort()
    return stale, broken


def unparsed_fields(fm: str) -> List[Tuple[str, str]]:
    """[(поле, сырое_значение)] для полей frontmatter, которые ЗАДАНЫ, но не разобраны.

    Один детектор на все поля, а не механизм на каждое: болезнь у них общая — поле молча
    не работает и выглядит как «не задано». Сейчас покрыты `applies_to` (глоб не вышел) и
    поля-даты (не ISO). Новое поле добавляется одной строкой здесь и получает ОБА канала
    жалобы сразу (немедленный на записи + сводку), не заводя третьей конструкции.
    """
    out: List[Tuple[str, str]] = []
    raw = unparsed_applies_to(fm)
    if raw is not None:
        out.append(("applies_to", raw))
    for key in DATE_FIELDS:
        _d, bad = _date_or_complaint(fm, key)
        if bad is not None:
            out.append((key, bad))
    return out


def scan_unparsed(cfg: Optional[MemoryConfig] = None) -> List[Tuple[str, str, str]]:
    """[(имя_урока, поле, сырое_значение)] по всей памяти — поля заданы, но не разобраны.

    Отдельной функцией, а не третьим элементом `scan()`: у неё нет ни `today`, ни обхода
    файлов проекта (дефект чисто в тексте урока), и её независимо зовёт немедленная
    жалоба на записи урока (hooks_cli.ev_post_record). Лишний обход каталога памяти —
    ~сотня мелких файлов, цена ничтожна против связности контракта `scan()`.

    Архив обходится ОТДЕЛЬНО и рекурсивно, и только на `archived_on`. Почему вообще: это
    мотивирующий случай правки — `archived_on: 01.01.2025` молча не существует, урок
    вечно мимо `archive_stale_months`; а `archived_on` по замыслу живёт именно в
    `archive/**`, куда плоский `*.md` не достаёт (без этого прохода жалоба обещала бы то,
    чего не делает). Почему только он: `reverify_after`/`applies_to` на ХОЛОДНОМ архивном
    уроке — заведомый шум, там нечему всплывать и нечего перепроверять.
    """
    cfg = cfg or get_config()
    out: List[Tuple[str, str, str]] = []
    for mf in lesson_paths(cfg):   # общий набор уроков, а не голый glob (см. 0.10.0)
        fm = _frontmatter(mf)
        if not fm:
            continue
        out += [(os.path.basename(mf), k, v) for k, v in unparsed_fields(fm)]
    arc_root = os.path.join(cfg.memory_dir, cfg.archive_dir_name)
    for mf in sorted(glob.glob(os.path.join(arc_root, "**", "*.md"), recursive=True)):
        fm = _frontmatter(mf)
        if not fm:
            continue
        _d, bad = _date_or_complaint(fm, "archived_on")
        if bad is not None:
            out.append((os.path.relpath(mf, cfg.memory_dir), "archived_on", bad))
    return out


def write_pending(
    cfg: Optional[MemoryConfig] = None,
    stale: Optional[List[Tuple[str, str, str]]] = None,
    broken: Optional[List[Tuple[str, List[str]]]] = None,
    today: Optional[datetime.date] = None,
    archived: Optional[List[Tuple[str, str, int, str]]] = None,
    reconcile: Optional[dict] = None,
    unparsed: Optional[List[Tuple[str, str, str]]] = None,
) -> bool:
    """Пишет `_stale_pending.md` (или удаляет, если долга нет). Возвращает True, если файл записан.

    reconcile — {урок -> [файлы]} кандидатов «показан на правке, не актуализирован»
    (бэкстоп stale_reconcile: показывается на следующем старте на случай сессии без
    закрывающего коммита)."""
    cfg = cfg or get_config()
    today = today or datetime.date.today()
    out_path = Path(cfg.memory_dir) / STALE_FILE
    if not stale and not broken and not archived and not reconcile and not unparsed:
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError:
                pass
        return False
    lines = [
        msg(cfg, "staleness.pending_file.header"),
        "",
        msg(cfg, "staleness.pending_file.preamble", date=today.isoformat()),
        "",
    ]
    if reconcile:
        lines.append(msg(cfg, "staleness.pending_file.reconcile_section_header"))
        lines += [
            msg(cfg, "staleness.pending_file.reconcile_item",
                lesson=lesson, files=", ".join(os.path.basename(f) for f in files if f))
            for lesson, files in sorted(reconcile.items())
        ]
        lines.append("")
    if stale:
        lines.append(msg(cfg, "staleness.pending_file.stale_section_header"))
        lines += [
            msg(cfg, "staleness.pending_file.stale_item", name=name, d=d, desc=desc)
            for d, name, desc in stale
        ]
        lines.append("")
    if archived:
        lines.append(msg(cfg, "staleness.pending_file.archive_section_header"))
        lines += [
            msg(cfg, "staleness.pending_file.archive_item", name=name, d=d, months=months, desc=desc)
            for d, name, months, desc in archived
        ]
        lines.append(msg(cfg, "staleness.pending_file.archive_hint"))
        lines.append("")
    if broken:
        lines.append(msg(cfg, "staleness.pending_file.broken_section_header"))
        lines += [
            msg(cfg, "staleness.pending_file.broken_item", name=name, dead=", ".join(dead))
            for name, dead in broken
        ]
        lines.append(msg(cfg, "staleness.pending_file.broken_hint"))
        lines.append("")
    if unparsed:
        lines.append(msg(cfg, "staleness.pending_file.unparsed_section_header"))
        lines += [
            msg(cfg, "staleness.pending_file.unparsed_item", name=name, field=field, value=value)
            for name, field, value in unparsed[:UNPARSED_CAP]
        ]
        if len(unparsed) > UNPARSED_CAP:
            lines.append(msg(cfg, "staleness.pending_file.unparsed_more",
                             count=len(unparsed) - UNPARSED_CAP))
        lines.append(msg(cfg, "staleness.pending_file.unparsed_hint"))
        lines.append("")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def run(
    cfg: Optional[MemoryConfig] = None,
    today: Optional[datetime.date] = None,
    reconcile: Optional[dict] = None,
) -> bool:
    """Скан + запись. Возвращает True, если есть долг (файл записан).

    reconcile — кандидаты stale_reconcile (бэкстоп): {урок -> [файлы]}, прокидываются в
    отдельную секцию _stale_pending. ev_session_end вычисляет их по меткам сессии."""
    cfg = cfg or get_config()
    stale, broken = scan(cfg, today)
    archived = scan_archive_stale(cfg, today)
    return write_pending(cfg, stale, broken, today, archived, reconcile, scan_unparsed(cfg))
