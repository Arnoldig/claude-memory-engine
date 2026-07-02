"""Тесты i18n-каталога сообщений и новых config-ключей (#memory-lib-cutover).

Покрывают: переопределение сообщений (язык), измерение ядра в символах/байтах,
ранний нудж на ratio, сужение размер-warning по префиксам/исключениям/override,
счётчик прецедентов, пропуск архива, конфигурируемые маркеры CATALOG, проектные
ноты SessionStart, и что каждый ключ msg() в коде существует в DEFAULT_MESSAGES.
"""
from __future__ import annotations

import datetime
import glob
import re
from dataclasses import replace
from pathlib import Path

from claude_memory import catalog_generate as CG
from claude_memory import hooks_cli as H
from claude_memory.messages import DEFAULT_MESSAGES, msg


# ── i18n message-каталог ──────────────────────────────────────────────────────

def test_msg_default_is_english(cfg) -> None:
    assert msg(cfg, "unit.chars") == "chars"
    assert "OVER" in msg(cfg, "bloat.core_over", core_file="M", size=1, unit="c", pct=1, budget=1)


def test_msg_override_changes_language(cfg) -> None:
    cfg2 = replace(cfg, messages={"unit.chars": "символов",
                                  "bloat.core_over": "ЯДРО {core_file} {size}{unit} {pct}%"})
    assert msg(cfg2, "unit.chars") == "символов"
    out = msg(cfg2, "bloat.core_over", core_file="MEMORY.md", size=99, unit="с", pct=80, budget=100)
    assert out == "ЯДРО MEMORY.md 99с 80%"


def test_msg_unknown_key_is_failsoft(cfg) -> None:
    assert msg(cfg, "no.such.key") == "no.such.key"  # не падаем, ключ виден


def test_every_msg_key_in_code_exists() -> None:
    """Регресс-замок: любой msg(cfg, "key") в движке имеет дефолт (защита от опечаток)."""
    root = Path(__file__).resolve().parents[1] / "claude_memory"
    used = set()
    for f in glob.glob(str(root / "*.py")):
        if f.endswith("messages.py"):
            continue
        for m in re.finditer(r'msg\([^,]+,\s*["\']([\w.]+)["\']', Path(f).read_text(encoding="utf-8")):
            used.add(m.group(1))
    missing = used - set(DEFAULT_MESSAGES)
    assert not missing, f"msg keys missing from DEFAULT_MESSAGES: {sorted(missing)}"


# ── размер ядра: символы vs байты (кириллица) ─────────────────────────────────

def _core(cfg, text: str) -> None:
    (Path(cfg.memory_dir) / cfg.core_file).write_text(text, encoding="utf-8")


def _bloat(cfg) -> str:
    ev = {"tool_name": "Write", "tool_input": {"file_path": str(Path(cfg.memory_dir) / cfg.core_file)}}
    return H.ev_bloat_check(ev, cfg)


def test_core_chars_vs_bytes_cyrillic(cfg) -> None:
    # 60 кириллических символов = 60 chars, но 120 байт (UTF-8 2 байта/символ)
    cfg_chars = replace(cfg, core_size_unit="chars", core_budget_bytes=100, core_warn_ratio=None)
    cfg_bytes = replace(cfg, core_size_unit="bytes", core_budget_bytes=100, core_warn_ratio=None)
    _core(cfg, "я" * 60)
    assert _bloat(cfg_chars) == ""        # 60 символов < 100 — тихо
    assert "OVER" in _bloat(cfg_bytes)    # 120 байт > 100 — превышение


# ── ранний нудж ядра на core_warn_ratio ───────────────────────────────────────

def test_core_warn_ratio_band(cfg) -> None:
    cfg2 = replace(cfg, core_size_unit="chars", core_budget_bytes=100, core_warn_ratio=0.8)
    _core(cfg, "x" * 85)
    out = _bloat(cfg2)
    assert "approaching" in out and "OVER" not in out      # 85% — ранний нудж
    _core(cfg, "x" * 105)
    assert "OVER" in _bloat(cfg2)                            # >100% — превышение
    cfg_off = replace(cfg2, core_warn_ratio=None)
    _core(cfg, "x" * 85)
    assert _bloat(cfg_off) == ""                             # ratio=None — раннего нуджа нет


# ── размер уроков: префиксы / исключения / override / счётчик / архив ─────────

def _lesson(cfg, name: str, size: int) -> None:
    (Path(cfg.memory_dir) / name).write_text("x" * size, encoding="utf-8")


def _bloat_file(cfg, name: str) -> str:
    ev = {"tool_name": "Write", "tool_input": {"file_path": str(Path(cfg.memory_dir) / name)}}
    return H.ev_bloat_check(ev, cfg)


def test_size_warn_prefixes_narrowing(cfg) -> None:
    cfg2 = replace(cfg, feedback_warn_bytes=100, size_warn_prefixes=("feedback",))
    _lesson(cfg, "feedback_a.md", 200)
    _lesson(cfg, "reference_b.md", 200)
    assert "feedback_a.md" in _bloat_file(cfg2, "feedback_a.md")
    assert _bloat_file(cfg2, "reference_b.md") == ""   # reference вне префиксов — тихо


