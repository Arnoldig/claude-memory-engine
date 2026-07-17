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

import datetime
import fnmatch
import glob
import os
import re
import subprocess
from typing import List, Optional, Tuple

from .config import MemoryConfig, get_config
from .lesson_files import lesson_paths

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


def field_value(fm: str, key: str) -> Optional[str]:
    """Сырое значение top-level или вложенного поля `key:` (обрезанное) или None, если поля нет.

    Обобщение `applies_to_value` на любое скалярное поле. Ведущий [ \\t]* ловит и top-level,
    и вложенное под `metadata:`; после двоеточия — [ \\t]* (не \\s*), иначе у ПУСТОГО значения
    regex съест перенос и захватит следующую строку frontmatter как значение (баг 0.9.4).

    Различие None (поля нет) и "" (поле есть, значение пусто) — несущее: на нём стоят все
    жалобы движка «поле задано, но не понято». Без него «не задано» и «задано мусором»
    сливаются в одно, и дефект живёт годами (см. историю applies_to).
    """
    m = re.search(rf"^[ \t]*{re.escape(key)}:[ \t]*(.*)$", fm, re.MULTILINE)
    return None if m is None else m.group(1).strip()


_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def iso_date_or_none(value: str) -> "Optional[datetime.date]":
    """Дата из строгого ISO (`YYYY-MM-DD`) или None, если значение — не дата.

    Единый разбор дат frontmatter/конфига (`reverify_after`, `archived_on`,
    `model_registry_verified_on`) вместо трёх копий regex+try/except: детектор «поле не
    понято» ОБЯЗАН быть точным дополнением парсера, иначе разъедутся и дадут либо ложную
    тишину, либо ложную тревогу. Живёт рядом со `strip_scalar` — там же, где с 0.9.6
    собраны общие хелперы разбора скалярных значений frontmatter.

    `fullmatch` ПЕРЕД `fromisoformat` — не украшение: с Python 3.11 `fromisoformat` ест
    весь ISO 8601, то есть `20260101` и даже `2026-W01-1` (→ 2025-12-29!) молча стали бы
    датой, а на Python <3.11 те же значения — нет. Без якоря поведение зависело бы от
    версии интерпретатора, а слово «строгий» в докстринге и `YYYY-MM-DD` в тексте жалобы
    были бы неправдой. Формат, который движок обещает человеку, ровно один.
    """
    v = strip_scalar(value) if isinstance(value, str) else ""
    if not _ISO_DATE_RE.fullmatch(v):
        return None
    try:
        return datetime.date.fromisoformat(v)
    except ValueError:  # синтаксис верный, даты не существует (2026-02-31)
        return None


def _frontmatter(path: str) -> str:
    try:
        head = read_head(path)
    except OSError:
        return ""
    if not head.startswith("---"):
        return ""
    return head.split("\n---", 1)[0]


def applies_to_value(fm: str) -> Optional[str]:
    """Сырое значение `applies_to:` (текст после двоеточия, обрезанный) или None, если
    поля нет вовсе. Пустая строка = поле ЕСТЬ с пустым значением (YAML-список ниже либо
    объявленная, но не заполненная привязка) — отличать её от None обязательно: на этом
    различии стоит жалоба `unparsed_applies_to`."""
    return field_value(fm, "applies_to")


def _applies_globs(fm: str) -> List[str]:
    """Глобы из `applies_to:` — инлайн-список `[a, b]`, одиночный глоб строкой ИЛИ
    YAML-список из `- `-строк.

    Ведущий [ \\t]* ловит И top-level applies_to, И вложенный под `metadata:` (нативный
    формат памяти harness). После `:` — [ \\t]* (не \\s*), иначе потеряется 1-й элемент
    многострочного списка.

    Порядок веток задан ЗНАЧЕНИЕМ, а не догадкой: `[` → инлайн-список; непустой скаляр →
    один глоб; ПУСТОЕ значение → YAML-список ниже. Скалярная ветка обязана стоять между
    ними и проверять непустоту: до неё `applies_to: "app/x.py"` молча уходил в разбор
    списка, первая же строка ниже (обычно `metadata:`) обрывала цикл → [] без единого
    слова, неотличимо от «уроков нет» (дыра с рождения модуля).
    """
    inline = applies_to_value(fm)
    if inline is None:
        return []
    if inline.startswith("["):
        inner = inline.strip("[]")
        return [g for g in (strip_scalar(x) for x in inner.split(",")) if g]
    if inline.startswith("{"):
        # YAML-отображение (или bash-подобное `{a,b}/x.py`) — глобом НЕ бывает: ни fnmatch,
        # ни glob фигурные скобки не раскрывают. Отдаём пусто → сработает жалоба
        # `unparsed_applies_to` с верным диагнозом. Без этой ветки скаляр ниже проглотил бы
        # `{…}` как «глоб», тот не совпал бы ни с чем и уехал в отчёт ПРОТУХШИХ привязок
        # («путь не найден — файл переехал?») — диагноз мимо, чинить будут не то.
        return []
    if inline:
        return [g for g in (strip_scalar(inline),) if g]
    globs: List[str] = []
    for line in fm[_APPLIES_RE.search(fm).end():].splitlines():
        ls = line.strip()
        if ls.startswith("- "):
            globs.append(strip_scalar(ls[2:]))
        elif ls and not ls.startswith("#"):
            break
    return [g for g in globs if g]


