"""Тесты оптимистичной блокировки параллельной правки памяти (CAS)."""
from __future__ import annotations

import re
from dataclasses import replace
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
    assert r and "[memory-concurrency-guard]" in r


def _make_conflict(tmp_path: Path) -> tuple:
    f = tmp_path / "MEMORY.md"
    f.write_text("v1", encoding="utf-8")
    td = str(tmp_path / "tmp")
    MC.record_seen("s1", str(f), td)
    f.write_text("v2-by-other", encoding="utf-8")
    return f, td


def test_conflict_text_neutral_default(tmp_path: Path) -> None:
    """Дефолтный текст deny языко-нейтрален: до этой правки он был зашит в код
    по-русски мимо messages.py — единственная такая строка в пакете."""
    f, td = _make_conflict(tmp_path)
    r = MC.conflict_reason("s1", str(f), td)
    assert r and "[memory-concurrency-guard]" in r
    assert re.search(r"[а-яА-ЯёЁ]", r) is None


def test_conflict_text_overridable_via_messages(tmp_path: Path, cfg) -> None:
    """Текст идёт через msg(): проект переводит его ключом concurrency.conflict_reason."""
    f, td = _make_conflict(tmp_path)
    cfg2 = replace(cfg, messages={"concurrency.conflict_reason": "OVERRIDE {filename}"})
    assert MC.conflict_reason("s1", str(f), td, cfg2) == "OVERRIDE MEMORY.md"


def test_sessions_isolated(tmp_path: Path) -> None:
    f = tmp_path / "MEMORY.md"
    f.write_text("v1", encoding="utf-8")
    td = str(tmp_path / "tmp")
    MC.record_seen("s1", str(f), td)
    # s2 ничего не видел → его маркера нет → fail-open
    assert MC.conflict_reason("s2", str(f), td) is None
