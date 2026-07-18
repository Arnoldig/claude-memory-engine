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
        except (re.error, TypeError) as e:
            # TypeError — не педантизм: `_coerce` типы строковых полей не приводит, поэтому
            # `"task_close_pattern": 42` из JSON доедет сюда числом и уронит весь
            # `warnings()`, а с ним и все остальные жалобы самодиагностики.
            out.append((fieldname, str(e)))
    return out


def close_pattern_lag_issues(cfg: MemoryConfig) -> List[Tuple[str, List[str]]]:
    """[(поле, [неузнанные формы])] — проектный шаблон закрытия ОТСТАЛ от эталона.

    ЧТО ЭТО ЗА КЛАСС. Часть полей потребитель копирует из дефолта, чтобы ДОПИСАТЬ своё,
    а не заменить: `task_close_pattern` берут целиком и добавляют русскую ветку. С этой
    минуты копия заморожена на том виде, какой дефолт имел в день копирования, а
    библиотека свой дефолт расширяет. Так семья `resolve/resolves/resolved`, добавленная
    в 0.10.0, не доехала НИ ДО ОДНОГО потребителя: полтора релиза `Resolves #N` тихо не
    распознавался в обоих боевых проектах. Отставший шаблон коварнее узкого — он был
    ПРАВИЛЬНЫМ, его никто не писал криво, и повод перепроверить не возникает.
    Соседние классы уже закрыты: СЛОМАННЫЙ ловит `bad_regex_issues`, а вот ОТСТАВШИЙ
    компилируется, работает и молчит.

    ПРИЗНАК — «ЧАСТИЧНОЕ ПОКРЫТИЕ», А НЕ «ОТЛИЧАЕТСЯ ОТ ДЕФОЛТА». Сравнивать текст с
    дефолтом нельзя дважды. Во-первых, переопределение НАМЕРЕННО — жалоба «у тебя не как
    в дефолте» навязчива, неустранима, и её отключат первой. Во-вторых, текстовый признак
    ломается ровно на целевом случае: отставшая копия содержит СТАРЫЙ дефолт как
    подстроку, то есть выглядит «построенной на дефолте» и после того, как отстала.
    Поэтому судим по ПОВЕДЕНИЮ: гоняем девять форм через тот же `extract_closed_task`,
    который вызывает боевой страж, и жалуемся, только если узнаётся ЧАСТЬ из них.
    Ни одной (0/9) — это законная полная замена под чужой трекер, молчим. Все (9/9) —
    шаблон покрывает эталон, что бы он ни добавлял сверху, молчим.
    Намеренное сужение ВНУТРИ девятки не глушится и заглушки не получает: GitHub закроет
    задачу на любом из девяти слов независимо от мнения движка, значит частичный шаблон
    недосрабатывает объективно. Ручка «не жалуйся на неполноту» воспроизвела бы исходный
    класс — страж, выключенный, чтобы не мешал.

    ПОЧЕМУ ПОЛЕ ЗДЕСЬ ОДНО. Признак применим только там, где у дефолта есть ЗАКРЫТЫЙ
    внешний перечень форм. Проверены все поля конфига; таких оказалось ровно одно.
    Ближайшие кандидаты и почему выпали: `lesson_prefixes` — с 0.10.0 не решает «что
    такое урок» (на это отвечает `lesson_files.is_lesson_file`), то есть перестал быть
    гейтом; `session_close_pattern` — его дефолт нейтральная английская фраза-заготовка,
    проект ОБЯЗАН задать свои формы, и покрывать там нечего; `known_model_substrs` /
    `strongest_model_substr` — самокорректируются рантайм-стражами при встрече незнакомой
    модели. Остальное — пороги, пути, таксономия и тексты, где расхождение с дефолтом это
    норма. Поэтому механизм НАМЕРЕННО узкий: реестр «поле → эталон» с единственной
    записью был бы обобщением вперёд спроса. Форма возврата при этом общая — второе поле,
    если появится, добавится строкой.
    """
    if not getattr(cfg, "task_close_lesson_gate", False):
        return []  # страж выключен целиком — жалоба на неиспользуемый шаблон была бы шумом
    pattern = getattr(cfg, "task_close_pattern", "") or ""
    if not pattern:
        return []  # пусто = страж намеренно выключен, как и в `bad_regex_issues`
    try:
        re.compile(pattern)
    except (re.error, TypeError):
        return []  # сломанный шаблон — дело `bad_regex_issues`; одна беда, одна жалоба.
        # TypeError здесь не теоретический: `_coerce` типы строковых полей не приводит, и
        # число в JSON доезжает до `re.compile` как есть. Без этого перехвата падал бы
        # весь `warnings()` разом — то есть человек терял бы ВСЕ жалобы самодиагностики
        # из-за одной описки в одном поле.
    from .stop_check import GITHUB_CLOSE_KEYWORDS, extract_closed_task

    missing = [
        word for word in GITHUB_CLOSE_KEYWORDS
        if extract_closed_task(f"feat: {word} #42", pattern) != "42"
    ]
    if not missing or len(missing) == len(GITHUB_CLOSE_KEYWORDS):
        return []  # покрыто целиком ИЛИ заменено целиком — оба случая законны
    return [("task_close_pattern", missing)]


