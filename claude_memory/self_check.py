"""Самодиагностика конфигурации (SessionStart + CLI). Без сети, ноль токенов.

Ловит ошибки настройки, которые иначе тихо портят работу весь сеанс:
  (1) плейсхолдеры messages-override: `{x}` должны быть ПОДМНОЖЕСТВОМ плейсхолдеров
      дефолта того же ключа, иначе `.format` не подставит значение (как было
      `{len(cards)}` вместо `{card_count}`). `msg()` деградирует на дефолт и не падает,
      но текст выходит неверный/английский — чиним в источнике (конфиге проекта);
  (2) ОПЕЧАТКИ в именах ключей конфига (`typo_key_issues`, difflib);
  (3) битые пользовательские regex-шаблоны (`bad_regex_issues`) — страж молча выключался;
  (4) поля-даты конфига не в ISO (`bad_date_issues`) — страж молча выключался;
  (5) расхождение с настройками ХОЗЯИНА — Claude Code (`settings_issues`): движок читает
      не ту папку, куда пишет авто-память, либо авто-память выключена и уроки писать
      некому вовсе. Самая дорогая в диагностике: всё выглядит рабочим, а стражи слепы.
Общее у (2)–(5): человек ОСМЫСЛЕННО что-то задал (или не задал), а движок молча сделал
вид, что всё хорошо. Молчание тут неотличимо от «всё в порядке» — поэтому говорим вслух.

ГРАНИЦА — ПО ФУНКЦИЯМ, а не по модулю: право бежать на каждом SessionStart принадлежит не
модулю целиком, а конкретному пути.
  • `warnings()` / `run()` — ПУТЬ SessionStart. Читает конфиги (свой и Claude Code) и
    МЕТАДАННЫЕ каталогов (существование, наличие/число файлов), но СОДЕРЖИМОЕ уроков —
    НИКОГДА. Отсюда и лицензия бежать каждую сессию.
  • `report()` — ТОЛЬКО verbose-CLI, по явной просьбе человека. Содержимое читает: разбивка
    по типам ради того и заведена. На SessionStart не попадает никогда.
Формулировка именно такая, потому что модульный инвариант «никогда не читаем уроки»
опровергался бы собственным `report()` — и следующий человек «починил» бы противоречие
одним из двух дурных способов: ослабил инвариант до бессмыслицы либо выпотрошил отчёт.

Дефекты в данных уроков (непонятые поля frontmatter) живут в `staleness.scan_unparsed` →
`_stale_pending`: там обход корпуса уже оплачен.

Триггер: SessionStart, КАЖДУЮ сессию (не throttle) — битая настройка актуальна, пока её
не исправят, и должна быть видна на старте. Шум от этого не копится: такие жалобы чинятся
один раз навсегда. Плюс ручной CLI (verbose) для setup.

"""
from __future__ import annotations

import difflib
import re
from dataclasses import replace
from typing import List, Tuple

from . import claude_code_env
from .applies_to import iso_date_or_none, read_head
from .catalog_generate import parse_frontmatter
from .config import MemoryConfig, get_config
from .lesson_files import lesson_paths, lesson_type
from .messages import DEFAULT_MESSAGES, msg

_PH_RE = re.compile(r"\{([^{}]+)\}")


def _placeholders(template: str) -> set:
    """Имена плейсхолдеров `{x}` в шаблоне (включая невалидные вроде `len(cards)`)."""
    return {m.group(1) for m in _PH_RE.finditer(template)}


def message_placeholder_issues(cfg: MemoryConfig) -> List[Tuple[str, set]]:
    """[(ключ, лишние_плейсхолдеры)] для override'ов, чьи плейсхолдеры НЕ ⊆ дефолта.

    Ключ-сирота (нет в дефолтах) пропускаем — он не ломает форматирование (msg()
    отдаёт его как есть); отдельная гигиена, не предмет этой проверки.
    """
    issues: List[Tuple[str, set]] = []
    overrides = getattr(cfg, "messages", None) or {}
    for key, template in overrides.items():
        default = DEFAULT_MESSAGES.get(key)
        if default is None:
            continue
        extra = _placeholders(str(template)) - _placeholders(default)
        if extra:
            issues.append((key, extra))
    return sorted(issues, key=lambda x: x[0])


def typo_key_issues(cfg: MemoryConfig) -> List[Tuple[str, str]]:
    """[(неизвестный_ключ, похожий_известный)] — вероятные ОПЕЧАТКИ в именах полей конфига.

    Молчаливое отбрасывание неизвестных ключей (`config._coerce`) задумано ради
    forward-compat: старый движок не должен падать на конфиге новой версии. Поэтому лаять
    на ВСЕ неизвестные ключи нельзя — это сломало бы само намерение. Но опечатка тоже
    отбрасывается молча, и человек получает английский дефолт вместо своей настройки
    (так уже случилось со стражем закрытия задачи).

    Разводим две популяции по близости к известному имени (`difflib`, stdlib): опечатка по
    построению близка к существующему ключу, честно новый/чужой ключ — нет. Ключи с
    ведущим `_` пропускаем ВСЕГДА: это принятое соглашение для заметок-комментариев внутри
    JSON (у конфига живого проекта есть `_task_close_pattern_note`, и он близок к
    `task_close_pattern` — без этого правила жалоба была бы ложной на каждом старте).
    """
    known = sorted(MemoryConfig.__dataclass_fields__)  # type: ignore[attr-defined]
    out: List[Tuple[str, str]] = []
    for key in getattr(cfg, "unknown_config_keys", ()) or ():
        if key.startswith("_"):
            continue
        near = difflib.get_close_matches(key, known, n=1, cutoff=0.8)
        if near:
            out.append((key, near[0]))
    return out


