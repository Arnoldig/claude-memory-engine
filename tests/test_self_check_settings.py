"""Самодиагностика расхождений с настройками хозяина (Claude Code).

Класс дефекта, который тут ловится: движок читает не ту папку, куда пишет авто-память, —
и молчит. Выглядит как «всё хорошо, уроков просто нет», а на деле стражи слепы и Stop
блокирует после каждого коммита. Именно этот сценарий был ПОВЕДЕНИЕМ ПО УМОЛЧАНИЮ до
0.10.0 (установщик ставил `~/.claude/memory`, куда не пишет никто).
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from claude_memory import self_check as SC
from conftest import write_lesson


def _settings(root: str, scope: str, data: dict) -> None:
    p = Path(root) / scope
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


# ── (1) авто-память выключена, а стражи включены — настоящий вечный тупик ────────

def test_auto_memory_off_with_gates_on_complains(cfg) -> None:
    _settings(cfg.project_root, ".claude/settings.json", {"autoMemoryEnabled": False})
    issues = SC.settings_issues(cfg)
    assert any("auto-memory is DISABLED" in i for i in issues)


def test_auto_memory_off_with_gates_off_is_silent(cfg) -> None:
    """Уроки писать некому — но и не требует никто. Это связная настройка, не дефект."""
    _settings(cfg.project_root, ".claude/settings.json", {"autoMemoryEnabled": False})
    cfg2 = replace(cfg, stop_lessons_enabled=False, task_close_lesson_gate=False)
    assert SC.settings_issues(cfg2) == []


def test_auto_memory_off_via_env(cfg, monkeypatch) -> None:
    from claude_memory import claude_code_env as E
    monkeypatch.setenv(E.DISABLE_ENV, "1")
    assert any("auto-memory is DISABLED" in i for i in SC.settings_issues(cfg))


# ── (2) явный autoMemoryDirectory ≠ memory_dir ──────────────────────────────────

def test_explicit_mismatch_complains(cfg, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    _settings(cfg.project_root, ".claude/settings.json",
              {"autoMemoryDirectory": str(other)})
    issues = SC.settings_issues(cfg)
    assert any("reading a directory nobody writes to" in i for i in issues)
    assert any(str(other) in i for i in issues)


def test_explicit_match_is_silent(cfg) -> None:
    _settings(cfg.project_root, ".claude/settings.json",
              {"autoMemoryDirectory": cfg.memory_dir})
    assert SC.settings_issues(cfg) == []


def test_explicit_match_via_symlink_is_silent(cfg, tmp_path: Path) -> None:
    """Сравнение через realpath: symlink на ту же папку — не расхождение."""
    link = tmp_path / "link-to-memory"
    link.symlink_to(cfg.memory_dir)
    _settings(cfg.project_root, ".claude/settings.json", {"autoMemoryDirectory": str(link)})
    assert SC.settings_issues(cfg) == []


# ── (3) memory_dir пуст, а рядом непустая папка авто-памяти ─────────────────────

def test_empty_memory_dir_with_lessons_elsewhere_complains(cfg, tmp_path: Path, monkeypatch) -> None:
    """Тот самый сценарий сломанного дефолта установщика: движок смотрит в пустоту,
    а уроки хозяина лежат в другой папке. Догадка подтверждена диском → жалуемся."""
    from claude_memory import claude_code_env as E

    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    auto = Path(E.default_auto_memory_dir(cfg.project_root))
    auto.mkdir(parents=True)
    write_lesson(str(auto), "kebab-lesson.md", name="k", description="d", type="project")

    issues = SC.settings_issues(cfg)   # memory_dir из фикстуры — пустой
    assert any("almost certainly pointed at the wrong directory" in i for i in issues)
    assert any(str(auto) in i for i in issues)


def test_not_complaining_when_engine_sees_lessons(cfg, tmp_path: Path, monkeypatch) -> None:
    """Движок видит хоть один урок → молчим. Возможен намеренно отдельный корпус, и
    жалоба тут была бы навязчивой."""
    from claude_memory import claude_code_env as E

    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    auto = Path(E.default_auto_memory_dir(cfg.project_root))
    auto.mkdir(parents=True)
    write_lesson(str(auto), "kebab-lesson.md", name="k", description="d", type="project")
    write_lesson(cfg.memory_dir, "my-own.md", name="m", description="d", type="project")

    assert SC.settings_issues(cfg) == []


def test_not_complaining_when_neighbour_empty(cfg, tmp_path: Path, monkeypatch) -> None:
    """Соседняя папка пуста → догадка НЕ подтверждена → молчим. Иначе жаловались бы по
    недокументированному правилу слага, то есть гадали вслух."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert SC.settings_issues(cfg) == []


# ── fail-open ───────────────────────────────────────────────────────────────────

def test_broken_settings_json_is_silent(cfg) -> None:
    p = Path(cfg.project_root) / ".claude" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ битый", encoding="utf-8")
    assert SC.settings_issues(cfg) == []


def test_no_settings_at_all_is_silent(cfg, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "nohome"))
    assert SC.settings_issues(cfg) == []


# ── отчёт для человека ──────────────────────────────────────────────────────────

def test_report_shows_paths_match_and_types(cfg) -> None:
    """Отчёт обязан отвечать на «что настроено», а не только на «что сломано»: при чистом
    конфиге жалоб нет, и проверить настройку человеку иначе нечем."""
    _settings(cfg.project_root, ".claude/settings.json",
              {"autoMemoryDirectory": cfg.memory_dir})
    write_lesson(cfg.memory_dir, "a-lesson.md", name="a", description="d", type="feedback")
    write_lesson(cfg.memory_dir, "b-lesson.md", name="b", description="d", type="user")

    text = "\n".join(SC.report(cfg))
    assert cfg.memory_dir in text
    assert "same directory    : yes" in text
    assert "lessons visible   : 2" in text
    assert "feedback: 1" in text and "user: 1" in text


def test_report_flags_mismatch_and_no_type(cfg, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    _settings(cfg.project_root, ".claude/settings.json", {"autoMemoryDirectory": str(other)})
    write_lesson(cfg.memory_dir, "no-type.md", name="n", description="d")

    text = "\n".join(SC.report(cfg))
    assert "same directory    : NO" in text
    assert "(no type): 1" in text


def test_report_marks_derived_path(cfg, tmp_path: Path, monkeypatch) -> None:
    """Невыведенный путь обязан быть помечен как догадка — молча выдавать её за истину
    нельзя: правило слага не задокументировано."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "nohome"))
    text = "\n".join(SC.report(cfg))
    assert "derived, not confirmed" in text
