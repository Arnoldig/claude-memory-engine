"""Рассылка универсальных стражей потребителям (guards_sync).

Класс, который закрывается: страж, скопированный в проект РУКАМИ, — отставшая
копия дефолта. Она верна в день копирования и устаревает беззвучно: замерено
2026-08-17 — у одного потребителя копии пяти стражей совпадали с движком
байт-в-байт, при этом очередная правка стража уже лежала в главной папке
движка, то есть расхождение было вопросом часов. Никакой тест двух
репозиториев одновременно не сверял.

Теперь пакет возит эталоны стражей в claude_memory/guards/, команда
`claude-memory sync-guards` доносит их до проекта, а doctor (verbose) называет
дрейф вслух. Замок внутри самого движка — первый тест: копия в пакете обязана
быть байт-в-байт равной рабочему стражу репозитория.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from claude_memory import guards_sync as GS

REPO = Path(__file__).resolve().parents[1]


def test_package_guards_are_byte_identical_to_repo_hooks() -> None:
    """Эталон в пакете == рабочий страж репозитория. Иначе движок сам заводит
    ту самую отставшую копию, против которой этот механизм построен."""
    for name in GS.guard_names():
        packaged = (GS.GUARD_DIR / name).read_bytes()
        live = (REPO / ".claude" / "hooks" / name).read_bytes()
        assert packaged == live, f"{name}: копия в пакете разошлась с .claude/hooks"


def test_sync_installs_registers_and_is_idempotent(tmp_path) -> None:
    lines = GS.sync(str(tmp_path))
    hooks_dir = tmp_path / ".claude" / "hooks"
    for name in GS.guard_names():
        p = hooks_dir / name
        assert p.is_file() and (p.stat().st_mode & stat.S_IXUSR), name
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text("utf-8"))
    cmds = [h["command"]
            for groups in settings["hooks"].values()
            for g in groups for h in g["hooks"]]
    assert len(cmds) == len(GS.GUARD_REGISTRATIONS)
    for c in cmds:
        assert c.startswith('"$CLAUDE_PROJECT_DIR"/.claude/hooks/'), c
    assert lines  # что-то напечатано человеку
    # повторный запуск ничего не дублирует
    GS.sync(str(tmp_path))
    settings2 = json.loads((tmp_path / ".claude" / "settings.json").read_text("utf-8"))
    assert settings2 == settings


def test_sync_updates_drifted_copy(tmp_path) -> None:
    GS.sync(str(tmp_path))
    name = GS.guard_names()[0]
    target = tmp_path / ".claude" / "hooks" / name
    target.write_text("#!/bin/bash\n# отставшая копия\n", encoding="utf-8")
    GS.sync(str(tmp_path))
    assert target.read_bytes() == (GS.GUARD_DIR / name).read_bytes()


def test_sync_no_register_leaves_settings_alone(tmp_path) -> None:
    GS.sync(str(tmp_path), register=False)
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_sync_preserves_foreign_hooks(tmp_path) -> None:
    s = tmp_path / ".claude" / "settings.json"
    s.parent.mkdir(parents=True, exist_ok=True)
    foreign = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "bash my_own_guard.sh"}]}]}}
    s.write_text(json.dumps(foreign), encoding="utf-8")
    GS.sync(str(tmp_path))
    settings = json.loads(s.read_text("utf-8"))
    cmds = [h["command"]
            for groups in settings["hooks"].values()
            for g in groups for h in g["hooks"]]
    assert "bash my_own_guard.sh" in cmds
    assert len(cmds) == 1 + len(GS.GUARD_REGISTRATIONS)


# ── дрейф в самодиагностике (doctor, verbose) ─────────────────────────────────

def _cfg(root: Path):
    from claude_memory.config import MemoryConfig
    mem = root / "memory"
    mem.mkdir(exist_ok=True)
    return MemoryConfig(memory_dir=str(mem), project_root=str(root))


def test_drift_flagged_in_verbose_only(tmp_path) -> None:
    from claude_memory import self_check as SC
    GS.sync(str(tmp_path), register=False)
    name = GS.guard_names()[0]
    (tmp_path / ".claude" / "hooks" / name).write_text("#!/bin/bash\n# старая\n",
                                                       encoding="utf-8")
    cfg = _cfg(tmp_path)
    assert not any(name in w for w in SC.warnings(cfg))            # дёшево и тихо
    assert any(name in w for w in SC.warnings(cfg, verbose=True))  # doctor говорит


def test_identical_or_absent_copy_is_silent(tmp_path) -> None:
    from claude_memory import self_check as SC
    cfg = _cfg(tmp_path)
    # стражей нет вовсе → не установлены = выбор проекта, молчим
    assert not any("sync-guards" in w for w in SC.warnings(cfg, verbose=True))
    GS.sync(str(tmp_path), register=False)
    # свежие копии → молчим
    assert not any("sync-guards" in w for w in SC.warnings(cfg, verbose=True))


def test_registration_timeouts_match_repo_settings() -> None:
    """Таймауты регистраций зеркалят settings.json репозитория движка — двух
    разных ответов на «сколько ждать стража» быть не должно."""
    repo_settings = json.loads((REPO / ".claude" / "settings.json").read_text("utf-8"))
    repo_timeouts = {}
    for groups in repo_settings["hooks"].values():
        for g in groups:
            for h in g["hooks"]:
                base = os.path.basename(str(h.get("command", "")).strip('"'))
                repo_timeouts[base.split("/")[-1]] = h.get("timeout")
    for name, _event, _matcher, timeout in GS.GUARD_REGISTRATIONS:
        assert repo_timeouts.get(name) == timeout, name
