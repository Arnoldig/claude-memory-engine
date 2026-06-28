"""Тесты стража устаревших уроков (stale_reconcile): метки сессии, кандидаты,
смысловой список, разовый блок на закрытии задачи, бэкстоп в _stale_pending."""
from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

from claude_memory import stale_reconcile as SR
from claude_memory import staleness
from conftest import write_lesson, RU_EN_CLOSE_PATTERN

SID = "sess-1"


def _git_commit(repo: Path, msg: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", msg], cwd=repo, check=True, env=env)


# ── метки и кандидаты ─────────────────────────────────────────────────────────

def test_shown_marker_roundtrip(tmp_path) -> None:
    td = str(tmp_path)
    marker = SR.applies_marker_path(SID, "/proj/app/x.py", td)
    SR.write_applies_marker(marker, "/proj/app/x.py", ["feedback_a.md", "feedback_b.md"])
    assert SR.gather_shown(SID, td) == {
        "feedback_a.md": {"/proj/app/x.py"},
        "feedback_b.md": {"/proj/app/x.py"},
    }


def test_old_marker_body_ignored(tmp_path) -> None:
    # старая метка с телом "1" (до обогащения JSON) не ломает сбор — fail-open
    td = str(tmp_path)
    d = SR.applies_gate_dir(SID, td)
    d.mkdir(parents=True)
    (d / "deadbeef").write_text("1", encoding="utf-8")
    assert SR.gather_shown(SID, td) == {}


def test_edited_lesson_excluded_from_candidates(tmp_path) -> None:
    td = str(tmp_path)
    m = SR.applies_marker_path(SID, "/proj/app/x.py", td)
    SR.write_applies_marker(m, "/proj/app/x.py", ["feedback_a.md", "feedback_b.md"])
    SR.record_edited_lesson(SID, "/mem/feedback_a.md", td)  # a тронут → исключаем
    cands = SR.candidates(SID, td)
    assert "feedback_a.md" not in cands
    assert cands["feedback_b.md"] == ["/proj/app/x.py"]


def test_edited_files_gather(tmp_path) -> None:
    td = str(tmp_path)
    SR.record_edited_file(SID, "/proj/app/marketing_consent.py", td)
    SR.record_edited_file(SID, "/proj/app/pay.py", td)
    assert sorted(SR.gather_edited_files(SID, td)) == [
        "/proj/app/marketing_consent.py", "/proj/app/pay.py",
    ]


# ── разовый блок на закрытии задачи ───────────────────────────────────────────

def test_gate_disabled_returns_none(cfg, tmp_path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "docs: Closes #task-9")
    m = SR.applies_marker_path("s", "/proj/app/x.py", str(tmp_path))
    SR.write_applies_marker(m, "/proj/app/x.py", ["feedback_a.md"])
    # дефолт stale_reconcile_gate=False → None даже при кандидатах
    assert SR.reconcile_reminder(cfg, str(repo), "s", str(tmp_path)) is None


def test_non_closing_commit_returns_none(cfg, tmp_path) -> None:
    cfg2 = replace(cfg, stale_reconcile_gate=True)
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "feat: ordinary work, no closure")
    m = SR.applies_marker_path("s", "/proj/app/x.py", str(tmp_path))
    SR.write_applies_marker(m, "/proj/app/x.py", ["feedback_a.md"])
    assert SR.reconcile_reminder(cfg2, str(repo), "s", str(tmp_path)) is None


def test_no_precise_candidates_returns_none(cfg, tmp_path) -> None:
    cfg2 = replace(cfg, stale_reconcile_gate=True)
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "docs: Closes #task-9")
    # показан feedback_a, но он же тронут → точных кандидатов нет → None
    m = SR.applies_marker_path("s", "/proj/app/x.py", str(tmp_path))
    SR.write_applies_marker(m, "/proj/app/x.py", ["feedback_a.md"])
    SR.record_edited_lesson("s", "/mem/feedback_a.md", str(tmp_path))
    assert SR.reconcile_reminder(cfg2, str(repo), "s", str(tmp_path)) is None


def test_blocks_once_then_passes(cfg, tmp_path) -> None:
    cfg2 = replace(cfg, stale_reconcile_gate=True)
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "docs: Closes #task-9")
    m = SR.applies_marker_path("s", "/proj/app/x.py", str(tmp_path))
    SR.write_applies_marker(m, "/proj/app/x.py", ["feedback_stale.md"])
    msg1 = SR.reconcile_reminder(cfg2, str(repo), "s", str(tmp_path))
    assert msg1 and "stale-reconcile-gate" in msg1
    assert "task-9" in msg1 and "feedback_stale.md" in msg1
    # разовость по (сессия, sha коммита): повтор → None
    assert SR.reconcile_reminder(cfg2, str(repo), "s", str(tmp_path)) is None


