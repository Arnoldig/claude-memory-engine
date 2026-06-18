"""Адресуемый индекс архива прецедентов.

Архив `archive/precedents-YYYY-QN.md` растёт append-only и к концу квартала огромен
(сотни КБ ≈ десятки-сотни тысяч токенов) — читать его ЦЕЛИКОМ нельзя (забьёт рабочую
память). Этот модуль делает архив АДРЕСУЕМЫМ:

- `build_index` / `parse_cards` — из заголовков карточек `## YYYY-MM-DD (тема)` +
  ссылок на уроки в теле строит компактный индекс (дата · тема · порождённые уроки).
- `extract_card` — возвращает ОДНУ карточку по дате/подстроке заголовка.
- main(): `--index <archive> [--write]`, `--extract <archive> <запрос>`,
  `--add-header <archive>`.

Сам скрипт читает большой файл в СВОЮ память; вызывающий агент видит только маленький
вывод. Префиксы файлов-уроков (feedback/reference/project) — из конфига.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, NamedTuple, Optional

from .config import MemoryConfig, get_config
from .messages import msg

CARD_HEAD_RE = re.compile(r"^## (.+)$", re.MULTILINE)
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _extract_cmd() -> str:
    """Команда module-формы для извлечения одной карточки (передаётся в сообщения как параметр)."""
    return "python3 -m claude_memory.precedent_index --extract <archive> <date|substring>"


def _ref_re(cfg: MemoryConfig) -> "re.Pattern[str]":
    """Регэксп ссылок на файлы-уроки по сконфигурированным префиксам (feedback_/reference_/…)."""
    alt = "|".join(re.escape(p) for p in cfg.lesson_prefixes)
    return re.compile(rf"\b((?:{alt})_[\w-]+\.md)\b")


class Card(NamedTuple):
    date: str          # YYYY-MM-DD ("" если в заголовке нет даты)
    title: str         # полный текст заголовка после "## "
    refs: List[str]    # порождённые/упомянутые уроки


def parse_cards(text: str, cfg: Optional[MemoryConfig] = None) -> List[Card]:
    """Карточки архива: заголовок `## …` + тело до следующего `## …`.

    Для каждой — дата (из заголовка), заголовок, уникальные ссылки на уроки в теле.
    """
    cfg = cfg or get_config()
    ref_re = _ref_re(cfg)
    heads = list(CARD_HEAD_RE.finditer(text))
    cards: List[Card] = []
    for i, m in enumerate(heads):
        title = m.group(1).strip()
        body_start = m.end()
        body_end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[body_start:body_end]
        dm = DATE_RE.search(title)
        date = dm.group(1) if dm else ""
        seen = set()
        refs: List[str] = []
        for r in ref_re.findall(title + "\n" + body):
            if r not in seen:
                seen.add(r)
                refs.append(r)
        cards.append(Card(date=date, title=title, refs=refs))
    return cards


def render_index(cards: List[Card], archive_name: str, cfg: Optional[MemoryConfig] = None) -> str:
    """Компактный markdown-индекс: одна строка на карточку (дата · тема · уроки)."""
    cfg = cfg or get_config()
    lines = [
        msg(cfg, "precedent.index_title", archive_name=archive_name),
        "",
        msg(
            cfg,
            "precedent.index_preamble",
            archive_name=archive_name,
            card_count=len(cards),
            extract_cmd=_extract_cmd(),
        ),
        "",
    ]
    for c in cards:
        theme = c.title
        if c.date and theme.startswith(c.date):
            theme = theme[len(c.date):].lstrip(" (—-").rstrip(")")
        refs = (" → " + ", ".join(c.refs)) if c.refs else ""
        date = c.date or "????-??-??"
        lines.append(f"- **{date}** {theme}{refs}")
    return "\n".join(lines).rstrip() + "\n"


def extract_card(text: str, query: str) -> str:
    """Текст ОДНОЙ карточки, чей заголовок содержит query (дата или подстрока).

    Несколько совпадений — все подходящие (разделены пустой строкой). Нет — пустая строка.
    Пустой/пробельный query → пустая строка (а НЕ весь архив: иначе один промах оператора
    «--extract без запроса» вывалил бы сотни КБ в контекст — ровно то, ради чего модуль).
    """
    if not query.strip():
        return ""
    heads = list(CARD_HEAD_RE.finditer(text))
    out: List[str] = []
    for i, m in enumerate(heads):
        if query.lower() in m.group(1).lower():
            start = m.start()
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            out.append(text[start:end].rstrip())
    return "\n\n".join(out)


def _index_path(archive_path: str) -> str:
    p = Path(archive_path)
    return str(p.with_name(p.stem + "-INDEX.md"))


def add_warning_header(text: str, cfg: Optional[MemoryConfig] = None) -> str:
    """Вписывает предупреждение «не читать целиком» после заголовка-`#`. Идемпотентно."""
    cfg = cfg or get_config()
    warn_header = msg(cfg, "precedent.warn_header", extract_cmd=_extract_cmd())
    # Идемпотентность: первая непустая строка шапки не содержит плейсхолдеров — по её
    # наличию надёжно распознаём уже вписанную шапку (на любом языке/override).
    marker_line = next((ln for ln in warn_header.split("\n") if ln.strip()), "")
    if marker_line and marker_line in text:
        return text  # уже есть
    lines = text.split("\n")
    insert_at = 0
    for i, ln in enumerate(lines):
        if ln.startswith("# "):
            insert_at = i + 1
            break
    if insert_at < len(lines) and lines[insert_at].strip() == "":
        insert_at += 1
    block = ["", warn_header.rstrip(), ""]
    return "\n".join(lines[:insert_at] + block + lines[insert_at:])


def main() -> None:
    import sys

    cfg = get_config()
    args = sys.argv[1:]
    if not args:
        print(msg(cfg, "precedent.cli_usage"))
        return

    if "--extract" in args:
        i = args.index("--extract")
        if i + 1 >= len(args):
            print(msg(cfg, "precedent.cli_usage"))
            return
        archive = args[i + 1]
        query = args[i + 2] if i + 2 < len(args) else ""
        text = Path(archive).read_text(encoding="utf-8")
        card = extract_card(text, query)
        print(card if card else msg(cfg, "precedent.extract_not_found", query=query))
        return

    if "--add-header" in args:
        i = args.index("--add-header")
        if i + 1 >= len(args):
            print(msg(cfg, "precedent.cli_usage"))
            return
        archive = args[i + 1]
        p = Path(archive)
        new = add_warning_header(p.read_text(encoding="utf-8"), cfg)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, p)
        print(msg(cfg, "precedent.header_written", filename=p.name))
        return

    if "--index" in args:
        i = args.index("--index")
        if i + 1 >= len(args):
            print(msg(cfg, "precedent.cli_usage"))
            return
        archive = args[i + 1]
        text = Path(archive).read_text(encoding="utf-8")
        cards = parse_cards(text, cfg)
        idx = render_index(cards, Path(archive).name, cfg)
        if "--write" in args:
            out = _index_path(archive)
            tmp = Path(out).with_name(Path(out).name + ".tmp")
            tmp.write_text(idx, encoding="utf-8")
            os.replace(tmp, Path(out))
            print(msg(cfg, "precedent.index_written", filename=Path(out).name, card_count=len(cards)))
        else:
            print(idx)
        return

    print(msg(cfg, "precedent.unknown_mode"))


if __name__ == "__main__":
    main()
