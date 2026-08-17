"""Поведение нуля у числовых стражей: «0 = выключен» (контракт GUARD_THRESHOLDS).

Контракт объявлен в правилах проекта и закреплён на ДЕФОЛТАХ
(test_no_guard_threshold_defaults_to_disabled), но само поведение нуля до этой
правки не проверял никто. В трёх местах ноль работал наоборот — включал
срабатывание на каждом событии: core_budget_bytes=0 давал предупреждение при
каждой записи ядра (и в ev_bloat_check, и в ev_pre_compact),
feedback_warn_bytes=0 — на каждом уроке, а marker_limit=0 превращал
предупреждающий страж в блокирующий каждую правку session-файла.

У каждого нулевого случая есть парный позитивный: страж с ненулевым порогом
обязан срабатывать — иначе «выключено» неотличимо от «страж мёртв».
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from claude_memory import hooks_cli as H
from claude_memory import session_marker_guard as SG
from conftest import write_lesson


def _write_event(path) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": str(path)}}


def _core(cfg) -> Path:
    p = Path(cfg.memory_dir) / cfg.core_file
    p.write_text("x" * 100, encoding="utf-8")
    return p


# --- core_budget_bytes: ev_bloat_check ---

def test_core_budget_zero_silences_bloat_check(cfg) -> None:
    out = H.ev_bloat_check(_write_event(_core(cfg)), replace(cfg, core_budget_bytes=0))
    assert out == ""


def test_core_budget_positive_still_warns(cfg) -> None:
    out = H.ev_bloat_check(_write_event(_core(cfg)), replace(cfg, core_budget_bytes=50))
    assert "budget" in out


# --- core_budget_bytes: ev_pre_compact ---

def test_core_budget_zero_silences_pre_compact(cfg) -> None:
    _core(cfg)
    assert H.ev_pre_compact(replace(cfg, core_budget_bytes=0)) == ""


def test_pre_compact_positive_still_warns(cfg) -> None:
    _core(cfg)
    assert H.ev_pre_compact(replace(cfg, core_budget_bytes=50)) != ""


# --- feedback_warn_bytes ---

def _big_lesson(cfg) -> Path:
    # name и короткое description заполнены, чтобы единственным возможным
    # предупреждением осталось именно предупреждение о размере
    return write_lesson(cfg.memory_dir, "feedback_big.md",
                        name="feedback-big", description="d", body="x" * 200)


def test_feedback_warn_zero_silences_lesson_size(cfg) -> None:
    out = H.ev_bloat_check(_write_event(_big_lesson(cfg)), replace(cfg, feedback_warn_bytes=0))
    assert "feedback_big.md" not in out


def test_feedback_warn_positive_still_warns(cfg) -> None:
    out = H.ev_bloat_check(_write_event(_big_lesson(cfg)), replace(cfg, feedback_warn_bytes=50))
    assert "feedback_big.md" in out


# --- marker_limit (блокирующий PreToolUse-страж) ---

def _marker_input(cfg, text: str) -> dict:
    return {"file_path": f"/m/{cfg.session_lessons_file}", "content": text}


def test_marker_limit_zero_disables_guard(cfg) -> None:
    ok = "<!-- 2026-06-17 abc #t — суть -->"
    assert SG.violation_reason("Write", _marker_input(cfg, ok),
                               replace(cfg, marker_limit=0)) is None


def test_marker_limit_zero_disables_multiline_check_too(cfg) -> None:
    # 0 выключает стража ЦЕЛИКОМ, включая проверку многострочности
    multi = "<!-- 2026-06-17 начало\nпродолжение -->"
    assert SG.violation_reason("Write", _marker_input(cfg, multi),
                               replace(cfg, marker_limit=0)) is None


def test_marker_limit_positive_still_blocks(cfg) -> None:
    marker = "<!-- 2026-06-17 a bit longer than thirty chars -->"
    assert SG.violation_reason("Write", _marker_input(cfg, marker),
                               replace(cfg, marker_limit=30)) is not None