def test_reconcile_reminder_detects_russian_closure(cfg, tmp_path) -> None:
    # страж устаревших уроков срабатывает и на русской форме закрытия «#id закрыт» БЕЗ «Closes»
    # (проектный шаблон с двумя ветками). Регресс-замок к пропуску #audit-2026-06-28-G2 (b2f91b1).
    cfg2 = replace(cfg, stale_reconcile_gate=True, task_close_pattern=RU_EN_CLOSE_PATTERN)
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "docs(tracker): #audit-2026-06-28-G2 закрыт — A28 DONE")
    m = SR.applies_marker_path("s", "/proj/app/x.py", str(tmp_path))
    SR.write_applies_marker(m, "/proj/app/x.py", ["feedback_stale.md"])
    msg1 = SR.reconcile_reminder(cfg2, str(repo), "s", str(tmp_path))
    assert msg1 and "stale-reconcile-gate" in msg1
    assert "audit-2026-06-28-G2" in msg1 and "feedback_stale.md" in msg1


def test_related_lessons_in_message(cfg, tmp_path) -> None:
    # связанный по смыслу урок (без applies на тронутый файл) попадает в советный блок
    cfg2 = replace(cfg, stale_reconcile_gate=True, retrieve_threshold=0.3)
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "feat: marketing consent checkbox Closes #task-9")
    # точный кандидат (по applies-метке на некий файл)
    f1 = str(tmp_path / "app" / "x.py")
    m = SR.applies_marker_path("s", f1, str(tmp_path))
    SR.write_applies_marker(m, f1, ["feedback_stale.md"])
    # правленый файл, дающий смысловой запрос
    SR.record_edited_file("s", str(tmp_path / "app" / "marketing_consent.py"), str(tmp_path))
    # связанный урок в памяти с пересекающимися токенами «marketing/consent»
    write_lesson(cfg.memory_dir, "feedback_consent_rule.md",
                 name="marketing consent checkbox reappears on bump",
                 description="consent flow rule")
    msg1 = SR.reconcile_reminder(cfg2, str(repo), "s", str(tmp_path))
    assert msg1 and "feedback_consent_rule.md" in msg1
    assert "feedback_stale.md" in msg1  # точный список тоже на месте


def test_related_excludes_shown(cfg, tmp_path) -> None:
    # урок, который уже в показанных, НЕ дублируется в смысловом списке
    cfg2 = replace(cfg, stale_reconcile_gate=True, retrieve_threshold=0.3)
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "feat: marketing consent Closes #task-9")
    f1 = str(tmp_path / "app" / "marketing_consent.py")
    m = SR.applies_marker_path("s", f1, str(tmp_path))
    SR.write_applies_marker(m, f1, ["feedback_consent_rule.md"])
    SR.record_edited_file("s", f1, str(tmp_path))
    write_lesson(cfg.memory_dir, "feedback_consent_rule.md",
                 name="marketing consent checkbox", description="consent")
    msg1 = SR.reconcile_reminder(cfg2, str(repo), "s", str(tmp_path))
    # урок есть в точном списке ровно один раз; в советном блоке его быть не должно
    assert msg1 and msg1.count("feedback_consent_rule.md") == 1


def test_fail_open_when_fired_marker_unwritable(cfg, tmp_path, monkeypatch) -> None:
    # если метку разовости записать нельзя — fail-open в сторону НЕ-блокировки (не «стена»)
    cfg2 = replace(cfg, stale_reconcile_gate=True)
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "docs: Closes #task-9")
    td = str(tmp_path / "tmp")
    m = SR.applies_marker_path("s", "/proj/app/x.py", td)
    SR.write_applies_marker(m, "/proj/app/x.py", ["feedback_stale.md"])
    orig = Path.write_text

    def boom(self, *a, **k):
        if SR.RECONCILE_FIRED_PREFIX in self.name:
            raise OSError("no space left")
        return orig(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", boom)
    assert SR.reconcile_reminder(cfg2, str(repo), "s", td) is None


def test_type_hints_resolve() -> None:
    # регресс: аннотации модуля (в т.ч. Tuple) должны резолвиться get_type_hints
    import typing
    typing.get_type_hints(SR.related_lessons)
    typing.get_type_hints(SR.format_related)
    typing.get_type_hints(SR._candidates_from)


# ── бэкстоп: секция в _stale_pending ──────────────────────────────────────────

def test_pending_reconcile_section(cfg) -> None:
    ok = staleness.write_pending(
        cfg, stale=[], broken=[], archived=[],
        reconcile={"feedback_x.md": ["/proj/app/a.py", "/proj/app/b.py"]},
    )
    assert ok
    body = (Path(cfg.memory_dir) / staleness.STALE_FILE).read_text(encoding="utf-8")
    assert "feedback_x.md" in body and "a.py" in body and "b.py" in body


def test_pending_deleted_when_nothing(cfg) -> None:
    # пусто по всем секциям, включая reconcile → файл не создаётся / удаляется
    p = Path(cfg.memory_dir) / staleness.STALE_FILE
    p.write_text("старое", encoding="utf-8")
    assert staleness.write_pending(cfg, stale=[], broken=[], archived=[], reconcile=None) is False
    assert not p.exists()
