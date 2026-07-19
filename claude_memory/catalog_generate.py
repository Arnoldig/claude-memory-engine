"""Машинная сборка указателя CATALOG из frontmatter уроков.

Зачем: указатель, который ведут вручную, «течёт» — теряет файлы, копит рассинхрон с
реальным набором уроков. Этот модуль строит индексную часть указателя детерминированно
из самих файлов: поле `topic:` во frontmatter решает раздел, строка — из `description:`.

Принцип сосуществования рукописного и машинного: рукописная преамбула (шапка) и любой
рукописный хвост СОХРАНЯЮТСЯ между пересборками — заменяется только блок между маркерами
AUTO-INDEX. Так владелец правит прозу руками, а список уроков всегда полон и не дрейфует.

Все проектные значения (каталог памяти, таксономия тем, имена особых файлов, пороги) —
из конфига. Парсинг frontmatter — регэкспами, без PyYAML (локальный `pytest`).
"""
from __future__ import annotations

import datetime
import os
import re
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from .applies_to import strip_scalar
from .config import MemoryConfig, get_config
from .lesson_files import lesson_paths
from .messages import msg

# Маркеры авто-блока по умолчанию (всё ВНЕ их — рукописное). Реальные значения берутся
# из cfg.catalog_auto_start/end (конфигурируемы под язык/конвенцию проекта); эти
# константы — дефолты конфига и опорные значения для тестов.
AUTO_START = "<!-- AUTO-INDEX:START — managed by catalog_generate; edits between markers are overwritten -->"
AUTO_END = "<!-- AUTO-INDEX:END -->"
# Имя файла-маркера троттлинга пульса здоровья (внутренний, в memory_dir).
HEALTH_MARKER_NAME = "_catalog_health_marker"

# Раздел для уроков без распознанной темы — всегда последним (сигнал «припиши topic:»).
NO_TOPIC_KEY = "_none"
# Сколько РАЗЛИЧНЫХ незаведённых слагов называть в пульсе (остальные — многоточием).
_PULSE_TOPICS_SHOWN = 5

_MD_LINK_RE = re.compile(r"\]\((?!https?:)([^)]+?\.md)(?:#[^)]*)?\)")
# Inline-примеры формата ссылки в уроках о самой памяти — не реальные цели.
_PLACEHOLDER_TARGETS = frozenset({"файл.md", "file.md"})
_SUBLABEL_RE = re.compile(r"^\s*-\s+\*\*(.+?)\*\*")
_WIKILINK_RE = re.compile(r"\[\[([^\]\[]+)\]\]")
# Форма ссылки-на-урок внутри `[[…]]`: составной слаг, опц. с `.md`. Требования:
#   • без пробелов/слэшей/пунктуации — иначе это проза, а не ссылка;
#   • ОБЯЗАТЕЛЬНО есть `-` или `_` — слаг урока по построению составной
#     (`short-kebab-case-slug`, `feedback_x`), а односложное `[[wiki]]`/`[[x]]`/`[[ссылки]]`
#     — почти всегда проза или пример. Замер на живых корпусах (514 уроков): односложных
#     слагов-уроков нет НИ ОДНОГО, а односложных `[[…]]`-вставок в прозе — 4 из 18
#     находок. Требование дефиса отсекает ровно их, не теряя ни одной настоящей ссылки.
# `\w` в Python — Unicode: кириллические слаги распознаются наравне с латинскими.
_LESSON_LINK_RE = re.compile(r"[\w-]*[-_][\w-]*(?:\.md)?")


class Lesson(NamedTuple):
    filename: str
    name: str
    description: str
    doc_type: str
    topic: str  # "" если не задан
    subtopic: str  # "" если не задан
    reverify_after: str  # "" если не задан
    size: int
    has_frontmatter: bool


