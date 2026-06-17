"""Тесты диспетчера хук-логики (раньше не покрывался — здесь и поймался hash()-баг)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from claude_memory import hooks_cli as H
from conftest import write_lesson


def _edit_event(path: str, new_string: str = "x") -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": path, "new_string": new_string}}


def test_applies_gate_fires_then_dedups(cfg, tmp_path) -> None:
    write_lesson(cfg.memory_dir, "feedback_app.md",
                 description="правила app", applies_to="[app/*.py]")
    target = f"{cfg.project_root}/app/x.py"
    td = str(tmp_path / "tmp")
    first = H.ev_pre_edit_guard(_edit_event(target), cfg, "sess1", td)
    second = H.ev_pre_edit_guard(_edit_event(target), cfg, "sess1", td)
    assert first is not None and "applies-to-gate" in first
    assert second is None  # разовость на (сессию, файл)


def test_applies_marker_uses_stable_sha256(cfg, tmp_path) -> None:
    """Регресс-замок к BLOCKER: маркер именуется СТАБИЛЬНЫМ sha256, не hash() (рандом per-process)."""
    write_lesson(cfg.memory_dir, "feedback_app.md", description="d", applies_to="[app/*.py]")
    target = f"{cfg.project_root}/app/x.py"
    td = str(tmp_path / "tmp")
    H.ev_pre_edit_guard(_edit_event(target), cfg, "sess1", td)
    expected_digest = hashlib.sha256(target.encode("utf-8")).hexdigest()
    expected = Path(td) / f"{H._APPLIES_MARKER}sess1" / expected_digest
    assert expected.exists()  # имя маркера детерминировано → дедуп переживёт смену процесса


def test_session_marker_format_denied(cfg, tmp_path) -> None:
    long_marker = "<!-- 2026-06-17 " + "x" * 250 + " -->"
    event = {"tool_name": "Write",
             "tool_input": {"file_path": f"{cfg.memory_dir}/{cfg.session_lessons_file}",
                            "content": long_marker}}
    r = H.ev_pre_edit_guard(event, cfg, "s", str(tmp_path / "tmp"))
    assert r and "session-marker-guard" in r


def test_concurrency_conflict_denied(cfg, tmp_path) -> None:
    from claude_memory import memory_concurrency as MC
    mem_file = Path(cfg.memory_dir) / "feedback_x.md"
    mem_file.write_text("v1", encoding="utf-8")
    td = str(tmp_path / "tmp")
    MC.record_seen("s1", str(mem_file), td)
    mem_file.write_text("v2-other", encoding="utf-8")  # другая сессия записала
    r = H.ev_pre_edit_guard(_edit_event(str(mem_file)), cfg, "s1", td)
    assert r and "другой сессией" in r


def test_retrieve_event(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_chat.md",
                 description="чат", applies_to="[app/routers/chat.py]")
    out = H.ev_retrieve({"prompt": "правлю app/routers/chat.py"}, cfg)
    assert "feedback_chat.md" in out


def test_bloat_check_core_over_budget(cfg, tmp_path) -> None:
    core = Path(cfg.memory_dir) / cfg.core_file
    core.write_text("x" * (cfg.core_budget_bytes + 100), encoding="utf-8")
    event = {"tool_name": "Write", "tool_input": {"file_path": str(core)}}
    out = H.ev_bloat_check(event, cfg)
    assert "budget" in out and cfg.core_file in out  # англ. дефолт; ядро в символах


def test_session_start_writes_catalog(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_a.md", description="a", topic="workflow")
    H.ev_session_start(cfg)
    catalog = Path(cfg.memory_dir) / cfg.catalog_file
    assert catalog.exists()
    assert "feedback_a.md" in catalog.read_text(encoding="utf-8")


def test_agent_guard_via_dispatcher(cfg, tmp_path) -> None:
    event = {"tool_name": "Agent", "tool_input": {"subagent_type": "Explore"}}
    r = H.ev_agent_guard(event, cfg, "s1", str(tmp_path / "tmp"))
    assert r is not None and "model" in r


def test_session_end_writes_stale(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_old.md", description="d", reverify_after="2026-01-01")
    H.ev_session_end(cfg)
    stale = Path(cfg.memory_dir) / "_stale_pending.md"
    assert stale.exists() and "feedback_old.md" in stale.read_text(encoding="utf-8")


def test_session_start_surfaces_stale(cfg) -> None:
    (Path(cfg.memory_dir) / "_stale_pending.md").write_text(
        "# Память — нужна повторная проверка\n\n- **feedback_x.md** просрочен\n", encoding="utf-8"
    )
    out = H.ev_session_start(cfg)
    assert "повторная проверка" in out and "feedback_x.md" in out


def test_stop_event_non_git_is_none(cfg, tmp_path) -> None:
    assert H.ev_stop(cfg, str(tmp_path / "nope"), now_ts=10_000_000_000) is None


def test_applies_to_cli_mode(cfg, tmp_path, monkeypatch, capsys) -> None:
    """`cme_hook.sh applies-to <path>` печатает уроки по пути и НЕ читает stdin."""
    import json
    import sys

    import pytest

    from claude_memory import config as C

    write_lesson(cfg.memory_dir, "feedback_app.md", description="rules", applies_to="[app/*.py]")
    cf = tmp_path / "c.json"
    cf.write_text(json.dumps({"memory_dir": cfg.memory_dir, "project_root": cfg.project_root}))
    monkeypatch.setenv("CLAUDE_MEMORY_CONFIG", str(cf))
    monkeypatch.setattr(sys, "argv", ["cme", "applies-to", f"{cfg.project_root}/app/x.py"])
    C.reset_cache()
    try:
        with pytest.raises(SystemExit):
            H.main()
    finally:
        C.reset_cache()
    assert "feedback_app.md" in capsys.readouterr().out
