"""Стена платформы для горячего ядра (core_wall_bytes).

Claude Code грузит первые 200 строк ИЛИ первые 25 КБ (25 600 байт) файла
авто-памяти — остальное молча не попадает в контекст (сверено 2026-08-17,
code.claude.com/docs/en/memory.md). Знаковый бюджет качества
(core_budget_bytes) эту механику не заменяет: кириллица в UTF-8 весит два
байта на букву, и русское ядро уходит за байтовую стену раньше, чем знаковый
бюджет скажет «превышен». Поэтому стена меряется ОТДЕЛЬНО и в единицах самой
стены — байтах файла; решение владельца «бюджет качества остаётся в знаках»
не затронуто.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from claude_memory import hooks_cli as H
from claude_memory.config import MemoryConfig


def _core(cfg, text: str) -> Path:
    p = Path(cfg.memory_dir) / cfg.core_file
    p.write_text(text, encoding="utf-8")
    return p


def _ev(path) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": str(path)}}


def test_wall_default_is_platform_value() -> None:
    # 25 КБ стены платформы = 25 * 1024 = 25600 байт (значение хозяина, не наше)
    assert MemoryConfig(memory_dir="/m", project_root="/p").core_wall_bytes == 25600


def test_wall_over_warns(cfg) -> None:
    p = _core(cfg, "x" * 150)
    out = H.ev_bloat_check(_ev(p), replace(cfg, core_wall_bytes=100, core_budget_bytes=0))
    assert "150" in out and "100" in out


def test_wall_approach_warns(cfg) -> None:
    # core_warn_ratio (0.8 по умолчанию) даёт раннее предупреждение и для стены
    p = _core(cfg, "x" * 85)
    out = H.ev_bloat_check(_ev(p), replace(cfg, core_wall_bytes=100, core_budget_bytes=0))
    assert out != "" and "85" in out


def test_wall_zero_disables(cfg) -> None:
    p = _core(cfg, "x" * 1000)
    out = H.ev_bloat_check(_ev(p), replace(cfg, core_wall_bytes=0, core_budget_bytes=0))
    assert out == ""


def test_wall_fires_while_char_budget_is_silent(cfg) -> None:
    """Мотивирующий случай: 400 кириллических букв = 800 байт. Знаковый бюджет
    в 1000 знаков молчит (400 < 800 = порог раннего предупреждения), а байтовая
    стена в 700 байт уже пройдена — до правки это состояние было невидимым."""
    p = _core(cfg, "б" * 400)
    out = H.ev_bloat_check(_ev(p), replace(cfg, core_budget_bytes=1000, core_wall_bytes=700))
    assert "700" in out
    assert "budget" not in out  # это говорит стена, а не бюджет качества


def test_pre_compact_includes_wall(cfg) -> None:
    _core(cfg, "x" * 150)
    out = H.ev_pre_compact(replace(cfg, core_wall_bytes=100, core_budget_bytes=0))
    assert "100" in out


def test_pre_compact_wall_zero_disables(cfg) -> None:
    _core(cfg, "x" * 1000)
    assert H.ev_pre_compact(replace(cfg, core_wall_bytes=0, core_budget_bytes=0)) == ""
