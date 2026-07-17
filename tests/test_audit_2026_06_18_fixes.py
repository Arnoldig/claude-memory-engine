"""Тесты на правки аудита 2026-06-18 (claude-memory-engine).

Каждый тест соответствует одной находке аудита: фиксирует исправленное поведение,
чтобы регресс не вернулся.
"""
from __future__ import annotations

import datetime
import json
from dataclasses import replace
from pathlib import Path

from conftest import write_lesson


# ── F1: msg() fail-soft на битом плейсхолдере override ─────────────────────────

def test_msg_bad_override_placeholder_falls_back_to_default(cfg) -> None:
    from claude_memory.messages import msg
    # override с НЕвалидным плейсхолдером (как было `{len(cards)}` в проде) не должен
    # ронять — деградируем на дефолт библиотеки (там {card_count}).
    cfg2 = replace(cfg, messages={"precedent.index_preamble": "битый {len(cards)}"})
    out = msg(cfg2, "precedent.index_preamble", archive_name="a", card_count=5, extract_cmd="c")
    assert "5 cards" in out          # подставился дефолт
    assert "{" not in out            # сырой плейсхолдер не утёк


def test_msg_unknown_key_returns_key(cfg) -> None:
    from claude_memory.messages import msg
    assert msg(cfg, "no.such.key.xyz") == "no.such.key.xyz"


def test_msg_bad_placeholder_no_default_returns_raw(cfg) -> None:
    from claude_memory.messages import msg
    # ключа нет в дефолтах + битый плейсхолдер → отдаём сырой шаблон, НЕ падаем
    cfg2 = replace(cfg, messages={"custom.k": "raw {nope}"})
    assert msg(cfg2, "custom.k", x=1) == "raw {nope}"


# ── F2: staleness не метит applies_to в skip-каталоге (.claude/) как протухшую ──

def test_staleness_skipdir_binding_not_false_broken(cfg, tmp_path) -> None:
    from claude_memory import staleness
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "cme_hook.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (tmp_path / "real.txt").write_text("x", encoding="utf-8")  # чтобы repo_files != []
    cfg2 = replace(cfg, staleness_skip_dirs=(".claude",))
    write_lesson(cfg.memory_dir, "feedback_live.md", description="d",
                 applies_to="\n  - .claude/hooks/cme_hook.sh")
    write_lesson(cfg.memory_dir, "feedback_dead.md", description="d",
                 applies_to="\n  - app/gone_xyz.py")
    _, broken = staleness.scan(cfg2)
    names = [n for n, _ in broken]
    assert "feedback_live.md" not in names   # реально существует под .claude/
    assert "feedback_dead.md" in names       # контроль: реально мёртвая привязка


# ── F3: count_real_precedents видит дату в скобках/после слага ──────────────────

def test_count_real_precedents_paren_and_slug_dates(cfg) -> None:
    from claude_memory.memory_archive import count_real_precedents
    text = (
        "**Precedent #61 (2026-04-27):** a\n\n"
        "**Precedent 2026-05-02:** b\n\n"
        "**Precedent FW-1 (#audit-2026-06-11-F, 2026-06-12):** c\n"
    )
    assert count_real_precedents(text, cfg) == 3


# ── F4: _link_re уважает archive_dir_name → идемпотентность при кастомном каталоге

def test_archive_idempotent_custom_archive_dir(cfg) -> None:
    from claude_memory import memory_archive as ma
    cfg2 = replace(cfg, archive_dir_name="vault")
    p = Path(cfg.memory_dir) / "feedback_p.md"
    p.write_text("---\nname: p\n---\nintro\n\n**Precedent 2020-01-15:** old.\n", encoding="utf-8")
    today = datetime.date(2026, 6, 18)
    r1 = ma.archive_old_precedents(p, today=today, threshold_days=30, cfg=cfg2)
    r2 = ma.archive_old_precedents(p, today=today, threshold_days=30, cfg=cfg2)
    assert r1.archived_count == 1
    assert r2.archived_count == 0           # pointer распознан → второго архива нет
    arc = (Path(cfg.memory_dir) / "vault" / "precedents-2020-Q1.md").read_text(encoding="utf-8")
    assert arc.count("old.") == 1


# ── F5: _precedent_head_re берёт дату КАРТОЧКИ (последнюю), не дату слага ───────

