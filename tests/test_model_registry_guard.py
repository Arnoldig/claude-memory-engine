"""Тесты стража реестра моделей (model_registry_guard, механизм #12).

Подстраховка от устаревания линейки моделей: ноль токенов, без сети.
"""
from __future__ import annotations

import datetime
import json
from dataclasses import replace
from pathlib import Path

from claude_memory import model_registry_guard as MR

TODAY = datetime.date(2026, 6, 18)


# ── проверка «неизвестная модель» (opt-in через known_model_substrs) ───────────

def test_unknown_model_nudges(cfg) -> None:
    cfg2 = replace(cfg, known_model_substrs=("haiku", "sonnet", "opus"))
    out = MR.nudges(cfg2, "claude-zeta-9", TODAY)
    assert any("zeta" in n for n in out)


def test_known_model_silent(cfg) -> None:
    cfg2 = replace(cfg, known_model_substrs=("haiku", "sonnet", "opus"))
    assert MR.nudges(cfg2, "claude-opus-4-8", TODAY) == []


def test_unknown_check_off_when_registry_empty(cfg) -> None:
    # пустой known_model_substrs → проверка выключена (opt-in)
    assert MR.nudges(cfg, "claude-zeta-9", TODAY) == []


def test_no_model_no_unknown_nudge(cfg) -> None:
    cfg2 = replace(cfg, known_model_substrs=("opus",))
    assert MR.nudges(cfg2, None, TODAY) == []


# ── проверка просрочки ручной сверки (ловит ДЕАКТИВАЦИЮ модели) ─────────────────

def test_stale_registry_nudges(cfg) -> None:
    cfg2 = replace(cfg, model_registry_verified_on="2026-01-01", model_registry_max_age_days=30)
    out = MR.nudges(cfg2, "claude-opus-4-8", TODAY)
    assert any("registry" in n.lower() for n in out)


def test_fresh_registry_silent(cfg) -> None:
    cfg2 = replace(cfg, model_registry_verified_on="2026-06-10", model_registry_max_age_days=60)
    assert MR.nudges(cfg2, "claude-opus-4-8", TODAY) == []


def test_verified_on_none_silent(cfg) -> None:
    assert MR.nudges(cfg, "claude-opus-4-8", TODAY) == []   # таймер выключен по умолчанию


def test_bad_verified_on_date_does_not_crash(cfg) -> None:
    cfg2 = replace(cfg, model_registry_verified_on="не-дата")
    assert MR.nudges(cfg2, "claude-opus-4-8", TODAY) == []   # кривая дата → тихо


# ── резолв модели сессии: event.model → транскрипт ─────────────────────────────

def test_resolve_model_from_event(cfg) -> None:
    assert MR.resolve_model({"model": "claude-opus-4-8"}, cfg) == "claude-opus-4-8"


def test_resolve_model_from_transcript(cfg, tmp_path) -> None:
    tr = tmp_path / "t.jsonl"
    tr.write_text(
        json.dumps({"message": {"role": "user", "content": "hi"}}) + "\n"
        + json.dumps({"message": {"role": "assistant", "model": "claude-haiku-4-5"}}) + "\n"
        + json.dumps({"message": {"role": "assistant", "model": "claude-opus-4-8"}}) + "\n",
        encoding="utf-8",
    )
    # берём ПОСЛЕДНюю модель ассистента
    assert MR.resolve_model({"transcript_path": str(tr)}, cfg) == "claude-opus-4-8"


def test_resolve_model_missing_everywhere(cfg) -> None:
    assert MR.resolve_model({}, cfg) is None
    assert MR.resolve_model({"transcript_path": "/no/such/file.jsonl"}, cfg) is None


# ── оба нуджа сразу + интеграция run() ─────────────────────────────────────────

def test_both_nudges_together(cfg) -> None:
    cfg2 = replace(
        cfg,
        known_model_substrs=("opus",),
        model_registry_verified_on="2026-01-01",
        model_registry_max_age_days=30,
    )
    out = MR.nudges(cfg2, "claude-zeta-9", TODAY)
    assert len(out) == 2


def test_run_uses_event_model(cfg) -> None:
    cfg2 = replace(cfg, known_model_substrs=("opus",))
    text = MR.run({"model": "claude-zeta-9"}, cfg2, TODAY)
    assert "zeta" in text
    text2 = MR.run({"model": "claude-opus-4-8"}, cfg2, TODAY)
    assert text2 == ""
