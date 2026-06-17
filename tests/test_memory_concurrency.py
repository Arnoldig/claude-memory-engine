"""Тесты оптимистичной блокировки параллельной правки памяти (CAS)."""
from __future__ import annotations

from pathlib import Path

from claude_memory import memory_concurrency as MC


def test_content_hash_missing_is_none(tmp_path: Path) -> None:
    assert MC.content_hash(str(tmp_path / "nope.md")) is None


def test_no_marker_fail_open(tmp_path: Path) -> None:
    f = tmp_path / "MEMORY.md"
    f.write_text("v1", encoding="utf-8")
    # сессия не читала файл (нет маркера) → первая правка разрешена
    assert MC.conflict_reason("s1", str(f), str(tmp_path / "tmp")) is None


def test_unchanged_since_seen_allows(tmp_path: Path) -> None:
    f = tmp_path / "MEMORY.md"
    f.write_text("v1", encoding="utf-8")
    td = str(tmp_path / "tmp")
    MC.record_seen("s1", str(f), td)
    assert MC.conflict_reason("s1", str(f), td) is None


def test_changed_by_other_session_blocks(tmp_path: Path) -> None:
    f = tmp_path / "MEMORY.md"
    f.write_text("v1", encoding="utf-8")
    td = str(tmp_path / "tmp")
    MC.record_seen("s1", str(f), td)        # s1 видел v1
    f.write_text("v2-by-other", encoding="utf-8")  # другая сессия записала
    r = MC.conflict_reason("s1", str(f), td)
    assert r and "изменён другой сессией" in r


def test_sessions_isolated(tmp_path: Path) -> None:
    f = tmp_path / "MEMORY.md"
    f.write_text("v1", encoding="utf-8")
    td = str(tmp_path / "tmp")
    MC.record_seen("s1", str(f), td)
    # s2 ничего не видел → его маркера нет → fail-open
    assert MC.conflict_reason("s2", str(f), td) is None
