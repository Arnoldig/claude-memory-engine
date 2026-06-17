"""applies_to работает одинаково в worktree и вне его (#memory-lib-cutover).

ЧеКи (и любой проект на worktree-per-task) правит файлы внутри
`<main>/.claude/worktrees/<wt>/…`. Регресс был: (1) матчер релативизировал к главному
project_root → путь `.claude/worktrees/<wt>/app/x.py` не совпадал с глобом `app/*`;
(2) pre-edit гейт исключал ЛЮБОЙ `/.claude/` путь, убивая страж в worktree. Эти тесты
поднимают НАСТОЯЩИЙ git-worktree и проверяют, что оба места починены.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from claude_memory import applies_to as AT
from claude_memory import hooks_cli as H
from claude_memory.config import MemoryConfig

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(args, cwd) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True, env=_GIT_ENV)


def _has_git() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


def _setup(tmp_path: Path, glob_spec: str):
    """Главный git-репо + реальный worktree под .claude/worktrees/wt + урок с глобом."""
    main = tmp_path / "main"
    main.mkdir()
    _git(["init", "-q", "-b", "main"], main)
    _git(["commit", "--allow-empty", "-q", "-m", "init"], main)
    wt = main / ".claude" / "worktrees" / "wt"
    wt.parent.mkdir(parents=True)
    _git(["worktree", "add", "-q", str(wt)], main)
    mem = tmp_path / "memory"  # память отдельно от репо (как в ЧеКи)
    mem.mkdir()
    (mem / "feedback_app.md").write_text(
        f"---\ndescription: app rules\napplies_to: [{glob_spec}]\n---\n", encoding="utf-8"
    )
    cfg = MemoryConfig(memory_dir=str(mem), project_root=str(main))
    return main, wt, cfg


@pytest.mark.skipif(not _has_git(), reason="git required")
def test_find_lessons_matches_inside_and_outside_worktree(tmp_path: Path) -> None:
    main, wt, cfg = _setup(tmp_path, "app/*.py")
    # файл ВНУТРИ worktree: git-toplevel = корень worktree → rel `app/x.py` матчит `app/*.py`
    wt_target = wt / "app" / "x.py"
    wt_target.parent.mkdir(parents=True)
    wt_target.write_text("print(1)\n", encoding="utf-8")
    assert any(n == "feedback_app.md" for n, _ in AT.find_lessons_for_path(str(wt_target), cfg))
    # файл в ГЛАВНОМ дереве — тоже матчит (rel к project_root)
    main_target = main / "app" / "y.py"
    main_target.parent.mkdir(parents=True)
    main_target.write_text("x\n", encoding="utf-8")
    assert any(n == "feedback_app.md" for n, _ in AT.find_lessons_for_path(str(main_target), cfg))


@pytest.mark.skipif(not _has_git(), reason="git required")
def test_pre_edit_gate_fires_in_worktree_but_skips_tooling(tmp_path: Path) -> None:
    # глоб `*.py` совпал бы с ЛЮБЫМ .py-путём (fnmatch '*' матчит и слэши) — так что
    # «тишина» на tooling-файле доказывает именно ИСКЛЮЧЕНИЕ, а не отсутствие совпадения.
    main, wt, cfg = _setup(tmp_path, "*.py")
    td = str(tmp_path / "td")
    # (1) проектный файл в worktree → страж срабатывает
    wt_target = wt / "app" / "x.py"
    wt_target.parent.mkdir(parents=True)
    wt_target.write_text("x\n", encoding="utf-8")
    r = H.ev_pre_edit_guard(
        {"tool_name": "Edit", "tool_input": {"file_path": str(wt_target), "new_string": "y"}},
        cfg, "s1", td,
    )
    assert r and "applies-to-gate" in r
    # (2) служебный .claude/hooks файл (НЕ worktree) → страж молчит, хотя глоб совпал бы
    tooling = main / ".claude" / "hooks" / "z.py"
    tooling.parent.mkdir(parents=True)
    tooling.write_text("x\n", encoding="utf-8")
    r2 = H.ev_pre_edit_guard(
        {"tool_name": "Edit", "tool_input": {"file_path": str(tooling), "new_string": "y"}},
        cfg, "s2", td,
    )
    assert r2 is None