def parse_frontmatter(text: str) -> Dict[str, str]:
    """Достаёт скалярные поля frontmatter регэкспом.

    Поддерживает И top-level `type:`, И вложенный `metadata:\\n  type:`. Возвращает
    плоский dict только нужных полей.
    """
    out: Dict[str, str] = {}
    if not text.startswith("---"):
        return out
    fm = text.split("\n---", 1)[0]
    # После двоеточия — ТОЛЬКО горизонтальный пробел `[ \t]*`, НЕ `\s*`: `\s` матчит и
    # перенос строки, поэтому у ПУСТОГО значения (`name:` / `name: ""` без текста) `\s*`
    # съедал `\n` и `(.*)` хватал СЛЕДУЮЩУЮ строку frontmatter как значение (empty `name:`
    # → «topic: …»). `[ \t]*` останавливается на конце строки → пустое поле = "".
    for key in ("name", "description"):
        m = re.search(rf"^{key}:[ \t]*(.*)$", fm, re.MULTILINE)
        if m:
            out[key] = strip_scalar(m.group(1))
    for key in ("topic", "subtopic", "reverify_after", "type"):
        m = re.search(rf"^[ \t]*{key}:[ \t]*(.*)$", fm, re.MULTILINE)
        if m:
            v = strip_scalar(m.group(1))
            if v:
                out[key] = v
    return out


def _lesson_paths(memory_dir: str, cfg: MemoryConfig) -> List[Tuple[str, str]]:
    """Пути всех уроков в корне memory_dir (без подпапок), отсортированы.

    Тонкая обёртка над `lesson_files.lesson_paths` — единым источником истины (0.10.0).
    Возвращает пары (path, basename): форма, удобная здешним потребителям.
    """
    return [(p, os.path.basename(p)) for p in lesson_paths(cfg, memory_dir)]


def collect_lessons(
    memory_dir: str,
    cfg: Optional[MemoryConfig] = None,
    topic_override: Optional[Dict[str, str]] = None,
    subtopic_override: Optional[Dict[str, str]] = None,
) -> List[Lesson]:
    """Читает frontmatter всех уроков в корне memory_dir.

    topic_override / subtopic_override (filename -> значение) перекрывают поля из файла
    — нужно для ПРЕВЬЮ до миграции.
    """
    cfg = cfg or get_config()
    topic_override = topic_override or {}
    subtopic_override = subtopic_override or {}
    lessons: List[Lesson] = []
    for path, base in _lesson_paths(memory_dir, cfg):
        raw = Path(path).read_text(encoding="utf-8")
        fm = parse_frontmatter(raw)
        topic = topic_override.get(base, fm.get("topic", ""))
        subtopic = subtopic_override.get(base, fm.get("subtopic", ""))
        lessons.append(
            Lesson(
                filename=base,
                name=fm.get("name", ""),
                description=fm.get("description", ""),
                doc_type=fm.get("type", ""),
                topic=topic,
                subtopic=subtopic,
                reverify_after=fm.get("reverify_after", ""),
                size=len(raw.encode("utf-8")),
                has_frontmatter=raw.startswith("---"),
            )
        )
    return lessons


def _line(lesson: Lesson, cfg: MemoryConfig, notes: Optional[Dict[str, str]] = None) -> str:
    """Одна строка указателя: описание-якорь → ссылка на файл (+ приписка, если есть)."""
    label = lesson.description or lesson.name or lesson.filename
    label = re.sub(r"\s+", " ", label).strip()
    if len(label) > cfg.desc_max:
        label = label[: cfg.desc_max - 1].rstrip() + "…"
    label = label.replace("]", "⟧")  # не сломать markdown-ссылку
    return f"- [{label}]({lesson.filename}){(notes or {}).get(lesson.filename, '')}"


def _render_group(
    parts: List[str],
    group: List[Lesson],
    cfg: MemoryConfig,
    notes: Optional[Dict[str, str]] = None,
) -> None:
    """Уроки темы: сперва без под-группы (плоско), затем по под-группам (subtopic).

    notes (filename → приписка) дописывается к строке. Нужен ⚠-разделу: там лежат две
    разные беды, и приписка называет ту, которую по заголовку раздела не угадать.
    """
    flat = sorted((x for x in group if not x.subtopic), key=lambda x: x.filename)
    parts.extend(_line(ls, cfg, notes) for ls in flat)
    subs: Dict[str, List[Lesson]] = {}
    for ls in group:
        if ls.subtopic:
            subs.setdefault(ls.subtopic, []).append(ls)
    for sub in sorted(subs):
        parts.append(f"- **{sub}:**")
        for ls in sorted(subs[sub], key=lambda x: x.filename):
            parts.append("  " + _line(ls, cfg, notes))


