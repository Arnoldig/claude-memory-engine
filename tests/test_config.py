"""Тесты загрузки и параметризации конфига."""
from __future__ import annotations

import json
from pathlib import Path

from claude_memory import config as C


def test_defaults_are_neutral() -> None:
    cfg = C.MemoryConfig(memory_dir="/m", project_root="/p")
    assert cfg.core_file == "MEMORY.md"
    assert cfg.strongest_model_substr == "opus"
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


# ── нормализация: описка ОДНОЗНАЧНА по намерению → понимаем, а не жалуемся ──────

def test_normalizes_unambiguous_typos_in_list_fields(tmp_path: Path) -> None:
    """`.py` не может значить ничего, кроме `py` — спрашивать не о чем, надо понять.
    Каждый случай раньше ломался МОЛЧА (см. докстринг MemoryConfig.__post_init__)."""
    cfg = C.MemoryConfig(
        memory_dir=str(tmp_path), project_root=str(tmp_path),
        retrieve_extensions=(".py", ".js", "css"),      # ведущая точка
        watched_dirs=("app/", "./tools/", "src"),        # хвостовой слэш и ведущее ./
        lesson_prefixes=("feedback_", "reference"),      # хвостовое подчёркивание
        staleness_skip_dirs=(".git/", "node_modules"),   # хвостовой слэш
    )
    assert cfg.retrieve_extensions == ("py", "js", "css")
    assert cfg.watched_dirs == ("app", "tools", "src")
    assert cfg.lesson_prefixes == ("feedback", "reference")
    assert cfg.staleness_skip_dirs == (".git", "node_modules")


def test_normalization_leaves_correct_values_untouched(tmp_path: Path) -> None:
    """Чиним только описку. Правильные значения (в т.ч. дефолты) обязаны остаться 1:1."""
    d = C.MemoryConfig(memory_dir=str(tmp_path), project_root=str(tmp_path))
    # Дефолт lesson_prefixes зеркалит ОФИЦИАЛЬНЫЙ словарь типов авто-памяти Claude Code
    # (user | feedback | project | reference). `user` добавлен в 0.10.0: без него урок
    # типа `user` был невидим стражу в любом проекте на дефолтах.
    assert d.retrieve_extensions[0] == "py"
    assert d.lesson_prefixes == ("feedback", "reference", "project", "user")
    assert ".git" in d.staleness_skip_dirs and "app" in d.watched_dirs
    # вложенный каталог не должен пострадать
    assert C.MemoryConfig(memory_dir=str(tmp_path), project_root=str(tmp_path),
                          watched_dirs=("site/src",)).watched_dirs == ("site/src",)


def test_normalization_holds_for_every_way_of_building_config(tmp_path: Path) -> None:
    """Инвариант обязан держаться при ЛЮБОМ способе создания — иначе разойдутся боевой
    путь (JSON) и тесты/`replace`. Потому нормализация в __post_init__, а не в _coerce."""
    from dataclasses import replace
    import json
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"memory_dir": str(tmp_path), "retrieve_extensions": [".py"],
                             "lesson_prefixes": ["feedback_"]}), encoding="utf-8")
    assert C.load(str(p)).retrieve_extensions == ("py",)          # из JSON
    assert C.load(str(p)).lesson_prefixes == ("feedback",)
    base = C.MemoryConfig(memory_dir=str(tmp_path), project_root=str(tmp_path))
    assert replace(base, watched_dirs=("app/",)).watched_dirs == ("app",)   # через replace


def test_normalization_drops_values_that_become_empty(tmp_path: Path) -> None:
    """`"."`/`"/"`/`"_"` схлопываются в пустоту — такой элемент матчил бы что попало
    (пустая ветка в regex `(?:a||b)` совпадает с чем угодно), поэтому выбрасываем."""
    cfg = C.MemoryConfig(memory_dir=str(tmp_path), project_root=str(tmp_path),
                         retrieve_extensions=(".", "py"),
                         watched_dirs=("/", "app"), lesson_prefixes=("_", "feedback"))
    assert cfg.retrieve_extensions == ("py",)
    assert cfg.watched_dirs == ("app",)
    assert cfg.lesson_prefixes == ("feedback",)
