"""Тесты поиска уроков по пути файла (applies_to-глобы)."""
from __future__ import annotations

from claude_memory import applies_to as A
from conftest import write_lesson


def test_inline_glob_matches(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_chat.md",
                 description="правка чата", applies_to="[app/routers/chat.py, static/js/chat.js]")
    res = A.find_lessons_for_path("app/routers/chat.py", cfg)
    assert res == [("feedback_chat.md", "правка чата")]


def test_yaml_list_glob_matches(cfg) -> None:
    p = cfg.memory_dir + "/feedback_tpl.md"
    with open(p, "w", encoding="utf-8") as f:
        f.write("---\ndescription: шаблоны\nmetadata:\n  applies_to:\n"
                "    - templates/*.html\n    - app/core/pdf.py\n---\nтело\n")
    assert A.find_lessons_for_path("templates/claim.html", cfg) == [("feedback_tpl.md", "шаблоны")]
    assert A.find_lessons_for_path("app/core/pdf.py", cfg) == [("feedback_tpl.md", "шаблоны")]


def test_no_match_returns_empty(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_chat.md",
                 description="чат", applies_to="[app/routers/chat.py]")
    assert A.find_lessons_for_path("app/main.py", cfg) == []


def test_lesson_without_applies_to_ignored(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_x.md", description="без applies_to")
    assert A.find_lessons_for_path("app/x.py", cfg) == []


def test_format_lines(cfg) -> None:
    assert A.format_lines([("a.md", "desc"), ("b.md", "")]) == "- a.md: desc\n- b.md"