def settings_issues(cfg: MemoryConfig) -> List[str]:
    """Готовые жалобы на РАСХОЖДЕНИЕ с настройками хозяина (Claude Code). Пусто — тихо.

    Общее с остальными проверками модуля: человек что-то настроил (или НЕ настроил), а
    движок молча делает вид, что всё хорошо. Здесь цена молчания максимальна: уроки пишет
    авто-память Claude Code, и если движок читает не ту папку — он честно не видит ничего,
    страж требует записать урок, урок пишут, страж требует снова. Часы диагностики вслепую.

    Четыре жалобы, от смертельной к бытовой:
      (1) авто-память ВЫКЛЮЧЕНА, а стражи уроков включены → уроки писать НЕКОМУ (движок их
          не создаёт, он читатель). Единственная комбинация с настоящим вечным тупиком, и
          достижима одной строкой в settings.json;
      (2) `autoMemoryDirectory` задан ЯВНО и не совпадает с memory_dir → движок читает не
          ту папку. Явное значение — не догадка, поэтому жалуемся уверенно;
      (3) memory_dir ПУСТ, а рядом есть НЕПУСТАЯ папка авто-памяти → почти наверняка
          наследие сломанного дефолта установщика (до 0.10.0 это был `~/.claude/memory`);
      (4) memory_dir НЕПУСТ, но папка авто-памяти всё равно другая → корпус, который видит
          движок, — мёртвый хвост: НОВЫЕ уроки пишутся мимо. Типичный путь сюда никто не
          выбирал: переезд/переименование каталога репозитория меняет слаг, Claude Code
          начинает писать в новый каталог, а memory_dir остаётся со старым.
    (3) и (4) — один и тот же разъезд, разнесены только формулировкой: «своих уроков нет»
    и «свои есть, но устарели навсегда» требуют разного текста, а не разной строгости.

    ПОЧЕМУ (4) ПОЯВИЛАСЬ. До 0.12.0 разъезд при непустом memory_dir считался законным:
    «видит хоть один урок — молчим, возможен намеренный корпус». Обоснование ложное, и
    опровергает его сам модуль: уроки пишет ТОЛЬКО авто-память Claude Code. Значит связка
    «папки разъехались + стражи уроков включены» неудовлетворима по построению — страж
    требует свежий урок, урок уходит в другую папку, движок его не увидит НИКОГДА. Держать
    отдельный корпус намеренно можно, но лишь с ВЫКЛЮЧЕННЫМИ стражами; там (4) и молчит.
    По той же причине (4) молчит при выключенной авто-памяти: «новые уроки пишутся в другую
    папку» было бы враньём — они не пишутся никуда, и про это уже сказала (1).

    Fail-open везде: нет файла / битый JSON / git недоступен → молчание.

    ЦЕНА (замерено): чтение settings.json — 0.15 мс на область, ерунда. Дорог git-вызов
    внутри (3)/(4) — 14 мс, а в патологии до `timeout=5` с. До 0.12.0 его отсекал дешёвый
    glob «вижу ли я уроки», но (4) ровно про случай, когда уроки видны, — этот отсекатель
    больше не годится. Заменён на равноценно дешёвый и более прицельный: у ОСНОВНОГО
    чекаута каталог авто-памяти вычисляется без git вовсе (`..._without_git`, один stat), и
    совпадение с memory_dir закрывает вопрос. Здоровый проект платит столько же, сколько
    платил; git остаётся worktree-сессиям и уже сломанным.
    """
    out: List[str] = []
    root = cfg.project_root

    disabled_at = claude_code_env.auto_memory_disabled(root)
    gates_on = cfg.stop_lessons_enabled or cfg.task_close_lesson_gate
    if disabled_at and gates_on:
        out.append(msg(cfg, "self_check.auto_memory_off", scope=disabled_at))

    explicit = claude_code_env.configured_auto_memory_dir(root)
    if explicit:
        if not claude_code_env.same_dir(cfg.memory_dir, explicit[0]):
            out.append(msg(cfg, "self_check.memory_dir_mismatch",
                           memory_dir=cfg.memory_dir, auto_dir=explicit[0], scope=explicit[1]))
        return out

    own_count = _own_lesson_count(cfg)
    has_lessons = own_count > 0
    if has_lessons and not (gates_on and not disabled_at):
        return out            # намеренный корпус законен — но только без стражей, см. (4)

    cheap = claude_code_env.default_auto_memory_dir_without_git(root)
    if cheap and claude_code_env.same_dir(cfg.memory_dir, cheap):
        return out            # ← отсекатель git: точный ответ «расхождения нет» за один stat

    confirmed = claude_code_env.existing_auto_memory_dir(root)
    if confirmed and not claude_code_env.same_dir(cfg.memory_dir, confirmed):
        if has_lessons:
            # own_count, а не count: у соседней жалобы (3) `count` — уроки в ЧУЖОЙ папке.
            # Один плейсхолдер с двумя смыслами — ловушка для переводчика, а имена
            # плейсхолдеров после релиза заморожены правилом «override ⊆ дефолта».
            out.append(msg(cfg, "self_check.memory_dir_divergent",
                           memory_dir=cfg.memory_dir, auto_dir=confirmed,
                           own_count=own_count))
        else:
            out.append(msg(cfg, "self_check.memory_dir_empty_elsewhere",
                           memory_dir=cfg.memory_dir, auto_dir=confirmed,
                           count=len(lesson_paths(replace(cfg, memory_dir=confirmed)))))
    return out