def test_precedent_head_uses_card_date_not_slug(cfg) -> None:
    from claude_memory.memory_archive import _precedent_head_re
    m = _precedent_head_re(cfg).search(
        "**Precedent FW-1 (#audit-2026-06-11-F, 2026-06-12, x):** body"
    )
    assert m and m.group(1) == "2026-06-12"


def test_archive_card_date_picks_right_quarter(cfg) -> None:
    from claude_memory import memory_archive as ma
    p = Path(cfg.memory_dir) / "feedback_q.md"
    # слаг-дата 2026-03-30 (Q1), реальная дата карточки 2026-04-02 (Q2)
    p.write_text(
        "---\nname: q\n---\nintro\n\n"
        "**Precedent CROSS-1 (#audit-2026-03-30-X, 2026-04-02):** body.\n",
        encoding="utf-8",
    )
    ma.archive_old_precedents(p, today=datetime.date(2026, 12, 1), threshold_days=30, cfg=cfg)
    assert (Path(cfg.memory_dir) / "archive" / "precedents-2026-Q2.md").exists()
    assert not (Path(cfg.memory_dir) / "archive" / "precedents-2026-Q1.md").exists()


# ── F6: многоабзацная карточка архивируется целиком (хвост не осиротеет) ────────

def test_archive_multiparagraph_card_full(cfg) -> None:
    from claude_memory import memory_archive as ma
    p = Path(cfg.memory_dir) / "feedback_mp.md"
    p.write_text(
        "---\nname: mp\n---\nintro\n\n"
        "**Precedent 2020-01-15 — тема:** para1\n\npara2 details\n\n- item A\n",
        encoding="utf-8",
    )
    ma.archive_old_precedents(p, today=datetime.date(2026, 6, 18), threshold_days=30, cfg=cfg)
    src = p.read_text(encoding="utf-8")
    arc = (Path(cfg.memory_dir) / "archive" / "precedents-2020-Q1.md").read_text(encoding="utf-8")
    assert "moved to" in src and "para2 details" not in src and "item A" not in src
    assert "para2 details" in arc and "item A" in arc


def test_archive_multiparagraph_stops_at_next_card(cfg) -> None:
    from claude_memory import memory_archive as ma
    p = Path(cfg.memory_dir) / "feedback_two.md"
    p.write_text(
        "---\nname: two\n---\nintro\n\n"
        "**Precedent 2020-01-15:** old1\n\ntail1\n\n"
        "**Precedent 2020-02-20:** old2\n\ntail2\n",
        encoding="utf-8",
    )
    ma.archive_old_precedents(p, today=datetime.date(2026, 6, 18), threshold_days=30, cfg=cfg)
    arc = (Path(cfg.memory_dir) / "archive" / "precedents-2020-Q1.md").read_text(encoding="utf-8")
    # tail1 ушёл со своей карточкой, tail2 — со своей; не перепутались
    assert "tail1" in arc and "tail2" in arc and "old1" in arc and "old2" in arc


# ── F7: дедуп — повторный прогон после крах-окна не задваивает архив ────────────

def test_archive_dedup_on_crash_rerun(cfg) -> None:
    from claude_memory import memory_archive as ma
    p = Path(cfg.memory_dir) / "feedback_d.md"
    original = "---\nname: d\n---\nintro\n\n**Precedent 2020-01-15:** card body.\n"
    p.write_text(original, encoding="utf-8")
    today = datetime.date(2026, 6, 18)
    ma.archive_old_precedents(p, today=today, threshold_days=30, cfg=cfg)
    arc_path = Path(cfg.memory_dir) / "archive" / "precedents-2020-Q1.md"
    assert arc_path.read_text(encoding="utf-8").count("card body") == 1
    # имитируем крах: источник откатился к доархивной версии, архив уже содержит блок
    p.write_text(original, encoding="utf-8")
    ma.archive_old_precedents(p, today=today, threshold_days=30, cfg=cfg)
    assert arc_path.read_text(encoding="utf-8").count("card body") == 1   # не задвоилось


# ── F8: extract_card на пустом запросе не вываливает весь архив ─────────────────

def test_extract_card_empty_query_returns_empty() -> None:
    from claude_memory.precedent_index import extract_card
    text = "## 2026-01-01 (x)\n\nbody1\n\n## 2026-02-02 (y)\n\nbody2\n"
    assert extract_card(text, "") == ""
    assert extract_card(text, "   ") == ""
    assert "body1" in extract_card(text, "2026-01-01")


