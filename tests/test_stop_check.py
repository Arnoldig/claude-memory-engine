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


# ── привратник закрытия задачи ───────────────────────────────────────────────

def test_extract_closed_task_numeric_and_slug(cfg) -> None:
    p = cfg.task_close_pattern
    assert SC.extract_closed_task("fix: Closes #58", p) == "58"
    assert SC.extract_closed_task("docs: Fixes #memory-lib-cutover", p) == "memory-lib-cutover"
    assert SC.extract_closed_task("just a normal commit", p) is None


def test_task_lesson_recorded_in_lesson_file(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_x.md", description="про #widget-42 и решение")
    assert SC.task_lesson_recorded(cfg, "widget-42") is True
    assert SC.task_lesson_recorded(cfg, "nope-99") is False


def test_task_lesson_recorded_in_archive(cfg) -> None:
    arc = Path(cfg.memory_dir) / "archive"
    arc.mkdir()
    (arc / "precedents-2026-Q2.md").write_text("## 2026-06-17 закрыта #task-7\n", encoding="utf-8")
    assert SC.task_lesson_recorded(cfg, "task-7") is True


def _git_commit(repo: Path, msg: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", msg], cwd=repo, check=True, env=env)


def test_closure_reminder_blocks_when_no_lesson(cfg, tmp_path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "docs: Closes #task-9")
    msg = SC.closure_reminder(cfg, str(repo))
    assert msg and "task-close-gate" in msg and "task-9" in msg


def test_closure_reminder_passes_when_lesson_exists(cfg, tmp_path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "docs: Closes #task-9")
    write_lesson(cfg.memory_dir, "feedback_done.md", description="урок про #task-9")
    assert SC.closure_reminder(cfg, str(repo)) is None


def test_closure_gate_disabled(cfg, tmp_path) -> None:
    from dataclasses import replace
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "docs: Closes #task-9")
    assert SC.closure_reminder(replace(cfg, task_close_lesson_gate=False), str(repo)) is None


def test_closure_reminder_non_closing_commit_is_none(cfg, tmp_path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "feat: ordinary work, no closure")
    assert SC.closure_reminder(cfg, str(repo)) is None