def render_index(lessons: List[Lesson], cfg: Optional[MemoryConfig] = None) -> str:
    """Машинный индекс: уроки по теме (cfg.topic_order), внутри — по под-группам."""
    cfg = cfg or get_config()
    titles = cfg.topic_titles()
    by_topic: Dict[str, List[Lesson]] = {}
    for ls in lessons:
        key = ls.topic if ls.topic in titles else NO_TOPIC_KEY
        by_topic.setdefault(key, []).append(ls)

    parts: List[str] = []
    for key, title in cfg.topic_order:
        group = by_topic.get(key)
        if not group:
            continue
        parts.append(f"### {title}")
        _render_group(parts, group, cfg)
        parts.append("")

    no_topic = by_topic.get(NO_TOPIC_KEY)
    if no_topic:
        parts.append(f"### {cfg.no_topic_title}")
        # Заголовок раздела говорит «добавь topic:» — для урока, где тема НАПИСАНА, это
        # неверный совет. Приписка называет увиденное значение, чтобы человек сразу видел
        # разницу между опечаткой в слаге и незаведённой в конфиге темой.
        notes = {
            ls.filename: msg(cfg, "catalog.unknown_topic_note", topic=str(ls.topic))
            for ls in no_topic if ls.topic
        }
        _render_group(parts, no_topic, cfg, notes)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def find_broken_links(memory_dir: str, cfg: Optional[MemoryConfig] = None) -> List[Tuple[str, str]]:
    """Битые перекрёстные ссылки МЕЖДУ уроками памяти (голым именем `](feedback_x.md)`).

    Ссылки с `/` (пути в репозиторий) и плейсхолдеры-примеры — вне зоны этой проверки.
    """
    cfg = cfg or get_config()
    broken: List[Tuple[str, str]] = []
    root = Path(memory_dir)
    for path, base in _lesson_paths(memory_dir, cfg):
        text = Path(path).read_text(encoding="utf-8")
        for m in _MD_LINK_RE.finditer(text):
            target = m.group(1)
            if "/" in target:
                continue
            if target in _PLACEHOLDER_TARGETS:
                continue
            if (root / target).exists():
                continue
            broken.append((base, target))
    return sorted(set(broken))


def find_broken_wikilinks(memory_dir: str, cfg: Optional[MemoryConfig] = None) -> List[Tuple[str, str]]:
    """Битые `[[X]]`-ссылки между уроками (вторая конвенция связывания помимо `](x.md)`).

    Цель валидна, если есть файл `X.md` ИЛИ урок с `name: X`.

    Что считаем ссылкой-на-урок. До 0.10.0 — «цель начинается с приставки урока», и это
    была та же болезнь, что у стража: ссылки между уроками БЕЗ приставки не проверялись
    вообще (на живом корпусе — все связи kebab-уроков молча, оборвись они, никто бы не
    узнал). Теперь признак — ФОРМА цели: слаг-подобный токен (`[\\w-]+`, опц. `.md`), без
    пробелов и путей. Это ровно та конвенция, которой связывает уроки авто-память
    Claude Code (`[[their-name]]`), и она не зависит от имени файла.

    Произвольные `[[...]]` с пробелами/путями/пунктуацией не трогаем — прозу и примеры
    не ловим. Диагностика report-only (идёт в пульс здоровья), поэтому цена редкого
    ложного срабатывания на односложной прозе — строка в пульсе, а не блок.
    """
    cfg = cfg or get_config()
    texts: Dict[str, str] = {}
    valid: set = set()
    for path, base in _lesson_paths(memory_dir, cfg):
        t = Path(path).read_text(encoding="utf-8")
        texts[base] = t
        valid.add(base[:-3] if base.endswith(".md") else base)
        nm = parse_frontmatter(t).get("name")
        if nm:
            valid.add(nm)
    broken: List[Tuple[str, str]] = []
    for base, t in texts.items():
        for m in _WIKILINK_RE.finditer(t):
            target = m.group(1).strip()
            if not _LESSON_LINK_RE.fullmatch(target):
                continue
            # обе конвенции записи цели: `[[feedback_x]]` и `[[feedback_x.md]]` — норму
            # (без .md) сверяем с набором {имя_файла_без_.md} ∪ {name-слаги}.
            norm = target[:-3] if target.endswith(".md") else target
            if norm not in valid and target not in valid:
                broken.append((base, target))
    return sorted(set(broken))


