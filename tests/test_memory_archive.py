"""Тесты авто-архивации прецедентов и session-маркеров."""
from __future__ import annotations

import datetime
from dataclasses import replace
from pathlib import Path

from claude_memory import memory_archive as MA

TODAY = datetime.date(2026, 6, 17)


def test_old_precedent_archived_recent_kept(cfg) -> None:
    fb = Path(cfg.memory_dir) / "feedback_x.md"
    fb.write_text(
        "**Прецедент 2026-01-01:** старый случай, должен уехать.\n\n"
        "**Прецедент 2026-06-15:** свежий, остаётся.\n",
        encoding="utf-8",
    )
    res = MA.archive_old_precedents(fb, today=TODAY, threshold_days=30, cfg=cfg)
    assert res.archived_count == 1
    text = fb.read_text(encoding="utf-8")
    assert "перенесён в [archive/precedents-2026-Q1.md]" in text   # pointer вместо старого
    assert "свежий, остаётся" in text                              # свежий на месте
    arc = Path(cfg.memory_dir) / "archive" / "precedents-2026-Q1.md"
    assert "старый случай" in arc.read_text(encoding="utf-8")


def test_no_candidates_is_noop(cfg) -> None:
    fb = Path(cfg.memory_dir) / "feedback_x.md"
    fb.write_text("**Прецедент 2026-06-15:** свежий.\n", encoding="utf-8")
    before = fb.read_text(encoding="utf-8")
    res = MA.archive_old_precedents(fb, today=TODAY, threshold_days=30, cfg=cfg)
    assert res.archived_count == 0
    assert fb.read_text(encoding="utf-8") == before


def test_count_real_precedents(cfg) -> None:
    text = ("**Прецедент 2026-06-15:** живой.\n\n"
            "**Прецедент 2026-01-01:** перенесён в [archive/precedents-2026-Q1.md](x).\n")
    assert MA.count_real_precedents(text, cfg) == 1  # один живой, один перенесён


def test_precedent_keyword_configurable(cfg) -> None:
    cfg2 = replace(cfg, precedent_keyword="Precedent", precedent_pointer="moved to")
    fb = Path(cfg.memory_dir) / "feedback_en.md"
    fb.write_text("**Precedent 2026-01-01:** old english card.\n", encoding="utf-8")
    res = MA.archive_old_precedents(fb, today=TODAY, threshold_days=30, cfg=cfg2)
    assert res.archived_count == 1
    assert "moved to [archive/precedents-2026-Q1.md]" in fb.read_text(encoding="utf-8")


def test_old_session_markers_archived(cfg) -> None:
    fb = Path(cfg.memory_dir) / "feedback_session_end_lessons.md"
    fb.write_text(
        "# Session lessons\n\n"
        "<!-- 2026-01-01 abc #old — старый маркер -->\n"
        "<!-- 2026-06-15 def #new — свежий маркер -->\n",
        encoding="utf-8",
    )
    res = MA.archive_old_session_markers(fb, today=TODAY, threshold_days=7)
    assert res.archived_count == 1
    text = fb.read_text(encoding="utf-8")
    assert "свежий маркер" in text and "старый маркер" not in text
    arc = Path(cfg.memory_dir) / "archive" / "session-end-markers-2026-Q1.md"
    assert "старый маркер" in arc.read_text(encoding="utf-8")