def bad_regex_issues(cfg: MemoryConfig) -> List[Tuple[str, str]]:
    """[(поле, текст_ошибки)] для пользовательских regex-шаблонов, которые НЕ компилируются.

    Битый шаблон движок ловит `except re.error` и возвращает «не совпало» — страж молча
    выключается, а «выключен» неотличимо от «ничего не нашёл». Худший класс дефекта:
    отсутствие сигнала выглядит как «всё хорошо».
    """
    out: List[Tuple[str, str]] = []
    for fieldname in ("session_close_pattern", "task_close_pattern"):
        pattern = getattr(cfg, fieldname, "") or ""
        if not pattern:
            continue  # пусто = страж намеренно выключен, это не дефект
        try:
            re.compile(pattern)
        except re.error as e:
            out.append((fieldname, str(e)))
    return out


def settings_issues(cfg: MemoryConfig) -> List[str]:
    """Готовые жалобы на РАСХОЖДЕНИЕ с настройками хозяина (Claude Code). Пусто — тихо.

    Общее с остальными проверками модуля: человек что-то настроил (или НЕ настроил), а
    движок молча делает вид, что всё хорошо. Здесь цена молчания максимальна: уроки пишет
    авто-память Claude Code, и если движок читает не ту папку — он честно не видит ничего,
    страж требует записать урок, урок пишут, страж требует снова. Часы диагностики вслепую.

    Три жалобы, от смертельной к бытовой:
      (1) авто-память ВЫКЛЮЧЕНА, а стражи уроков включены → уроки писать НЕКОМУ (движок их
          не создаёт, он читатель). Единственная комбинация с настоящим вечным тупиком, и
          достижима одной строкой в settings.json;
      (2) `autoMemoryDirectory` задан ЯВНО и не совпадает с memory_dir → движок читает не
          ту папку. Явное значение — не догадка, поэтому жалуемся уверенно;
      (3) memory_dir пуст, а рядом есть НЕПУСТАЯ папка авто-памяти → почти наверняка
          наследие сломанного дефолта установщика (до 0.10.0 это был `~/.claude/memory`).
          Жалуемся ТОЛЬКО когда догадка подтверждена диском: своих уроков ноль И у хозяина
          они есть. Видит движок хоть один урок — молчим (возможен намеренный корпус).
    Fail-open везде: нет файла / битый JSON / git недоступен → молчание.

    ЦЕНА (замерено): чтение settings.json — 0.15 мс на область, ерунда. Дорог git-вызов
    внутри (3) — 14 мс. Поэтому порядок условий тут не косметика: дешёвый glob «вижу ли я
    уроки» стоит ПЕРВЫМ и отсекает git у всех, у кого всё настроено. Платит только тот,
    кто уже сломан, — а ему 14 мс не жалко.
    """
    out: List[str] = []
    root = cfg.project_root

    disabled_at = claude_code_env.auto_memory_disabled(root)
    if disabled_at and (cfg.stop_lessons_enabled or cfg.task_close_lesson_gate):
        out.append(msg(cfg, "self_check.auto_memory_off", scope=disabled_at))

    explicit = claude_code_env.configured_auto_memory_dir(root)
    if explicit:
        if not claude_code_env.same_dir(cfg.memory_dir, explicit[0]):
            out.append(msg(cfg, "self_check.memory_dir_mismatch",
                           memory_dir=cfg.memory_dir, auto_dir=explicit[0], scope=explicit[1]))
    elif not _has_lessons(cfg):   # ← дешёвый glob ПЕРЕД git-вызовом ниже
        confirmed = claude_code_env.existing_auto_memory_dir(root)
        if confirmed and not claude_code_env.same_dir(cfg.memory_dir, confirmed):
            out.append(msg(cfg, "self_check.memory_dir_empty_elsewhere",
                           memory_dir=cfg.memory_dir, auto_dir=confirmed,
                           count=len(lesson_paths(replace(cfg, memory_dir=confirmed)))))
    return out


def _has_lessons(cfg: MemoryConfig) -> bool:
    """Видит ли движок хоть один урок в своём memory_dir (без чтения содержимого)."""
    try:
        return bool(lesson_paths(cfg))
    except OSError:
        return True   # не смогли посмотреть → не жалуемся (fail-open)