def unparsed_applies_to(fm: str) -> Optional[str]:
    """Сырое значение `applies_to:`, если поле ЕСТЬ, но глобов из него НЕ вышло; иначе None.

    Опора обеих жалоб движка (немедленной на записи урока и сводной в _stale_pending).
    Такой урок никогда не всплывёт на правке — а выглядит настроенным. Молчать здесь
    нельзя: пустой результат разбора неотличим от «привязок нет», и дефект живёт годами
    (ровно так прожил скалярный `applies_to`)."""
    raw = applies_to_value(fm)
    if raw is None or _applies_globs(fm):
        return None
    return raw


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


def _in_claude_tooling(target: str) -> bool:
    """Путь лежит в служебном `.claude/` (но НЕ в `.claude/worktrees/<wt>/` — там проектные
    файлы: проект может работать из worktree, и они не служебные)."""
    norm = os.path.abspath(target).replace("\\", "/")
    return "/.claude/" in norm and "/.claude/worktrees/" not in norm


def _glob_targets_claude(g: str) -> bool:
    """Глоб АДРЕСУЕТ `.claude/` явно (а не попал туда широким `*.py`)."""
    return ".claude/" in g.replace("\\", "/")


def find_lessons_for_path(
    target: str, cfg: Optional[MemoryConfig] = None
) -> List[Tuple[str, str]]:
    """Список (имя_файла_урока, описание) уроков, чьи applies_to-глобы матчат target.

    Отсортировано по имени файла. Описание — из `description:` frontmatter ("" если нет).

    Для путей внутри служебного `.claude/` действует ЯВНОЕ правило: матчат только те
    глобы, что сами адресуют `.claude/`. Так уживаются две правды, каждая из которых
    раньше побеждала целиком и была неправа:
      • широкий глоб (`*.py` — fnmatch `*` матчит и слэши) НЕ должен всплывать на
        служебных файлах; прежний общий пропуск `.claude/` защищал именно от этого;
      • но проекты держат в `.claude/` НАСТОЯЩИЙ код (стражи выкладки, хуки), и у ЧеКи
        есть урок, ПРЕДПИСЫВАЮЩИЙ привязывать уроки о движке к вендоренной копии
        (`.claude/memory_engine/claude_memory/…`). Общий пропуск молча отменял явную
        привязку — 12 уроков в двух проектах не всплывали никогда.
    Правило живёт ЗДЕСЬ, а не в хуке: иначе половины движка расходятся (так и было —
    ретривер `.claude/` никогда не пропускал, и по упоминанию пути в запросе урок
    находился, а на правке того же файла молчал).
    """
    cfg = cfg or get_config()
    if not target:
        return []
    candidates = _candidates(target, cfg.project_root)
    tooling = _in_claude_tooling(target)
    out: List[Tuple[str, str]] = []
    # Набор уроков — общий (`lesson_files`), а не голый glob `*.md`: до 0.10.0 здесь был
    # ТРЕТИЙ вариант определения урока — без исключения ядра/указателя/приватных, с одним
    # лишь фильтром «есть frontmatter». Сегодня не стреляло (у ядра frontmatter'а нет), но
    # это ровно та мина, что и подорвалась у стража: определение, живущее своей жизнью.
    for mf in lesson_paths(cfg):
        fm = _frontmatter(mf)
        if not fm:
            continue
        globs = _applies_globs(fm)
        if tooling:
            globs = [g for g in globs if _glob_targets_claude(g)]
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