# ── F9: CLI без пути-архива → usage, а не сырой IndexError ──────────────────────

def test_cli_flag_without_archive_shows_usage(cfg, monkeypatch, capsys) -> None:
    from claude_memory import precedent_index as pi
    monkeypatch.setattr(pi, "get_config", lambda: cfg)
    for flag in ("--extract", "--add-header", "--index"):
        monkeypatch.setattr("sys.argv", ["prog", flag])
        pi.main()  # не должно бросить IndexError
        assert "usage" in capsys.readouterr().out.lower()


# ── F10: ретривер ловит dotfile-путь (.claude/…) в запросе ─────────────────────

def test_path_lessons_finds_dotfile_path(cfg) -> None:
    from claude_memory import memory_retrieve as mr
    cfg2 = replace(cfg, watched_dirs=("app",), retrieve_extensions=("sh", "py"))
    write_lesson(cfg.memory_dir, "feedback_hook.md", description="hook lesson",
                 applies_to="\n  - .claude/hooks/cme_hook.sh")
    found = mr.path_lessons("правлю .claude/hooks/cme_hook.sh сейчас", cfg2)
    assert "feedback_hook.md" in found


# ── F11/F13: длинный frontmatter не теряется (окно чтения убрано) ───────────────

def test_long_frontmatter_applies_to_still_parsed(cfg) -> None:
    from claude_memory.applies_to import find_lessons_for_path
    write_lesson(cfg.memory_dir, "feedback_long.md",
                 description="x" * 5000, applies_to="\n  - app/special.py")
    assert find_lessons_for_path("app/special.py", cfg)


def test_long_frontmatter_retriever_reads_body(cfg) -> None:
    from claude_memory.memory_retrieve import read_fields
    p = write_lesson(cfg.memory_dir, "feedback_lb.md",
                     description="y" * 5000, body="уникальноетелослово")
    _, desc, _, body = read_fields(str(p))
    assert desc.startswith("y")            # длинное описание прочитано
    assert "уникальноетелослово" in body   # тело за длинным frontmatter не потеряно


# ── F12: removeprefix вместо lstrip — нет ложного dotless-кандидата ─────────────

def test_candidates_removeprefix_not_lstrip(cfg) -> None:
    from claude_memory.applies_to import _candidates
    c = _candidates(".github/workflows/ci.yml", "/nonexistent")
    assert ".github/workflows/ci.yml" in c
    assert "github/workflows/ci.yml" not in c     # ложный кандидат не порождён
    c2 = _candidates("./app/x.py", "/nonexistent")
    assert "app/x.py" in c2                        # ведущий ./ всё ещё снимается


# ── F14: task_lesson_recorded — точное #id с границей справа ────────────────────

def test_task_lesson_recorded_word_boundary(cfg) -> None:
    from claude_memory.stop_check import task_lesson_recorded
    (Path(cfg.memory_dir) / "feedback_num.md").write_text("see #580 here\n", encoding="utf-8")
    assert task_lesson_recorded(cfg, "58") is False     # #580 НЕ матчит #58
    assert task_lesson_recorded(cfg, "580") is True
    (Path(cfg.memory_dir) / "feedback_slug.md").write_text("Closes #memory-lib-cutover\n", encoding="utf-8")
    assert task_lesson_recorded(cfg, "memory-lib-cutover") is True
    assert task_lesson_recorded(cfg, "memory-lib") is False   # префикс слага не матчит


# ── F15: newest_lesson_mtime учитывает ВСЕ префиксы (reference_/project_) ───────

def test_newest_lesson_mtime_all_prefixes(cfg) -> None:
    import os
    from claude_memory.stop_check import newest_lesson_mtime
    fb = Path(cfg.memory_dir) / "feedback_old.md"; fb.write_text("x", encoding="utf-8")
    ref = Path(cfg.memory_dir) / "reference_new.md"; ref.write_text("y", encoding="utf-8")
    os.utime(fb, (1_000_000_000, 1_000_000_000))
    os.utime(ref, (2_000_000_000, 2_000_000_000))
    assert newest_lesson_mtime(cfg) == 2_000_000_000.0   # reference новее — учтён
