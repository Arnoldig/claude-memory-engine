"""Тесты CLI `claude-memory` (вариант C: pip-пакет + развёртывание одной командой)."""
from __future__ import annotations

import json
import shlex
import stat
import sys
from pathlib import Path

import pytest

from claude_memory import __version__, cli
from claude_memory import config as cfgmod
from claude_memory import installer as I


@pytest.fixture(autouse=True)
def _stub_import_ok(monkeypatch):
    """Изолируем проверки файлов от того, виден ли пакет под subprocess-интерпретатором
    (зависит от cwd запуска pytest). Реальную ветку «не импортируется» тестируем отдельно."""
    monkeypatch.setattr(cli, "_import_ok", lambda exe: True)


def _settings_commands(settings_path: Path) -> list:
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    out = []
    for groups in data.get("hooks", {}).values():
        for g in groups:
            for h in g.get("hooks", []):
                out.append(h.get("command", ""))
    return out


def _init(project: Path, memory: Path) -> int:
    return cli.main(["init", str(project), str(memory)])


def test_init_creates_wrapper_config_settings(tmp_path):
    project, memory = tmp_path / "proj", tmp_path / "mem"
    assert _init(project, memory) == 0

    wrapper = project / ".claude" / "hooks" / "cme_hook.sh"
    config = project / ".claude" / "claude-memory.config.json"
    settings = project / ".claude" / "settings.json"

    assert wrapper.is_file()
    assert wrapper.stat().st_mode & stat.S_IXUSR            # исполняемая

    body = wrapper.read_text(encoding="utf-8")
    assert "claude_memory.hooks_cli" in body               # зовёт диспетчер
    assert sys.executable in body                          # зафиксированный интерпретатор
    assert "memory_engine" not in body                    # pip-режим: движок НЕ вшит
    assert "PYTHONPATH" not in body                        # и PYTHONPATH не ставим

    cfg = json.loads(config.read_text(encoding="utf-8"))
    assert cfg["memory_dir"] == str(memory.resolve())
    assert cfg["project_root"] == str(project.resolve())

    cmds = _settings_commands(settings)
    assert len([c for c in cmds if "cme_hook.sh" in c]) == len(I.HOOK_REGISTRATIONS)
    assert f"bash {wrapper} retrieve" in cmds

    assert memory.is_dir()                                  # каталог памяти создан


def test_wrapper_interpreter_is_shell_quoted(tmp_path):
    project, memory = tmp_path / "proj", tmp_path / "mem"
    _init(project, memory)
    body = (project / ".claude" / "hooks" / "cme_hook.sh").read_text(encoding="utf-8")
    assert f"exec {shlex.quote(sys.executable)} -m claude_memory.hooks_cli" in body


def test_init_idempotent(tmp_path):
    project, memory = tmp_path / "proj", tmp_path / "mem"
    assert _init(project, memory) == 0
    settings = project / ".claude" / "settings.json"
    first = _settings_commands(settings)
    assert _init(project, memory) == 0                     # повтор
    assert _settings_commands(settings) == first           # дублей нет


def test_init_preserves_existing_config_and_foreign_hooks(tmp_path):
    project, memory = tmp_path / "proj", tmp_path / "mem"
    claude = project / ".claude"
    claude.mkdir(parents=True)
    config = claude / "claude-memory.config.json"
    config.write_text(
        json.dumps({"memory_dir": "/custom/mem", "project_root": "/custom"}), encoding="utf-8"
    )
    settings = claude / "settings.json"
    settings.write_text(
        json.dumps({"hooks": {"UserPromptSubmit": [
            {"matcher": "", "hooks": [{"type": "command", "command": "bash /other/foo.sh"}]}
        ]}}),
        encoding="utf-8",
    )

    assert _init(project, memory) == 0
    assert json.loads(config.read_text())["memory_dir"] == "/custom/mem"   # чужой конфиг цел
    cmds = _settings_commands(settings)
    assert "bash /other/foo.sh" in cmds                                    # чужой хук цел
    assert any("cme_hook.sh retrieve" in c for c in cmds)                  # наш добавлен


