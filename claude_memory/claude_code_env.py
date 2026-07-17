"""Знание о среде-хозяине: где Claude Code держит свою авто-память и включена ли она.

Зачем движку это знать. Файлы уроков создаёт НЕ движок — их пишет встроенная авто-память
Claude Code. Движок в этом каталоге гость: читает, индексирует, сторожит. Значит `memory_dir`
движка обязан указывать ровно туда, куда пишет хозяин, иначе движок читает пустоту, а
страж требует записать урок, которого в его папке никогда не появится.

Проверено на живых данных: `cli.py` до 0.10.0 ставил по умолчанию `~/.claude/memory`, тогда
как Claude Code пишет в `~/.claude/projects/<slug>/memory`. То есть разъезд каталогов был
ПОВЕДЕНИЕМ ПО УМОЛЧАНИЮ, а не ошибкой пользователя: поставил без явного пути → вечный
Stop-блок и диагностика вслепую.

ЧЕСТНАЯ ГРАНИЦА ЗНАНИЯ. Правило вывода слага НЕ задокументировано — оно выведено обратной
инженерией и проверено на трёх боевых проектах (включая кириллические пути). Поэтому:
  • догадку НИКОГДА не подают молча как истину — она либо подтверждена диском
    (`existing_auto_memory_dir`), либо сопровождается явной оговоркой;
  • явно заданный `autoMemoryDirectory` всегда сильнее вычисленного;
  • всё fail-open: нет файла / битый JSON / git недоступен → None, движок работает дальше.

Источник (сверено 2026-07-17): https://code.claude.com/docs/en/memory.md,
https://code.claude.com/docs/en/settings.md
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

# Переменная окружения-киллсвитч авто-памяти (бьёт мимо settings.json).
DISABLE_ENV = "CLAUDE_CODE_DISABLE_AUTO_MEMORY"

# Области settings.json от СИЛЬНОЙ к слабой. Managed policy и `--settings` недостижимы
# из библиотеки — принимаем это ограничение осознанно (лучше не знать, чем соврать).
_SETTINGS_SCOPES = (
    ".claude/settings.local.json",   # local  — сильнее project
    ".claude/settings.json",         # project
)


def project_slug(path: str) -> str:
    """Слаг каталога проекта в `~/.claude/projects/<slug>` (правило Claude Code).

    Каждый символ вне [a-zA-Z0-9] → дефис. Проверено на трёх боевых проектах, включая
    кириллицу: `/Users/v/Claude/Чеки/Projects/cheki_001` → `-Users-v-Claude------Projects-cheki-001`
    (каждая кириллическая буква даёт свой дефис, `_` тоже становится дефисом).

    ПРАВИЛО НЕ ЗАДОКУМЕНТИРОВАНО — результат обязателен к проверке на диске, см. модульный
    докстринг."""
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


def main_checkout(cwd: str) -> Optional[str]:
    """Корень ОСНОВНОГО чекаута git-репозитория ("" / None, если не git).

    Не `--show-toplevel`: в git-worktree он вернул бы путь worktree, а каталог авто-памяти
    у Claude Code — общий на весь репозиторий («all worktrees and subdirectories within the
    same repo share one auto memory directory»). Общий `.git` даёт основной чекаут:
    `<main>/.git` → dirname → `<main>`.

    `--path-format=absolute` требует git ≥ 2.31 (2021). На более старом git вызов падает →
    None → слаг считается от самого project_root → почти наверняка промах → но
    `existing_auto_memory_dir` его не подтвердит, и путь уйдёт к человеку С ОГОВОРКОЙ.
    То есть на древнем git деградируем в предупреждение, а не во враньё."""
    try:
        out = subprocess.check_output(
            ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            stderr=subprocess.DEVNULL, text=True, timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out:
        return None
    return os.path.dirname(out) or None


def default_auto_memory_dir(project_root: str) -> Optional[str]:
    """ВЫЧИСЛЕННЫЙ (не подтверждённый) путь авто-памяти Claude Code для проекта.

    `~/.claude/projects/<slug основного чекаута>/memory`. Вне git слаг считаем от самого
    project_root: у Claude Code слаг выводится «из git-репозитория», но для не-git каталога
    он всё равно чем-то будет — и проверка диском (`existing_auto_memory_dir`) отсеет
    промах. Здесь мы не утверждаем, а предлагаем кандидата."""
    root = main_checkout(project_root) or os.path.abspath(project_root)
    return str(Path.home() / ".claude" / "projects" / project_slug(root) / "memory")


def existing_auto_memory_dir(project_root: str) -> Optional[str]:
    """Путь авто-памяти, ПОДТВЕРЖДЁННЫЙ диском: каталог существует И непуст (есть *.md).

    Это и есть способ не гадать: догадка о слаге, подкреплённая реальными файлами хозяина,
    — уже не догадка. Пусто/нет каталога → None (звать `default_auto_memory_dir` и честно
    оговариваться)."""
    guess = default_auto_memory_dir(project_root)
    if not guess:
        return None
    try:
        if any(Path(guess).glob("*.md")):
            return guess
    except OSError:
        return None
    return None


def _read_settings(project_root: str) -> List[Tuple[str, dict]]:
    """[(область, данные)] прочитанных settings.json от СИЛЬНОЙ области к слабой.

    Fail-open: нечитаемый/битый JSON просто пропускается (движок не вправе падать из-за
    чужого файла)."""
    paths = [(scope, Path(project_root) / scope) for scope in _SETTINGS_SCOPES]
    paths.append(("~/.claude/settings.json", Path.home() / ".claude" / "settings.json"))
    out: List[Tuple[str, dict]] = []
    for scope, p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.append((scope, data))
    return out


def configured_auto_memory_dir(project_root: str) -> Optional[Tuple[str, str]]:
    """(путь, область) из ЯВНО заданного `autoMemoryDirectory`, иначе None.

    Значение обязано быть абсолютным или начинаться с `~/` (требование Claude Code);
    относительный путь не поддерживается — такой ключ игнорируем, он всё равно не работает
    и у хозяина."""
    for scope, data in _read_settings(project_root):
        raw = data.get("autoMemoryDirectory")
        if not isinstance(raw, str) or not raw.strip():
            continue
        raw = raw.strip()
        if not (raw.startswith("~/") or os.path.isabs(raw)):
            continue
        return str(Path(raw).expanduser()), scope
    return None


def auto_memory_disabled(project_root: str) -> Optional[str]:
    """Где именно выключена авто-память ("" / None — не выключена).

    Возвращает область (для внятной жалобы). Env-киллсвитч проверяем тоже: он бьёт мимо
    settings.json, и без него диагностика соврала бы «включена»."""
    if os.environ.get(DISABLE_ENV, "").strip() not in ("", "0", "false", "False"):
        return f"env {DISABLE_ENV}"
    for scope, data in _read_settings(project_root):
        if data.get("autoMemoryEnabled") is False:
            return scope
    return None


def resolve_auto_memory_dir(project_root: str) -> Tuple[Optional[str], bool]:
    """(путь авто-памяти, подтверждён ли). Единая точка для установщика и самодиагностики.

    Приоритет: явный `autoMemoryDirectory` → подтверждённая диском догадка → голая догадка.
    Второй элемент — «можно ли этому верить»: True для явного значения и для догадки с
    реальными файлами; False — для голой догадки (её обязаны сопроводить оговоркой)."""
    explicit = configured_auto_memory_dir(project_root)
    if explicit:
        return explicit[0], True
    confirmed = existing_auto_memory_dir(project_root)
    if confirmed:
        return confirmed, True
    return default_auto_memory_dir(project_root), False


def same_dir(a: Optional[str], b: Optional[str]) -> bool:
    """Один ли это каталог: сравнение после expanduser+realpath (symlink/`~`/`..` не должны
    давать ложное расхождение). None с любой стороны → False (нечего сравнивать)."""
    if not a or not b:
        return False
    try:
        return os.path.realpath(os.path.expanduser(a)) == os.path.realpath(os.path.expanduser(b))
    except OSError:
        return False
