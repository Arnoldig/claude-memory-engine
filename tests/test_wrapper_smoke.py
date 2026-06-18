"""Сквозной smoke-тест тонкой bash-обёртки hooks/cme_hook.sh.

Питоновская логика покрыта unit-тестами; здесь проверяем саму «проводку» — что обёртка
задаёт окружение и зовёт диспетчер, и каждое событие отрабатывает без трейсбека с верным
кодом возврата (0 — обслуживание/инъекция, 2 — блокирующий страж).
"""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from conftest import ROOT

WRAPPER = ROOT / "hooks" / "cme_hook.sh"


def _run(event: str, payload: dict, cfg_path):
    env = dict(os.environ)
    # обёртка ПРЕПЕНДит свой (несуществующий в репо) memory_engine, а корень репо оставляет
    # в PYTHONPATH → claude_memory импортируется из исходников.
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["CLAUDE_MEMORY_CONFIG"] = str(cfg_path)
    # Изолируем TMPDIR на кейс: разовые маркеры стражей (model-guard, applies-to) живут в
    # tmp и иначе переживают прогоны → «разовый за сессию» страж не сработал бы повторно.
    tmp = cfg_path.parent / "t"
    tmp.mkdir(exist_ok=True)
    env["TMPDIR"] = str(tmp)
    return subprocess.run(
        ["bash", str(WRAPPER), event],
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=30,
    )


@pytest.fixture
def sandbox(tmp_path):
    mem = tmp_path / "memory"; mem.mkdir()
    proj = tmp_path / "proj"; proj.mkdir()
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"memory_dir": str(mem), "project_root": str(proj)}), encoding="utf-8")
    return cfg, mem


def test_wrapper_exists_and_executable() -> None:
    assert WRAPPER.is_file(), f"обёртка не найдена: {WRAPPER}"


def test_wrapper_session_start(sandbox) -> None:
    cfg, _ = sandbox
    p = _run("session-start", {"hook_event_name": "SessionStart", "model": "claude-opus-4-8"}, cfg)
    assert p.returncode == 0 and "Traceback" not in p.stderr


def test_wrapper_retrieve_silent_on_irrelevant(sandbox) -> None:
    cfg, _ = sandbox
    p = _run("retrieve", {"prompt": "погода в москве сегодня"}, cfg)
    assert p.returncode == 0 and p.stdout.strip() == "" and "Traceback" not in p.stderr


def test_wrapper_marker_guard_blocks(sandbox) -> None:
    cfg, mem = sandbox
    marker = "<!-- 2026-06-18 " + ("x" * 250) + " -->"
    p = _run("pre-edit-guard", {
        "tool_name": "Write", "session_id": "s1",
        "tool_input": {"file_path": str(mem / "feedback_session_end_lessons.md"), "content": marker},
    }, cfg)
    assert p.returncode == 2 and "session-marker-guard" in p.stderr


def test_wrapper_agent_guard_blocks(sandbox) -> None:
    cfg, _ = sandbox
    p = _run("agent-guard", {
        "tool_name": "Agent", "session_id": "s2", "tool_input": {"subagent_type": "Explore"},
    }, cfg)
    assert p.returncode == 2 and "subagent-model-guard" in p.stderr


def test_wrapper_applies_to_cli(sandbox) -> None:
    cfg, _ = sandbox
    # CLI-режим (не событие): не должен зависнуть на stdin, отдаёт пусто/exit 0
    p = subprocess.run(
        ["bash", str(WRAPPER), "applies-to", "app/x.py"],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTHONPATH": str(ROOT), "CLAUDE_MEMORY_CONFIG": str(cfg)},
    )
    assert p.returncode == 0 and "Traceback" not in p.stderr


@pytest.mark.parametrize("event,payload", [
    ("post-record", {"tool_name": "Read", "session_id": "s", "tool_input": {"file_path": "x.md"}}),
    ("bloat-check", {"tool_input": {"file_path": "MEMORY.md"}}),
    ("agent-log", {"tool_name": "Agent", "session_id": "s", "tool_input": {"subagent_type": "Explore", "model": "haiku"}}),
    ("pre-compact", {"hook_event_name": "PreCompact"}),
    ("session-end", {"hook_event_name": "SessionEnd"}),
    ("stop-check", {"hook_event_name": "Stop"}),
])
def test_wrapper_all_events_no_crash(sandbox, event, payload) -> None:
    cfg, _ = sandbox
    p = _run(event, payload, cfg)
    assert p.returncode in (0, 2), f"{event}: rc={p.returncode}"
    assert "Traceback" not in p.stderr, f"{event}: {p.stderr[:300]}"