def test_size_exempt_and_override(cfg) -> None:
    cfg2 = replace(cfg, feedback_warn_bytes=100,
                   size_exempt=("feedback_registry.md",),
                   size_override={"feedback_big.md": 1000})
    _lesson(cfg, "feedback_registry.md", 500)
    _lesson(cfg, "feedback_big.md", 500)
    _lesson(cfg, "feedback_plain.md", 500)
    assert _bloat_file(cfg2, "feedback_registry.md") == ""    # exempt
    assert _bloat_file(cfg2, "feedback_big.md") == ""         # 500 < override 1000
    assert "feedback_plain.md" in _bloat_file(cfg2, "feedback_plain.md")  # 500 > 100


def test_precedent_count_warn(cfg) -> None:
    # Даты — относительно сегодня: захардкоженные однажды перешагивают
    # precedent_archive_days, авто-архив съедает блоки ДО подсчёта и тест протухает
    # (дата-бомба: упал 2026-07-02 на датах 2026-06-01..03 при пороге 30 дней).
    cfg2 = replace(cfg, feedback_warn_bytes=10 ** 9, precedent_count_warn=3)
    days = [datetime.date.today() - datetime.timedelta(days=i) for i in (1, 2, 3)]
    blocks = "".join(f"**Прецедент {d:%Y-%m-%d}:** разбор\n\n" for d in days)
    (Path(cfg.memory_dir) / "feedback_p.md").write_text(blocks, encoding="utf-8")
    assert "3" in _bloat_file(cfg2, "feedback_p.md")          # ≥3 живых блока
    two = "".join(f"**Прецедент {d:%Y-%m-%d}:** разбор\n\n" for d in days[:2])
    (Path(cfg.memory_dir) / "feedback_p.md").write_text(two, encoding="utf-8")
    assert "Precedent" not in _bloat_file(cfg2, "feedback_p.md")  # 2 блока — без счётчика


def test_archive_files_skipped(cfg) -> None:
    arc = Path(cfg.memory_dir) / cfg.archive_dir_name
    arc.mkdir()
    (arc / "feedback_old.md").write_text("x" * 10000, encoding="utf-8")
    ev = {"tool_name": "Write",
          "tool_input": {"file_path": str(arc / "feedback_old.md")}}
    assert H.ev_bloat_check(ev, cfg) == ""   # архив не предупреждаем


# ── конфигурируемые маркеры CATALOG (узнаём существующий файл) ─────────────────

def test_configurable_catalog_markers_preserve_preamble(cfg) -> None:
    # проект задаёт свои (напр. локализованные) маркеры; существующий файл с НИМИ
    # должен распознаваться — преамбула сохраняется, дубля маркера нет.
    start = "<!-- НАЧАЛО-ИНДЕКСА -->"
    end = "<!-- КОНЕЦ-ИНДЕКСА -->"
    cfg2 = replace(cfg, catalog_auto_start=start, catalog_auto_end=end,
                   catalog_preamble="# Шапка по умолчанию")
    cat = Path(cfg.memory_dir) / cfg.catalog_file
    cat.write_text(f"# Моя рукописная шапка\n\n{start}\nстарый индекс\n{end}\n", encoding="utf-8")
    text, _ = CG.build_catalog(cfg.memory_dir, cfg2)
    assert "# Моя рукописная шапка" in text          # преамбула сохранена
    assert text.count(start) == 1                     # ровно один стартовый маркер (нет дубля)
    assert "# Шапка по умолчанию" not in text         # дефолтная преамбула не подставлена


# ── проектные ноты SessionStart ───────────────────────────────────────────────

def test_session_start_notes_emitted(cfg) -> None:
    cfg2 = replace(cfg, session_start_notes=("ЛОГИН: только через скрипт", "вторая нота"))
    out = H.ev_session_start({}, cfg2)
    assert "ЛОГИН: только через скрипт" in out and "вторая нота" in out


# ── override доходит до модулей-стражей (доказывает, что cfg прокинут в msg) ───

def test_marker_guard_respects_message_override(cfg) -> None:
    from claude_memory import session_marker_guard as SG
    cfg2 = replace(cfg, messages={"marker.violation_reason": "СВОЁ правило, лимит {limit}"})
    path = f"/m/{cfg.session_lessons_file}"
    r = SG.violation_reason("Write", {"file_path": path, "content": "<!-- 2026-06-17 " + "x" * 250 + " -->"}, cfg2)
    assert r == "СВОЁ правило, лимит 200"


def test_model_guard_respects_message_override(cfg) -> None:
    from claude_memory import subagent_model_guard as G
    cfg2 = replace(cfg, messages={"model_guard.strongest_model_reason": "СИЛЬНАЯ модель {model}"})
    r = G.decide_strongest("Agent", {"subagent_type": "general-purpose", "model": "claude-opus-4-8"}, cfg2)
    assert r == "СИЛЬНАЯ модель claude-opus-4-8"
