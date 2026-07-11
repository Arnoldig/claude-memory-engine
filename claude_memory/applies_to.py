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
_DESC_RE = re.compile(r"^description:[ \t]*(.*)$", re.MULTILINE)  # [ \t]* не \s*: пустое поле не хватает следующую строку


def read_head(path: str, cap: int = 65536) -> str:
    """Начало файла до cap байт (по умолч. 64К). Покрывает весь frontmatter любого
    реального урока + начало тела. Заменяет прежние фикс.окна 2000/4000, которые молча
    ТЕРЯЛИ длинный frontmatter (и applies_to-глоб, и поля для ретривера). Уроки малы —
    цена чтения ничтожна. OSError пробрасывается вызывающему."""
    with open(path, encoding="utf-8") as f:
        return f.read(cap)


def strip_scalar(value: str) -> str:
    """Очистка скалярного значения frontmatter: пробелы по краям + снятие одного слоя
    обрамляющих кавычек (`"…"` или `'…'`).

    Единый хелпер вместо копий идиомы `.strip().strip('"').strip("'")` по всем парсерам
    (catalog_generate.parse_frontmatter, memory_retrieve.read_fields, applies_to,
    staleness) — чтобы снятие кавычек было ОДИНАКОВЫМ во всех потребителях frontmatter.
    Раньше `applies_to`/`staleness` снимали только пробелы → `description: "…"` в кавычках
    показывался с кавычками в «уроках по пути» и `_stale_pending`, но без — в CATALOG и
    поиске (половины системы расходились в отображении). DRY-инвариант держит их в лаге."""
    return value.strip().strip('"').strip("'")


def _frontmatter(path: str) -> str:
    try:
        head = read_head(path)
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
    """Пути-кандидаты для матчинга глоб: исходный аргумент + rel-к-корню-проекта +
    rel-к-git-toplevel.

    Оба rel считаем ВСЕГДА и добавляем оба (а не «или»): в worktree-сессии путь лежит
    под главным project_root (rel получится `.claude/worktrees/<wt>/app/x.py` — НЕ
    матчит глоб `app/*`), но git-toplevel = корень worktree → rel `app/x.py` матчит.
    Так applies_to работает одинаково и в worktree, и вне его (#memory-lib-cutover).
    """
    abspath = os.path.abspath(target)
    # removeprefix («./»), НЕ lstrip(«./»): lstrip снимает КЛАСС символов {'.', '/'} и
    # портит dotfile-пути ('.github/x.yml' → 'github/x.yml'), порождая ложный кандидат,
    # способный совпасть с typo-глобом. Нужно снять ровно ведущий «./» относительного пути.
    cands = {target, target.removeprefix("./")}
    # 1) относительно корня проекта из конфига
    try:
        root_abs = os.path.abspath(project_root)
        if root_abs and abspath.startswith(root_abs + os.sep):
            cands.add(os.path.relpath(abspath, root_abs))
    except (OSError, ValueError):
        pass
    # 2) относительно git-toplevel (в worktree это корень worktree → совпадает с глобами)
    search_dir = abspath if os.path.isdir(abspath) else os.path.dirname(abspath)
    try:
        top = subprocess.check_output(
            ["git", "-C", search_dir, "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        if top and abspath.startswith(top + os.sep):
            cands.add(os.path.relpath(abspath, top))
    except (OSError, subprocess.SubprocessError):
        pass
    return {c for c in cands if c}


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
        desc = strip_scalar(dm.group(1)) if dm else ""
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