def run_diagnostics(
    memory_dir: str, lessons: List[Lesson], cfg: Optional[MemoryConfig] = None
) -> Dict[str, list]:
    """Сводка здоровья: сироты-без-темы, без описания/frontmatter, крупные, битые ссылки."""
    cfg = cfg or get_config()
    titles = cfg.topic_titles()
    # Два РАЗНЫХ дефекта, и чинятся они в разных местах: «поля нет» — дописать `topic:` в
    # УРОК; «значение не заведено» — либо опечатка в слаге урока, либо тема настоящая, но
    # её нет в КОНФИГЕ. До #14 оба лежали в одной куче под заголовком «добавь topic:», то
    # есть про вторую половину движок утверждал обратное факту. Наборы намеренно
    # непересекающиеся: иначе один урок считался бы дважды и цифры в пульсе врали бы.
    no_topic = sorted(ls.filename for ls in lessons if not ls.topic)
    unknown_topic = sorted(
        (ls.filename, str(ls.topic)) for ls in lessons
        if ls.topic and ls.topic not in titles
    )
    no_desc = sorted(ls.filename for ls in lessons if not ls.description)
    # Пустой `name` без keywords обнуляет высоковесный (×2) набор токенов заголовка —
    # урок труднее «всплывает» в retrieve. Частый источник — нормализация frontmatter
    # инструментом редактирования (обнуляет name); чинится восстановлением заголовка.
    no_name = sorted(ls.filename for ls in lessons if not ls.name)
    no_fm = sorted(ls.filename for ls in lessons if not ls.has_frontmatter)
    oversize = sorted(
        (ls.filename, ls.size) for ls in lessons if ls.size > cfg.oversize_bytes
    )
    return {
        "total": [len(lessons)],
        "no_topic": no_topic,
        "unknown_topic": unknown_topic,
        "no_description": no_desc,
        "no_name": no_name,
        "no_frontmatter": no_fm,
        "oversize": oversize,
        "broken_links": find_broken_links(memory_dir, cfg),
        "broken_wikilinks": find_broken_wikilinks(memory_dir, cfg),
    }


def _split_preamble_footer(existing: str, cfg: MemoryConfig) -> Tuple[str, str]:
    """Делит существующий указатель на рукописную преамбулу (до маркера) и хвост (после).

    Маркеры берём из cfg (проект может задать свои/локализованные) — так первая
    пересборка узнаёт существующий файл с уже стоящими маркерами и не плодит дубль.
    """
    start, end = cfg.catalog_auto_start, cfg.catalog_auto_end
    if start in existing and end in existing:
        pre = existing.split(start, 1)[0].rstrip()
        post = existing.split(end, 1)[1].lstrip("\n")
        return pre, post
    head = existing.split("\n### ", 1)[0].rstrip() if existing else ""
    return head, ""


def build_catalog(
    memory_dir: Optional[str] = None,
    cfg: Optional[MemoryConfig] = None,
    topic_override: Optional[Dict[str, str]] = None,
    subtopic_override: Optional[Dict[str, str]] = None,
    today: Optional[datetime.date] = None,
) -> Tuple[str, Dict[str, list]]:
    """Собирает полный текст указателя (преамбула + машинный индекс) и диагностику."""
    cfg = cfg or get_config()
    memory_dir = memory_dir or cfg.memory_dir
    if today is None:
        today = datetime.date.today()
    lessons = collect_lessons(memory_dir, cfg, topic_override, subtopic_override)
    index = render_index(lessons, cfg)
    diag = run_diagnostics(memory_dir, lessons, cfg)

    catalog_path = os.path.join(memory_dir, cfg.catalog_file)
    existing = ""
    if os.path.exists(catalog_path):
        existing = Path(catalog_path).read_text(encoding="utf-8")
    preamble, footer = _split_preamble_footer(existing, cfg)
    if not preamble:
        preamble = cfg.catalog_preamble

    note = msg(cfg, "catalog.auto_index_note", today=today.isoformat(), count=len(lessons))
    body = "\n".join(
        [preamble, "", cfg.catalog_auto_start, note, "", index.rstrip(), "", cfg.catalog_auto_end]
    )
    if footer:
        body += "\n\n" + footer
    return body.rstrip() + "\n", diag


def _section_to_topic(cfg: MemoryConfig) -> Dict[str, str]:
    """Обратная карта «заголовок раздела → слаг темы» из cfg.topic_order (для бутстрапа)."""
    return {title: slug for slug, title in cfg.topic_order}