def _own_lesson_count(cfg: MemoryConfig) -> int:
    """Сколько уроков движок видит в СВОЁМ memory_dir (без чтения содержимого).

    Было `_has_lessons() -> bool`. Стало число, потому что у `settings_issues` теперь два
    потребителя одного факта: выбор между жалобами (3) и (4) и само число внутри (4).
    Держать их раздельно значило бы дважды сходить в файловую систему и — хуже — оставить
    второй вызов БЕЗ `except`: `lesson_paths` бросает OSError, и жалоба про сломанную
    настройку роняла бы хук. Один безопасный подсчёт закрывает оба вопроса.

    0 при OSError, а не «считаем, что уроки есть»: не сумев посмотреть, честнее сказать
    «своих уроков не видно» — тогда (4) не соврёт числом, которого не знает, а разъезд всё
    равно будет назван формулировкой (3). Путь почти недостижим: `glob` на нечитаемом
    каталоге не бросает, а возвращает пусто."""
    try:
        return len(lesson_paths(cfg))
    except OSError:
        return 0


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
    # После bad_regex намеренно: у битого шаблона lag-проверка молчит (0 узнанных форм),
    # поэтому на один дефект приходится ровно одна жалоба, и первой идёт более грубая.
    out += [msg(cfg, "self_check.close_pattern_lag", field=f,
                missing=", ".join(m), example=f"{m[0]} #42")
            for f, m in close_pattern_lag_issues(cfg)]
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
