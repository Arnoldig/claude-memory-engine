"""Тесты скана устаревания памяти (SessionEnd)."""
from __future__ import annotations

import datetime
from pathlib import Path

from claude_memory import staleness as ST
from conftest import write_lesson

TODAY = datetime.date(2026, 6, 17)


def test_scan_finds_expired_reverify(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_old.md", description="просрочено",
                 reverify_after="2026-01-01")
    write_lesson(cfg.memory_dir, "feedback_fresh.md", description="ещё актуально",
                 reverify_after="2099-01-01")
    stale, _ = ST.scan(cfg, today=TODAY)
    names = [n for _, n, _ in stale]
    assert "feedback_old.md" in names and "feedback_fresh.md" not in names


def test_scan_finds_dead_applies_to(cfg) -> None:
    # реальный файл проекта, чтобы repo_files был непустым (иначе проверка пропускается)
    (Path(cfg.project_root) / "app").mkdir()
    (Path(cfg.project_root) / "app" / "live.py").write_text("x", encoding="utf-8")
    write_lesson(cfg.memory_dir, "feedback_live.md", description="живой путь",
                 applies_to="[app/live.py]")
    write_lesson(cfg.memory_dir, "feedback_dead.md", description="путь исчез",
                 applies_to="[app/gone.py]")
    _, broken = ST.scan(cfg, today=TODAY)
    broken_names = [n for n, _ in broken]
    assert "feedback_dead.md" in broken_names
    assert "feedback_live.md" not in broken_names


def test_write_pending_creates_and_removes(cfg) -> None:
    out = Path(cfg.memory_dir) / ST.STALE_FILE
    assert ST.write_pending(cfg, stale=[("2026-01-01", "feedback_x.md", "d")], broken=[], today=TODAY)
    assert out.exists() and "reverify_after" in out.read_text(encoding="utf-8")
    # пустой долг → файл удаляется (не шумим на старте)
    assert ST.write_pending(cfg, stale=[], broken=[], today=TODAY) is False
    assert not out.exists()


def test_run_end_to_end(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_old.md", description="d", reverify_after="2026-01-01")
    assert ST.run(cfg, today=TODAY) is True
    assert (Path(cfg.memory_dir) / ST.STALE_FILE).exists()