def bad_date_issues(cfg: MemoryConfig) -> List[Tuple[str, str]]:
    """[(поле, значение)] для полей-дат конфига не в строгом ISO.

    `model_registry_verified_on` в виде `01.01.2026` → страж сверки линейки молча
    выключается. Человек его ЗАПОЛНИЛ, то есть намеренно включал — и получил тишину,
    неотличимую от «выключено» (дефолт поля — None = выключено)."""
    out: List[Tuple[str, str]] = []
    for fieldname in ("model_registry_verified_on",):
        raw = getattr(cfg, fieldname, None)
        if raw and iso_date_or_none(str(raw)) is None:
            out.append((fieldname, str(raw)))
    return out


def warnings(cfg: MemoryConfig = None, verbose: bool = False) -> List[str]:
    """Готовые строки-предупреждения самодиагностики (пусто, если всё чисто).

    verbose=True (CLI-режим, человек сам попросил проверку при настройке) добавляет ВСЕ
    неизвестные ключи справочно — так закрывается слепая зона difflib: опечатка, далёкая
    от любого известного имени. На SessionStart этого нет намеренно: там лаем только на
    похожие, иначе forward-compat-ключи шумели бы каждую сессию.
    """
    cfg = cfg or get_config()
    out = [
        msg(cfg, "self_check.bad_placeholder", msg_key=key, extras=", ".join(sorted(extra)))
        for key, extra in message_placeholder_issues(cfg)
    ]
    out += [msg(cfg, "self_check.typo_key", key=key, near=near)
            for key, near in typo_key_issues(cfg)]
    out += [msg(cfg, "self_check.bad_regex", field=f, error=e) for f, e in bad_regex_issues(cfg)]
    out += [msg(cfg, "self_check.bad_date", field=f, value=v) for f, v in bad_date_issues(cfg)]
    out += settings_issues(cfg)
    if verbose:
        flagged = {k for k, _ in typo_key_issues(cfg)}
        rest = [k for k in (getattr(cfg, "unknown_config_keys", ()) or ())
                if k not in flagged and not k.startswith("_")]
        if rest:
            out.append(msg(cfg, "self_check.unknown_keys_info", keys=", ".join(rest)))
    return out


def report(cfg: MemoryConfig = None) -> List[str]:
    """Картина настройки для ЧЕЛОВЕКА (CLI): куда смотрит движок, куда пишет Claude Code,
    сходятся ли, сколько уроков видно и каких типов.

    Зачем отдельно от жалоб. Жалобы отвечают «что сломано», и при чистом конфиге их нет —
    человеку нечем ПРОВЕРИТЬ настройку, только узнать о поломке. А проверять надо: разъезд
    каталогов выглядит ровно как «всё хорошо, просто уроков пока нет». Отчёт печатается
    ВСЕГДА (в т.ч. когда всё в порядке) и отвечает «что настроено».

    Только для verbose-CLI: на SessionStart это был бы шум каждую сессию.
    """
    cfg = cfg or get_config()
    root = cfg.project_root
    out = [msg(cfg, "self_check.report_header")]
    out.append(msg(cfg, "self_check.report_memory_dir", memory_dir=cfg.memory_dir))

    explicit = claude_code_env.configured_auto_memory_dir(root)
    if explicit:
        auto_dir, note = explicit[0], msg(cfg, "self_check.report_note_explicit", scope=explicit[1])
    else:
        auto_dir, trusted = claude_code_env.resolve_auto_memory_dir(root)
        note = "" if trusted else msg(cfg, "self_check.report_note_derived")
    out.append(msg(cfg, "self_check.report_auto_dir", auto_dir=auto_dir or "?",
                   note=("\n" + note if note else "")))
    out.append(msg(cfg, "self_check.report_match",
                   verdict="yes" if claude_code_env.same_dir(cfg.memory_dir, auto_dir) else "NO"))

    paths = lesson_paths(cfg)
    types: dict = {}
    for p in paths:
        t = lesson_type(parse_frontmatter(read_head(p))) or "(no type)"
        types[t] = types.get(t, 0) + 1
    types_str = ("  [" + ", ".join(f"{k}: {v}" for k, v in sorted(types.items())) + "]") if types else ""
    out.append(msg(cfg, "self_check.report_lessons", count=len(paths), types=types_str))

    disabled_at = claude_code_env.auto_memory_disabled(root)
    if disabled_at:
        out.append(msg(cfg, "self_check.report_auto_off", scope=disabled_at))
    return out


def run(cfg: MemoryConfig = None) -> str:
    """Текст самодиагностики для встраивания в SessionStart (или '')."""
    return "\n".join(warnings(cfg))


def main() -> None:
    """CLI: `python3 -m claude_memory.self_check` — проверить конфиг при настройке проекта.

    Печатает КАРТИНУ всегда (человек просил проверку — он должен увидеть, что настроено),
    затем жалобы. Код возврата 1 при жалобах — чтобы годился в скрипты.
    """
    import sys

    cfg = get_config()
    for line in report(cfg):
        print(line)
    issues = warnings(cfg, verbose=True)
    if not issues:
        print(msg(cfg, "self_check.ok"))
        return
    print()
    for w in issues:
        print(w, file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
