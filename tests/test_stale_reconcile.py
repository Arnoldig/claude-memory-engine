"""Тесты стража устаревших уроков (stale_reconcile): метки сессии, кандидаты,
фраза закрытия, чек-лист итогов сессии, смысловой список, бэкстоп в _stale_pending."""
from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

from claude_memory import stale_reconcile as SR
from claude_memory import staleness
from conftest import write_lesson

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


# ── фраза закрытия сессии ─────────────────────────────────────────────────────

def test_close_phrase_case_insensitive(cfg) -> None:
    cfg2 = replace(cfg, session_close_pattern=r"\bdone\b", session_close_case_sensitive=False)
    assert SR.matches_close_phrase("all DONE here", cfg2)
    assert SR.matches_close_phrase("done", cfg2)


def test_close_phrase_case_sensitive(cfg) -> None:
    # регистрозависимость убирает ложные срабатывания на строчных формах частых слов
    cfg2 = replace(cfg, session_close_pattern=r"Туши свет|\bDone\b",
                   session_close_case_sensitive=True)
    assert SR.matches_close_phrase("Туши свет", cfg2)
    assert SR.matches_close_phrase("Done", cfg2)
    assert not SR.matches_close_phrase("i'm done with auth", cfg2)  # строчная — мимо
    assert not SR.matches_close_phrase("туши свет", cfg2)


def test_close_phrase_empty_or_bad_regex(cfg) -> None:
    assert not SR.matches_close_phrase("anything", replace(cfg, session_close_pattern=""))
    assert not SR.matches_close_phrase("", replace(cfg, session_close_pattern="x"))
    assert not SR.matches_close_phrase("x", replace(cfg, session_close_pattern="("))  # битый regex


# ── чек-лист итогов на фразу закрытия ─────────────────────────────────────────

def test_reconcile_on_close_gate_off_returns_none(cfg, tmp_path) -> None:
    cfg2 = replace(cfg, stale_reconcile_gate=False, session_close_pattern="done")
    assert SR.reconcile_on_close(cfg2, "done", str(tmp_path), "s", str(tmp_path)) is None


def test_reconcile_on_close_non_close_prompt_returns_none(cfg, tmp_path) -> None:
    cfg2 = replace(cfg, stale_reconcile_gate=True, session_close_pattern="Туши свет")
    assert SR.reconcile_on_close(cfg2, "обычная реплика", str(tmp_path), "s", str(tmp_path)) is None


def test_checklist_always_shown_even_when_clean(cfg, tmp_path) -> None:
    # на фразу закрытия чек-лист показывается ВСЕГДА, даже когда по всем пунктам пусто
    cfg2 = replace(cfg, stale_reconcile_gate=True, session_close_pattern="done")
    out = SR.reconcile_on_close(cfg2, "done", str(tmp_path), "s", str(tmp_path / "tmp"))
    assert out
    assert "no stale lessons" in out                       # «чисто»
    assert "Guards on" in out and "stale-lessons" in out    # список стражей вкл
    assert "Guards off" in out                              # и выключенных


def test_checklist_lists_candidates(cfg, tmp_path) -> None:
    td = str(tmp_path / "tmp")
    cfg2 = replace(cfg, stale_reconcile_gate=True, session_close_pattern="done")
    m = SR.applies_marker_path("s", "/proj/app/auth.py", td)
    SR.write_applies_marker(m, "/proj/app/auth.py", ["feedback_stale.md"])
    out = SR.reconcile_on_close(cfg2, "done", str(tmp_path), "s", td)
    assert out and "feedback_stale.md" in out
    assert "Re-verify" in out                # заголовок секции кандидатов
    assert "remaining: 1" in out


def test_checklist_counts_reconciled(cfg, tmp_path) -> None:
    td = str(tmp_path / "tmp")
    cfg2 = replace(cfg, stale_reconcile_gate=True, session_close_pattern="done")
    m = SR.applies_marker_path("s", "/proj/app/x.py", td)
    SR.write_applies_marker(m, "/proj/app/x.py", ["feedback_a.md", "feedback_b.md"])
    SR.record_edited_lesson("s", "/mem/feedback_a.md", td)  # один из двух актуализирован
    out = SR.reconcile_on_close(cfg2, "done", str(tmp_path), "s", td)
    assert "reconciled: 1" in out and "remaining: 1" in out


def test_checklist_related_decoupled_from_precise(cfg, tmp_path) -> None:
    # смысловой список появляется даже БЕЗ точных кандидатов (отвязан от precise)
    td = str(tmp_path)
    cfg2 = replace(cfg, stale_reconcile_gate=True, session_close_pattern="done",
                   retrieve_threshold=0.3)
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "feat: marketing consent checkbox")
    SR.record_edited_file("s", str(tmp_path / "app" / "marketing_consent.py"), td)
    write_lesson(cfg.memory_dir, "feedback_consent_rule.md",
                 name="marketing consent checkbox reappears on bump",
                 description="consent flow rule")
    out = SR.reconcile_on_close(cfg2, "done", str(repo), "s", td)
    assert out and "feedback_consent_rule.md" in out   # связанный по смыслу (без applies)
    assert "no stale lessons" in out                    # точных кандидатов нет


def test_type_hints_resolve() -> None:
    # регресс: аннотации модуля (в т.ч. Tuple) должны резолвиться get_type_hints
    import typing
    typing.get_type_hints(SR.related_lessons)
    typing.get_type_hints(SR.format_related)
    typing.get_type_hints(SR._candidates_from)
    typing.get_type_hints(SR.build_session_checklist)


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
