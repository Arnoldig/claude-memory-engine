"""Поиск «путь → уроки»: по пути файла находит уроки, чьи `applies_to:`-глобы
(frontmatter) совпадают с этим путём.

Раньше эта логика жила Python-вставкой внутри bash-хука `lessons_for_path.sh` и
дублировалась вызовом из ретривера. Здесь она — один Python-модуль, который зовут И
ретривер (`memory_retrieve.path_lessons`), И тонкая обёртка-хук «перед первой правкой
файла». Один источник истины, без shell-зависимости, тестируемо.

`applies_to` задаётся относительно корня проекта; матчинг идёт по пути,
релативизированному к корню проекта (а если не вышло — к git-toplevel; работает и в
worktree).
"""
from __future__ import annotations

import fnmatch
import glob
import os
import re
import subprocess
from typing import List, Optional, Tuple

from .config import MemoryConfig, get_config

_APPLIES_RE = re.compile(r"^[ \t]*applies_to:[ \t]*(.*)$", re.MULTILINE)
_DESC_RE = re.compile(r"^description:\s*(.*)$", re.MULTILINE)


def _frontmatter(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(2000)
    except OSError:
        return ""
    if not head.startswith("---"):
        return ""
    return head.split("\n---", 1)[0]


def _applies_globs(fm: str) -> List[str]:
    """Глобы из `applies_to:` — инлайн-список `[a, b]` ИЛИ YAML-список из `- `-строк.

    Ведущий [ \\t]* ловит И top-level applies_to, И вложенный под `metadata:` (нативный
    формат памяти harness). После `:` — [ \\t]* (не \\s*), иначе потеряется 1-й элемент
    многострочного списка.
    """
    m = _APPLIES_RE.search(fm)
    if not m:
        return []
    inline = m.group(1).strip()
    if inline.startswith("["):
        inner = inline.strip("[]")
        return [g.strip().strip("'\"") for g in inner.split(",") if g.strip()]
    globs: List[str] = []
    for line in fm[m.end():].splitlines():
        ls = line.strip()
        if ls.startswith("- "):
            globs.append(ls[2:].strip().strip("'\""))
        elif ls and not ls.startswith("#"):
            break
    return [g for g in globs if g]


def _candidates(target: str, project_root: str) -> set:
    """Пути-кандидаты для матчинга глоб: rel-к-корню + исходный аргумент."""
    abspath = os.path.abspath(target)
    rel: Optional[str] = None
    # 1) относительно корня проекта из конфига
    try:
        root_abs = os.path.abspath(project_root)
        if root_abs and abspath.startswith(root_abs + os.sep):
            rel = os.path.relpath(abspath, root_abs)
    except (OSError, ValueError):
        rel = None
    # 2) запасной вариант — git-toplevel (worktree)
    if rel is None:
        search_dir = abspath if os.path.isdir(abspath) else os.path.dirname(abspath)
        try:
            top = subprocess.check_output(
                ["git", "-C", search_dir, "rev-parse", "--show-toplevel"],
                stderr=subprocess.DEVNULL, text=True,
            ).strip()
            if top and abspath.startswith(top):
                rel = os.path.relpath(abspath, top)
        except (OSError, subprocess.SubprocessError):
            rel = None
    return {c for c in (rel, target, target.lstrip("./")) if c}


def find_lessons_for_path(
    target: str, cfg: Optional[MemoryConfig] = None
) -> List[Tuple[str, str]]:
    """Список (имя_файла_урока, описание) уроков, чьи applies_to-глобы матчат target.

    Отсортировано по имени файла. Описание — из `description:` frontmatter ("" если нет).
    """
    cfg = cfg or get_config()
    if not target:
        return []
    candidates = _candidates(target, cfg.project_root)
    out: List[Tuple[str, str]] = []
    for mf in sorted(glob.glob(os.path.join(cfg.memory_dir, "*.md"))):
        fm = _frontmatter(mf)
        if not fm:
            continue
        globs = _applies_globs(fm)
        if not globs:
            continue
        if not any(
            cand == g or fnmatch.fnmatch(cand, g) for g in globs for cand in candidates
        ):
            continue
        dm = _DESC_RE.search(fm)
        desc = dm.group(1).strip() if dm else ""
        out.append((os.path.basename(mf), desc))
    return out


def format_lines(matches: List[Tuple[str, str]]) -> str:
    """Формат вывода хука/CLI: одна строка `- имя: описание` на урок."""
    return "\n".join(f"- {n}: {d}" if d else f"- {n}" for n, d in matches)


def main() -> None:
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else ""
    if not target:
        return
    matches = find_lessons_for_path(target)
    if matches:
        print(format_lines(matches))


if __name__ == "__main__":
    main()
