"""Тесты журнала эффективности суб-агентов."""
from __future__ import annotations

import json
from pathlib import Path

from claude_memory import subagent_efficiency_log as S


def test_format_explicit_model(cfg) -> None:
    line = S.format_record("sess1", {"subagent_type": "Explore", "model": "haiku", "prompt": "x" * 100},
                           "2026-06-17T10:00:00Z", routine_types=frozenset(cfg.routine_subagent_types))
    rec = json.loads(line)
    assert rec["model"] == "haiku" and rec["routine"] is True and rec["prompt_chars"] == 100
    assert line.endswith("\n")


def test_format_inherited_when_empty(cfg) -> None:
    line = S.format_record("s", {"subagent_type": "general-purpose"}, "T",
                           routine_types=frozenset(cfg.routine_subagent_types))
    assert json.loads(line)["model"] == "INHERITED"


def test_format_non_routine(cfg) -> None:
    line = S.format_record("s", {"subagent_type": "Plan", "model": "opus"}, "T",
                           routine_types=frozenset(cfg.routine_subagent_types))
    assert json.loads(line)["routine"] is False


def test_format_omitted_type_resolves_to_default(cfg) -> None:
    # subagent_type опущен → пишем реальный default (general-purpose), routine=True,
    # type_implicit=True (сигнал «тип не указан» остаётся видимым для анализа)
    line = S.format_record("s", {"model": "opus", "prompt": "x" * 5}, "T",
                           routine_types=frozenset(cfg.routine_subagent_types),
                           default_type="general-purpose")
    rec = json.loads(line)
    assert rec["type"] == "general-purpose"
    assert rec["type_implicit"] is True
    assert rec["routine"] is True


def test_format_explicit_type_not_implicit(cfg) -> None:
    line = S.format_record("s", {"subagent_type": "Explore", "model": "haiku"}, "T",
                           routine_types=frozenset(cfg.routine_subagent_types),
                           default_type="general-purpose")
    assert json.loads(line)["type_implicit"] is False


def test_format_non_dict_none() -> None:
    assert S.format_record("s", "notdict", "T", routine_types=frozenset()) is None


def test_append_creates_and_appends(tmp_path: Path) -> None:
    log = tmp_path / "sub" / "_eff.jsonl"
    assert S.append_record(str(log), '{"a": 1}\n') is True
    assert S.append_record(str(log), '{"b": 2}\n') is True
    assert log.read_text(encoding="utf-8").splitlines() == ['{"a": 1}', '{"b": 2}']


def test_append_empty_noop(tmp_path: Path) -> None:
    assert S.append_record(str(tmp_path / "x.jsonl"), None) is False
    assert not (tmp_path / "x.jsonl").exists()