def bootstrap_topics_from_catalog(
    memory_dir: str, cfg: Optional[MemoryConfig] = None
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Строит ({filename: topic}, {filename: subtopic}) из текущего указателя (read-only).

    Нужно только для ПРЕВЬЮ до миграции: показать честный вид будущего указателя до
    появления полей `topic:`/`subtopic:` в файлах. Файлы из ядра, не попавшие в раздел
    указателя, получают topic=core (если такой слаг есть в таксономии).
    """
    cfg = cfg or get_config()
    section_to_topic = _section_to_topic(cfg)
    has_core = "core" in {slug for slug, _ in cfg.topic_order}
    catalog_path = os.path.join(memory_dir, cfg.catalog_file)
    topics: Dict[str, str] = {}
    subtopics: Dict[str, str] = {}
    if not os.path.exists(catalog_path):
        return topics, subtopics
    section = None
    for line in Path(catalog_path).read_text(encoding="utf-8").splitlines():
        h = re.match(r"^#{2,3}\s+(.*)$", line)
        if h:
            section = h.group(1).strip()
            continue
        topic = section_to_topic.get(section or "", "")
        if not topic:
            continue
        sm = _SUBLABEL_RE.match(line)
        sub = sm.group(1).strip().rstrip(":").strip() if sm else ""
        for m in _MD_LINK_RE.finditer(line):
            tgt = m.group(1)
            if "/" not in tgt:
                topics.setdefault(tgt, topic)
                if sub:
                    subtopics.setdefault(tgt, sub)
    if has_core:
        core_path = os.path.join(memory_dir, cfg.core_file)
        if os.path.exists(core_path):
            for m in _MD_LINK_RE.finditer(Path(core_path).read_text(encoding="utf-8")):
                tgt = m.group(1)
                if "/" not in tgt:
                    topics.setdefault(tgt, "core")
    return topics, subtopics


def set_frontmatter_field(text: str, key: str, value: str) -> Tuple[str, bool]:
    """Вписывает/обновляет `key: value` во frontmatter. Идемпотентно. Нет frontmatter
    → возврат без изменений (False). Новое поле — после `description:`/`name:`, top-level."""
    if not text.startswith("---"):
        return text, False
    lines = text.split("\n")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return text, False
    fm = lines[1:end]
    key_re = re.compile(rf"^{re.escape(key)}:\s*(.*)$")
    for i, ln in enumerate(fm):
        m = key_re.match(ln)
        if m:
            if strip_scalar(m.group(1)) == value:
                return text, False
            fm[i] = f"{key}: {value}"
            return "\n".join(lines[:1] + fm + lines[end:]), True
    insert_at = len(fm)
    for anchor in ("description:", "name:"):
        for i, ln in enumerate(fm):
            if ln.startswith(anchor):
                insert_at = i + 1
                break
        else:
            continue
        break
    fm.insert(insert_at, f"{key}: {value}")
    return "\n".join(lines[:1] + fm + lines[end:]), True


def migrate_frontmatter(
    memory_dir: str,
    topics: Dict[str, str],
    subtopics: Dict[str, str],
    apply: bool = False,
    cfg: Optional[MemoryConfig] = None,
) -> Dict[str, list]:
    """Вписывает topic/subtopic во frontmatter всех уроков с известной темой.

    apply=False — сухой прогон (только отчёт). apply=True — атомарная запись каждого
    изменённого файла (tempfile + os.replace).
    """
    cfg = cfg or get_config()
    changed: List[str] = []
    skipped_no_topic: List[str] = []
    skipped_no_fm: List[str] = []
    for path, base in _lesson_paths(memory_dir, cfg):
        topic = topics.get(base)
        if not topic:
            skipped_no_topic.append(base)
            continue
        text = Path(path).read_text(encoding="utf-8")
        if not text.startswith("---"):
            skipped_no_fm.append(base)
            continue
        cur = parse_frontmatter(text)
        sub = subtopics.get(base)
        if cur.get("topic") == topic and (not sub or cur.get("subtopic") == sub):
            continue
        new, c1 = (
            set_frontmatter_field(text, "topic", topic)
            if cur.get("topic") != topic
            else (text, False)
        )
        c2 = False
        if sub and cur.get("subtopic") != sub:
            new, c2 = set_frontmatter_field(new, "subtopic", sub)
        if c1 or c2:
            changed.append(base)
            if apply:
                tmp = Path(path).with_name(base + ".tmp")
                tmp.write_text(new, encoding="utf-8")
                os.replace(tmp, path)
    return {
        "changed": changed,
        "skipped_no_topic": skipped_no_topic,
        "skipped_no_fm": skipped_no_fm,
    }


def format_health_pulse(diag: Dict[str, list], cfg: Optional[MemoryConfig] = None) -> str:
    """Компактная сводка здоровья для SessionStart. Пусто, если нет actionable-долга."""
    cfg = cfg or get_config()
    nt = len(diag["no_topic"])
    unknown = diag.get("unknown_topic", [])
    ut = len(unknown)
    nn = len(diag.get("no_name", []))
    bl = len(diag["broken_links"])
    wbl = len(diag.get("broken_wikilinks", []))
    osz = len(diag["oversize"])
    total = diag["total"][0] if diag.get("total") else 0
    many = bool(cfg.lesson_count_warn) and total >= cfg.lesson_count_warn
    if nt == 0 and ut == 0 and nn == 0 and bl == 0 and wbl == 0 and not many:
        return ""
    parts = []
    if nt:
        parts.append(msg(cfg, "health.no_topic", nt=nt))
    if ut:
        # Сами слаги — в пульсе, а не только счётчик: причина должна быть ясна с первой
        # строки, без раскопок по файлам. Список различных значений короток по природе
        # (тем в проекте единицы), но всё же ограничен — пульс обязан оставаться одной
        # строкой, иначе его перестают читать.
        seen = sorted({t for _, t in unknown})
        shown = ", ".join(seen[:_PULSE_TOPICS_SHOWN])
        if len(seen) > _PULSE_TOPICS_SHOWN:
            shown += ", …"
        parts.append(msg(cfg, "health.unknown_topic", ut=ut, topics=shown))
    if nn:
        parts.append(msg(cfg, "health.no_name", nn=nn))
    if bl:
        parts.append(msg(cfg, "health.broken_links", bl=bl))
    if wbl:
        parts.append(msg(cfg, "health.broken_wikilinks", wbl=wbl))
    if many:
        parts.append(msg(cfg, "health.many_lessons", total=total, limit=cfg.lesson_count_warn))
    if osz:
        parts.append(msg(cfg, "health.oversize", osz=osz, oversize_kb=cfg.oversize_bytes // 1000))
    return msg(cfg, "health.pulse_prefix") + "; ".join(parts) + msg(cfg, "health.pulse_suffix")


def _pulse_signature(diag: Dict[str, list], cfg: MemoryConfig) -> str:
    """Подпись «долга» для троттлинга: пульс звучит заново, когда она изменилась.

    Неизвестные темы входят в подпись СОСТАВОМ (`ut`), а не только числом: исправление
    опечатки в слаге на верный оставляет счётчик прежним, если рядом появился ещё один
    неизвестный, — и пульс промолчал бы про новую беду до конца суток. Ровно тот
    молчаливый отказ, который эта заявка и чинит, только внесённый самой починкой.
    """
    _total = diag["total"][0] if diag.get("total") else 0
    _many = 1 if (cfg.lesson_count_warn and _total >= cfg.lesson_count_warn) else 0
    _unknown = ",".join(sorted({t for _, t in diag.get("unknown_topic", [])}))
    return (
        f"nt{len(diag['no_topic'])}_nn{len(diag.get('no_name', []))}"
        f"_bl{len(diag['broken_links'])}"
        f"_wbl{len(diag.get('broken_wikilinks', []))}_many{_many}"
        f"_ut{len(diag.get('unknown_topic', []))}:{_unknown}"
    )


def health_marker_path(cfg: MemoryConfig) -> str:
    """Путь файла-маркера троттлинга пульса (внутренний `_*` файл в memory_dir)."""
    return os.path.join(cfg.memory_dir, HEALTH_MARKER_NAME)


def throttle_pulse(
    pulse: str,
    diag: Dict[str, list],
    cfg: MemoryConfig,
    today: Optional[datetime.date] = None,
    marker: Optional[str] = None,
) -> str:
    """Троттлинг пульса: вернуть pulse к показу или '' (и записать маркер при показе).

    Правило: не чаще раза в день; показать при смене «долга» (nt/bl) ИЛИ через 7 дней
    при неизменном долге. cfg.health_pulse_throttle=False — отдать pulse как есть.
    Вынесено сюда из main(--report), чтобы SessionStart-хук применял тот же троттлинг.
    """
    if not pulse:
        return ""
    if not cfg.health_pulse_throttle:
        return pulse
    if today is None:
        today = datetime.date.today()
    marker = marker or health_marker_path(cfg)
    sig = _pulse_signature(diag, cfg)
    last_date = last_sig = ""
    try:
        last_date, last_sig = (
            Path(marker).read_text(encoding="utf-8").strip().split("|", 1) + [""]
        )[:2]
    except OSError:
        pass
    if last_date == today.isoformat():
        return ""
    emit = (not last_date) or (sig != last_sig)
    if not emit:
        try:
            emit = (today - datetime.date.fromisoformat(last_date)).days >= 7
        except ValueError:
            emit = True
    if not emit:
        return ""
    try:
        tmp = Path(marker).with_name(Path(marker).name + ".tmp")
        tmp.write_text(f"{today.isoformat()}|{sig}", encoding="utf-8")
        os.replace(tmp, Path(marker))
    except OSError:
        pass
    return pulse


def print_diagnostics(diag: Dict[str, list], cfg: MemoryConfig, stream) -> None:
    """Подробная диагностика указателя для человека (CLI). Вынесено из main() ради теста:
    жалоба может быть верно посчитана и не напечатана — тогда до человека она не дойдёт."""
    print("\n" + msg(cfg, "diag.separator"), file=stream)
    print(msg(cfg, "diag.header"), file=stream)
    print(msg(cfg, "diag.total", count=diag["total"][0]), file=stream)
    print(msg(cfg, "diag.no_topic_count", count=len(diag["no_topic"])), file=stream)
    for f in diag["no_topic"]:
        print(msg(cfg, "diag.no_topic_item", f=f), file=stream)
    unknown = diag.get("unknown_topic", [])
    print(msg(cfg, "diag.unknown_topic_count", count=len(unknown)), file=stream)
    for f, topic in unknown:
        print(msg(cfg, "diag.unknown_topic_item", f=f, topic=topic), file=stream)
    print(msg(cfg, "diag.no_description", count=len(diag["no_description"])), file=stream)
    print(msg(cfg, "diag.no_name", count=len(diag.get("no_name", []))), file=stream)
    for f in diag.get("no_name", []):
        print(msg(cfg, "diag.no_name_item", f=f), file=stream)
    print(msg(cfg, "diag.no_frontmatter", count=len(diag["no_frontmatter"])), file=stream)
    print(msg(cfg, "diag.oversize_count", oversize_bytes=cfg.oversize_bytes,
              count=len(diag["oversize"])), file=stream)
    print(msg(cfg, "diag.broken_links_count", count=len(diag["broken_links"])), file=stream)
    for src, tgt in diag["broken_links"]:
        print(msg(cfg, "diag.broken_link_item", src=src, tgt=tgt), file=stream)


def main() -> None:
    import sys

    cfg = get_config()
    args = sys.argv[1:]
    memory_dir = cfg.memory_dir

    if "--report" in args:
        idx = args.index("--report")
        marker = (
            args[idx + 1]
            if idx + 1 < len(args)
            else health_marker_path(cfg)
        )
        diag = run_diagnostics(memory_dir, collect_lessons(memory_dir, cfg), cfg)
        pulse = throttle_pulse(format_health_pulse(diag, cfg), diag, cfg, marker=marker)
        if pulse:
            print(pulse)
        return

    write = "--write" in args
    flat = "--flat" in args
    use_bootstrap = "--bootstrap" in args
    topic_override = subtopic_override = None
    if use_bootstrap:
        topic_override, subtopic_override = bootstrap_topics_from_catalog(memory_dir, cfg)
        if flat:
            subtopic_override = None

    catalog_text, diag = build_catalog(
        memory_dir, cfg, topic_override=topic_override, subtopic_override=subtopic_override
    )

    if write:
        cat_path = Path(os.path.join(memory_dir, cfg.catalog_file))
        tmp = cat_path.with_name(cfg.catalog_file + ".tmp")
        tmp.write_text(catalog_text, encoding="utf-8")
        os.replace(tmp, cat_path)
        print(msg(cfg, "catalog.written", catalog_file=cfg.catalog_file, count=diag["total"][0]))
    else:
        print(catalog_text)

    print_diagnostics(diag, cfg, stream=sys.stderr)


if __name__ == "__main__":
    main()
