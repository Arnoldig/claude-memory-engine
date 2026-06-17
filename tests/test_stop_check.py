"""Тесты напоминания про уроки при завершении (Stop)."""
from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

from claude_memory import stop_check as SC
from conftest import write_lesson

AGE = 14400


def test_decide_table() -> None:
    # коммит свежий и новее урока → блок
    assert SC.decide(commit_ts=1000, feedback_ts=500, now_ts=1100, age_limit=AGE) is True
    # урок новее коммита → не блок
    assert SC.decide(commit_ts=1000, feedback_ts=2000, now_ts=1100, age_limit=AGE) is False
    # коммит слишком старый → не блок
    assert SC.decide(commit_ts=1000, feedback_ts=500, now_ts=1000 + AGE + 1, age_limit=AGE) is False
    # нет коммита → не блок
    assert SC.decide(commit_ts=0, feedback_ts=0, now_ts=100, age_limit=AGE) is False


def test_newest_lesson_mtime(cfg) -> None:
    assert SC.newest_lesson_mtime(cfg) == 0.0  # пусто
    f = write_lesson(cfg.memory_dir, "feedback_a.md", description="d")
    os.utime(f, (1000, 1000))
    assert SC.newest_lesson_mtime(cfg) == 1000.0


def test_disabled_returns_none(cfg) -> None:
    cfg2 = replace(cfg, stop_lessons_enabled=False)
    assert SC.should_remind(cfg2, cfg.project_root, now_ts=10_000_000_000) is None


def test_non_git_dir_returns_none(cfg, tmp_path) -> None:
    assert SC.last_commit_ts(str(tmp_path / "nope")) == 0
    assert SC.should_remind(cfg, str(tmp_path), now_ts=10_000_000_000) is None  # нет git → нет блока


def test_real_git_commit_triggers(cfg, tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=repo, check=True, env=env)
    # урок старее коммита (mtime в прошлом)
    f = write_lesson(cfg.memory_dir, "feedback_a.md", description="d")
    os.utime(f, (1000, 1000))
    commit_ts = SC.last_commit_ts(str(repo))
    assert commit_ts > 0
    msg = SC.should_remind(cfg, str(repo), now_ts=commit_ts + 10)
    assert msg and "stop-lessons" in msg
