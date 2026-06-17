"""Тесты стража выбора модели суб-агентов."""
from __future__ import annotations

from dataclasses import replace

from claude_memory import subagent_model_guard as G


def test_routine_without_model_nudges(cfg) -> None:
    r = G.decide("Agent", {"subagent_type": "Explore"}, cfg)
    assert r and "model" in r


def test_explicit_model_respected(cfg) -> None:
    assert G.decide("Agent", {"subagent_type": "Explore", "model": "haiku"}, cfg) is None


def test_non_routine_type_ignored(cfg) -> None:
    assert G.decide("Agent", {"subagent_type": "Plan"}, cfg) is None
    assert G.decide("Agent", {"subagent_type": "Plan", "model": "fable"}, cfg) is None


def test_strongest_model_for_routine_nudges(cfg) -> None:
    r = G.decide_strongest("Agent", {"subagent_type": "general-purpose", "model": "claude-fable-5"}, cfg)
    assert r and "STRONGEST" in r
    # обычная модель — без нуджа
    assert G.decide_strongest("Agent", {"subagent_type": "general-purpose", "model": "sonnet"}, cfg) is None


def test_strongest_substr_is_configurable(cfg) -> None:
    cfg2 = replace(cfg, strongest_model_substr="opus")
    assert G.decide_strongest("Agent", {"subagent_type": "Explore", "model": "opus-x"}, cfg2) is not None
    assert G.decide_strongest("Agent", {"subagent_type": "Explore", "model": "fable"}, cfg2) is None


def test_strongest_substr_accepts_list(cfg) -> None:
    # список «премиальных» подстрок — совпадение по любой (гибко под N моделей/поколений)
    cfg2 = replace(cfg, strongest_model_substr=["fable", "opus-5"])
    assert G.decide_strongest("Agent", {"subagent_type": "Explore", "model": "claude-fable-5"}, cfg2) is not None
    assert G.decide_strongest("Agent", {"subagent_type": "Explore", "model": "claude-opus-5-x"}, cfg2) is not None
    assert G.decide_strongest("Agent", {"subagent_type": "Explore", "model": "sonnet"}, cfg2) is None


def test_forgot_model_is_count_agnostic(cfg) -> None:
    # «забыл model» не зависит от того, сколько моделей доступно — ловит любой пропуск
    assert G.decide("Agent", {"subagent_type": "Explore"}, cfg) is not None


def test_routine_types_configurable(cfg) -> None:
    cfg2 = replace(cfg, routine_subagent_types=("custom-agent",))
    assert G.decide("Agent", {"subagent_type": "custom-agent"}, cfg2) is not None
    assert G.decide("Agent", {"subagent_type": "Explore"}, cfg2) is None  # больше не рутинный


def test_gate_fires_once(cfg, tmp_path) -> None:
    td = str(tmp_path / "tmp")
    first = G.gate("sess1", "Agent", {"subagent_type": "Explore"}, td, cfg)
    second = G.gate("sess1", "Agent", {"subagent_type": "Explore"}, td, cfg)
    assert first is not None
    assert second is None  # маркер снял блок


def test_non_agent_tool_ignored(cfg, tmp_path) -> None:
    assert G.gate("s", "Bash", {"command": "ls"}, str(tmp_path), cfg) is None
