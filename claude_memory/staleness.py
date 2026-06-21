"""Скан устаревания памяти (SessionEnd): без ИИ, без сети.

Две механические проверки → результат в `_stale_pending.md` (его показывает следующий
SessionStart для повторной проверки):
  (1) уроки с `reverify_after:` < сегодня — просроченные time-bound правила;
  (2) ПРОТУХШИЕ `applies_to`-привязки — glob больше не матчит ни один файл проекта
      (файл переехал/переименован → урок молча перестал всплывать).

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

from .applies_to import _applies_globs, _frontmatter
from .config import MemoryConfig, get_config
from .messages import msg

_REVERIFY_RE = re.compile(r"^[ \t]*reverify_after:\s*['\"]?(\d{4}-\d{2}-\d{2})", re.MULTILINE)
_ARCHIVED_RE = re.compile(r"^[ \t]*archived_on:\s*['\"]?(\d{4}-\d{2}-\d{2})", re.MULTILINE)
_DESC_RE = re.compile(r"^description:\s*(.*)$", re.MULTILINE)
STALE_FILE = "_stale_pending.md"


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
        m = _ARCHIVED_RE.search(fm)
        if not m:
            continue
        try:
            a = datetime.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        elapsed = _months_elapsed(a, today)
        if elapsed >= n:
            dm = _DESC_RE.search(fm)
            out.append((a.isoformat(), os.path.basename(mf), elapsed, dm.group(1).strip() if dm else ""))
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
    for mf in sorted(glob.glob(os.path.join(cfg.memory_dir, "*.md"))):
        fm = _frontmatter(mf)
        if not fm:
            continue
        name = os.path.basename(mf)
        m = _REVERIFY_RE.search(fm)
        if m:
            try:
                d = datetime.date.fromisoformat(m.group(1))
                if d < today:
                    dm = _DESC_RE.search(fm)
                    stale.append((d.isoformat(), name, dm.group(1).strip() if dm else ""))
            except ValueError:
                pass
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


def write_pending(
    cfg: Optional[MemoryConfig] = None,
    stale: Optional[List[Tuple[str, str, str]]] = None,
    broken: Optional[List[Tuple[str, List[str]]]] = None,
    today: Optional[datetime.date] = None,
    archived: Optional[List[Tuple[str, str, int, str]]] = None,
) -> bool:
    """Пишет `_stale_pending.md` (или удаляет, если долга нет). Возвращает True, если файл записан."""
    cfg = cfg or get_config()
    today = today or datetime.date.today()
    out_path = Path(cfg.memory_dir) / STALE_FILE
    if not stale and not broken and not archived:
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
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def run(cfg: Optional[MemoryConfig] = None, today: Optional[datetime.date] = None) -> bool:
    """Скан + запись. Возвращает True, если есть долг (файл записан)."""
    cfg = cfg or get_config()
    stale, broken = scan(cfg, today)
    archived = scan_archive_stale(cfg, today)
    return write_pending(cfg, stale, broken, today, archived)
