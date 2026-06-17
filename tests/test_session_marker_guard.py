"""Тесты стража формата session-маркеров."""
from __future__ import annotations

from dataclasses import replace

from claude_memory import session_marker_guard as SG

TARGET = "feedback_session_end_lessons.md"


def _input(text: str, fname: str = TARGET) -> dict:
    return {"file_path": f"/memory/{fname}", "content": text}


def test_ok_short_single_line(cfg) -> None:
    assert SG.violation_reason("Write", _input("<!-- 2026-06-17 abc #t — суть -->"), cfg) is None


def test_too_long_blocked(cfg) -> None:
    long = "<!-- 2026-06-17 " + "x" * 250 + " -->"
    r = SG.violation_reason("Write", _input(long), cfg)
    assert r and "знаков" in r


def test_multiline_marker_blocked(cfg) -> None:
    r = SG.violation_reason("Write", _input("<!-- 2026-06-17 начало\nпродолжение -->"), cfg)
    assert r and "несколько строк" in r


def test_wrong_file_ignored(cfg) -> None:
    long = "<!-- 2026-06-17 " + "x" * 250 + " -->"
    assert SG.violation_reason("Write", _input(long, "feedback_other.md"), cfg) is None


def test_marker_limit_configurable(cfg) -> None:
    cfg2 = replace(cfg, marker_limit=30)
    marker = "<!-- 2026-06-17 a bit longer than thirty chars -->"
    assert SG.violation_reason("Write", _input(marker), cfg2) is not None
    assert SG.violation_reason("Write", _input(marker), cfg) is None  # дефолт 200 — ок


def test_edit_uses_new_string(cfg) -> None:
    long = "<!-- 2026-06-17 " + "x" * 250 + " -->"
    assert SG.violation_reason("Edit", {"file_path": f"/m/{TARGET}", "new_string": long}, cfg) is not None
