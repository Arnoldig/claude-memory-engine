"""Тесты стража актуальности LLM (llm_actuality): состояние, суточный троттлинг,
реактивная «незнакомая модель», строка чек-листа, CLI-запись итога сверки."""
from __future__ import annotations

import datetime
from dataclasses import replace

from claude_memory import hooks_cli as H
from claude_memory import llm_actuality as LA

NOW = datetime.datetime(2026, 6, 30, 12, 0, tzinfo=datetime.timezone.utc)


# ── состояние и семейства ─────────────────────────────────────────────────────

def test_state_roundtrip_and_families(cfg) -> None:
    # сид: дефолтные known_model_substrs
    assert LA.families(cfg) == ("opus", "sonnet", "haiku", "fable")
    LA.record_state(cfg, NOW, "confirmed", fam=["opus", "sonnet"])
    st = LA.load_state(cfg)
    assert st["result"] == "confirmed" and st["verified_on"].startswith("2026-06-30")
    assert LA.families(cfg) == ("opus", "sonnet")   # теперь из состояния


def test_is_due(cfg) -> None:
    assert LA.is_due({}, NOW, 24)                                   # нет даты → пора
    recent = {"verified_on": (NOW - datetime.timedelta(hours=1)).isoformat()}
    assert not LA.is_due(recent, NOW, 24)
    old = {"verified_on": (NOW - datetime.timedelta(hours=25)).isoformat()}
    assert LA.is_due(old, NOW, 24)


# ── нудж на SessionStart ──────────────────────────────────────────────────────

def test_session_start_reactive_unknown_model(cfg) -> None:
    out = LA.session_start_nudge({"model": "claude-zeta-9"}, cfg, now=NOW)
    assert "claude-zeta-9" in out                                   # реактивный нудж незнакомой


def test_session_start_known_model_no_reactive_but_daily(cfg) -> None:
    out = LA.session_start_nudge({"model": "claude-opus-4-8"}, cfg, now=NOW)
    assert "claude-opus-4-8" not in out                             # известная → нет нуджа незнакомой
    assert "llm-actuality" in out                                   # state пуст → суточная просьба


def test_daily_ask_throttled_after_record(cfg) -> None:
    LA.record_state(cfg, NOW, "confirmed")
    out1 = LA.session_start_nudge({"model": "claude-opus-4-8"}, cfg, now=NOW + datetime.timedelta(hours=1))
    assert "llm-actuality" not in out1                              # <24ч → не просим
    out2 = LA.session_start_nudge({"model": "claude-opus-4-8"}, cfg, now=NOW + datetime.timedelta(hours=25))
    assert "llm-actuality" in out2                                  # >24ч → снова пора


def test_disabled_silences_daily_and_checklist(cfg) -> None:
    cfg2 = replace(cfg, llm_actuality_enabled=False)
    assert LA.checklist_line(cfg2) == ""
    out = LA.session_start_nudge({"model": "claude-opus-4-8"}, cfg2, now=NOW)
    assert "llm-actuality" not in out                               # суточной просьбы нет


# ── строка чек-листа ──────────────────────────────────────────────────────────

def test_checklist_line_states(cfg) -> None:
    assert "not verified" in LA.checklist_line(cfg)                 # нет состояния
    LA.record_state(cfg, NOW, "confirmed")
    assert "verified 2026-06-30" in LA.checklist_line(cfg)
    LA.record_state(cfg, NOW, "changes: Fable locked")
    line = LA.checklist_line(cfg)
    assert "Fable locked" in line and "⚠" in line


def test_checklist_includes_llm_line(cfg, tmp_path) -> None:
    from claude_memory import stale_reconcile as SR
    cfg2 = replace(cfg, stale_reconcile_gate=True, session_close_pattern="done")
    out = SR.reconcile_on_close(cfg2, "done", str(tmp_path), "s", str(tmp_path / "tmp"))
    assert "LLM actuality" in out                                   # строка статуса в чек-листе


# ── CLI-запись итога сверки ───────────────────────────────────────────────────

def test_cli_llm_verified_writes_state(cfg, tmp_path, monkeypatch) -> None:
    """`cme_hook.sh llm-verified` пишет состояние и НЕ читает stdin (не зависает)."""
    import json
    import sys

    import pytest

    from claude_memory import config as C

    cf = tmp_path / "c.json"
    cf.write_text(json.dumps({"memory_dir": cfg.memory_dir, "project_root": cfg.project_root}))
    monkeypatch.setenv("CLAUDE_MEMORY_CONFIG", str(cf))
    monkeypatch.setattr(sys, "argv", ["cme", "llm-verified"])
    C.reset_cache()
    try:
        with pytest.raises(SystemExit):
            H.main()
    finally:
        C.reset_cache()
    st = LA.load_state(cfg)
    assert st.get("result") == "confirmed" and st.get("verified_on")


def test_cli_llm_changes_updates_families(cfg, tmp_path, monkeypatch) -> None:
    import json
    import sys

    import pytest

    from claude_memory import config as C

    cf = tmp_path / "c.json"
    cf.write_text(json.dumps({"memory_dir": cfg.memory_dir, "project_root": cfg.project_root}))
    monkeypatch.setenv("CLAUDE_MEMORY_CONFIG", str(cf))
    monkeypatch.setattr(sys, "argv", ["cme", "llm-changes", "Fable locked", "--families", "opus,sonnet,haiku"])
    C.reset_cache()
    try:
        with pytest.raises(SystemExit):
            H.main()
    finally:
        C.reset_cache()
    st = LA.load_state(cfg)
    assert "Fable locked" in st.get("result", "")
    assert st.get("families") == ["opus", "sonnet", "haiku"]
