"""Тесты адресуемого индекса архива прецедентов."""
from __future__ import annotations

from dataclasses import replace

from claude_memory import precedent_index as PI

ARCHIVE = (
    "# Прецеденты — 2026 Q2\n\n"
    "## 2026-06-01 (тема А)\n\nразбор → feedback_alpha.md и reference_beta.md\n\n"
    "## 2026-06-10 (тема Б)\n\nбез ссылок на уроки\n"
)


def test_parse_cards_dates_and_refs(cfg) -> None:
    cards = PI.parse_cards(ARCHIVE, cfg)
    assert [c.date for c in cards] == ["2026-06-01", "2026-06-10"]
    assert cards[0].refs == ["feedback_alpha.md", "reference_beta.md"]
    assert cards[1].refs == []


def test_refs_do_not_require_prefix(cfg) -> None:
    """Распознавание ссылок согласовано с lesson_files: приставка — не признак урока,
    поэтому кастомные lesson_prefixes ссылки больше не сужают."""
    cfg2 = replace(cfg, lesson_prefixes=("note",))
    text = "## 2026-06-01 t\n\nnote_x.md и feedback_y.md\n"
    cards = PI.parse_cards(text, cfg2)
    assert cards[0].refs == ["note_x.md", "feedback_y.md"]


def test_refs_see_kebab_lessons_without_prefix(cfg) -> None:
    """Урок без приставки — норма: имена файлам даёт авто-память Claude Code, а не движок.
    До правки распознаватель требовал приставку из lesson_prefixes, и такие уроки были
    невидимы для индекса прецедентов — единственный модуль, не переведённый на lesson_files."""
    text = "## 2026-06-01 t\n\nсм. dead-guard-looks-like-blocking.md и feedback_y.md\n"
    cards = PI.parse_cards(text, cfg)
    assert cards[0].refs == ["dead-guard-looks-like-blocking.md", "feedback_y.md"]


def test_refs_skip_core_catalog_and_private(cfg) -> None:
    text = "## 2026-06-01 t\n\nMEMORY.md, CATALOG.md, _stale_pending.md, real-lesson.md\n"
    cards = PI.parse_cards(text, cfg)
    assert cards[0].refs == ["real-lesson.md"]


def test_extract_card_by_date(cfg) -> None:
    card = PI.extract_card(ARCHIVE, "2026-06-10")
    assert "тема Б" in card and "тема А" not in card


def test_extract_card_no_match(cfg) -> None:
    assert PI.extract_card(ARCHIVE, "1999-01-01") == ""


def test_render_index(cfg) -> None:
    idx = PI.render_index(PI.parse_cards(ARCHIVE, cfg), "precedents-2026-Q2.md")
    assert "**2026-06-01**" in idx and "feedback_alpha.md" in idx


def test_add_warning_header_idempotent(cfg) -> None:
    once = PI.add_warning_header("# Заголовок\n\nтекст\n", cfg)
    assert "DO NOT READ IN FULL" in once  # английский дефолт сообщения
    assert PI.add_warning_header(once, cfg) == once  # второй раз не дублирует