def test_init_warns_when_package_not_importable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_import_ok", lambda exe: False)
    rc = _init(tmp_path / "proj", tmp_path / "mem")
    assert rc == 1
    err = capsys.readouterr().err
    assert "claude_memory" in err and "pip install" in err


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 0
    assert "init" in capsys.readouterr().out


def test_config_get(tmp_path, capsys, monkeypatch):
    project, memory = tmp_path / "proj", tmp_path / "mem"
    _init(project, memory)
    config = project / ".claude" / "claude-memory.config.json"
    monkeypatch.setenv("CLAUDE_MEMORY_CONFIG", str(config))
    cfgmod.reset_cache()
    try:
        assert cli.main(["config", "get", "memory_dir"]) == 0
        assert str(memory.resolve()) in capsys.readouterr().out
    finally:
        cfgmod.reset_cache()


def test_doctor_clean_config(tmp_path, monkeypatch):
    project, memory = tmp_path / "proj", tmp_path / "mem"
    _init(project, memory)
    config = project / ".claude" / "claude-memory.config.json"
    monkeypatch.setenv("CLAUDE_MEMORY_CONFIG", str(config))
    cfgmod.reset_cache()
    try:
        assert cli.main(["doctor"]) == 0
    finally:
        cfgmod.reset_cache()


def test_uninstall_removes_deployed_files_and_keeps_lessons(tmp_path):
    project, memory = tmp_path / "proj", tmp_path / "mem"
    assert _init(project, memory) == 0
    claude = project / ".claude"
    wrapper = claude / "hooks" / "cme_hook.sh"
    config = claude / "claude-memory.config.json"
    settings = claude / "settings.json"
    lesson = memory / "feedback_demo.md"               # урок в памяти — трогать нельзя
    lesson.write_text("demo", encoding="utf-8")

    assert cli.main(["uninstall", str(project)]) == 0

    assert not wrapper.exists()                         # обёртка удалена
    assert not config.exists()                          # конфиг удалён
    assert not any("cme_hook.sh" in c for c in _settings_commands(settings))  # наши хуки сняты
    assert lesson.exists()                              # уроки не тронуты
    assert memory.is_dir()


def test_uninstall_preserves_foreign_hooks(tmp_path):
    project, memory = tmp_path / "proj", tmp_path / "mem"
    claude = project / ".claude"
    claude.mkdir(parents=True)
    settings = claude / "settings.json"
    settings.write_text(
        json.dumps({"hooks": {"UserPromptSubmit": [
            {"matcher": "", "hooks": [{"type": "command", "command": "bash /other/foo.sh"}]}
        ]}}),
        encoding="utf-8",
    )
    assert _init(project, memory) == 0
    assert cli.main(["uninstall", str(project)]) == 0
    cmds = _settings_commands(settings)
    assert "bash /other/foo.sh" in cmds                 # чужой хук уцелел
    assert not any("cme_hook.sh" in c for c in cmds)    # наши сняты


def test_uninstall_removes_vendored_engine(tmp_path):
    # имитируем вариант A: вшитую копию движка в .claude/memory_engine
    project, memory = tmp_path / "proj", tmp_path / "mem"
    _init(project, memory)
    engine = project / ".claude" / "memory_engine" / "claude_memory"
    engine.mkdir(parents=True)
    (engine / "__init__.py").write_text("", encoding="utf-8")
    assert cli.main(["uninstall", str(project)]) == 0
    assert not (project / ".claude" / "memory_engine").exists()


def test_uninstall_on_clean_project_is_safe(tmp_path, capsys):
    project = tmp_path / "proj"
    project.mkdir()
    assert cli.main(["uninstall", str(project)]) == 0
    assert "nothing to remove" in capsys.readouterr().out


def test_uninstall_tolerates_non_object_files(tmp_path):
    # settings.json и config.json — валидный JSON, но не объект: uninstall не падает
    project, memory = tmp_path / "proj", tmp_path / "mem"
    _init(project, memory)
    claude = project / ".claude"
    (claude / "settings.json").write_text("[]", encoding="utf-8")
    (claude / "claude-memory.config.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert cli.main(["uninstall", str(project)]) == 0
    assert not (claude / "hooks" / "cme_hook.sh").exists()        # обёртка удалена
    assert not (claude / "claude-memory.config.json").exists()    # «странный» конфиг удалён
