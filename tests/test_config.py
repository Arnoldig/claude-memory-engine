"""Тесты загрузки и параметризации конфига."""
from __future__ import annotations

import json
from pathlib import Path

from claude_memory import config as C


def test_defaults_are_neutral() -> None:
    cfg = C.MemoryConfig(memory_dir="/m", project_root="/p")
    assert cfg.core_file == "MEMORY.md"
    assert cfg.strongest_model_substr == "fable"
    assert cfg.marker_limit == 200
    assert ("workflow", "Workflow & methodology") in cfg.topic_order
    assert cfg.topic_titles()["core"].startswith("Hot core")


def test_load_from_json_overrides(tmp_path: Path) -> None:
    cfg_file = tmp_path / "claude-memory.config.json"
    cfg_file.write_text(json.dumps({
        "memory_dir": "/custom/mem",
        "project_root": "/custom/proj",
        "strongest_model_substr": "opus",
        "marker_limit": 120,
        "topic_order": [["t1", "Тема 1"], ["t2", "Тема 2"]],
        "routine_subagent_types": ["Explore"],
        "precedent_keyword": "Precedent",
    }), encoding="utf-8")
    cfg = C.load(str(cfg_file))
    assert cfg.memory_dir == "/custom/mem"
    assert cfg.strongest_model_substr == "opus"
    assert cfg.marker_limit == 120
    assert cfg.topic_order == (("t1", "Тема 1"), ("t2", "Тема 2"))  # list→tuple of tuples
    assert cfg.routine_subagent_types == ("Explore",)               # list→tuple
    assert cfg.precedent_keyword == "Precedent"


def test_unknown_keys_ignored(tmp_path: Path) -> None:
    cfg_file = tmp_path / "claude-memory.config.json"
    cfg_file.write_text(json.dumps({
        "memory_dir": "/m", "project_root": "/p", "totally_unknown_field": 42,
    }), encoding="utf-8")
    cfg = C.load(str(cfg_file))  # не падает на чужом поле
    assert cfg.memory_dir == "/m"
    assert not hasattr(cfg, "totally_unknown_field")


def test_paths_from_env_when_absent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_MEMORY_DIR", str(tmp_path / "mem"))
    monkeypatch.setenv("CLAUDE_PROJECT_ROOT", str(tmp_path / "proj"))
    monkeypatch.delenv("CLAUDE_MEMORY_CONFIG", raising=False)
    cfg = C.load()
    assert cfg.memory_dir == str(tmp_path / "mem")
    assert cfg.project_root == str(tmp_path / "proj")


def test_get_config_caches_and_resets(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_MEMORY_DIR", str(tmp_path / "a"))
    monkeypatch.delenv("CLAUDE_MEMORY_CONFIG", raising=False)
    C.reset_cache()
    first = C.get_config()
    monkeypatch.setenv("CLAUDE_MEMORY_DIR", str(tmp_path / "b"))
    assert C.get_config() is first             # кэш — тот же объект
    C.reset_cache()
    assert C.get_config().memory_dir == str(tmp_path / "b")  # после сброса перечитан
