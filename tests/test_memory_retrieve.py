"""Тесты офлайн-ретривера."""
from __future__ import annotations

from claude_memory import memory_retrieve as MR
from conftest import write_lesson


def test_tokenize_stems_and_stopwords(cfg) -> None:
    assert MR.tokenize("payment Payments", cfg) == {"payme"}        # стем + lowercase
    assert MR.tokenize("the and for x", cfg) == set()                # стоп-слова + короткое


def test_score_ranks_rare_term_higher(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_a.md", name="kafka smz", description="payment kafka")
    write_lesson(cfg.memory_dir, "feedback_b.md", name="payment card", description="payment visa")
    write_lesson(cfg.memory_dir, "feedback_c.md", name="payment sbp", description="payment fast")
    res = MR.score_files("kafka", cfg)
    assert res and res[0][1] == "feedback_a.md"


def test_score_skips_core_and_catalog_and_underscore(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_a.md", name="kafka", description="kafka")
    write_lesson(cfg.memory_dir, "MEMORY.md", name="core", description="kafka")
    write_lesson(cfg.memory_dir, "CATALOG.md", name="cat", description="kafka")
    write_lesson(cfg.memory_dir, "_log.md", name="log", description="kafka")
    assert {b for _, b, _ in MR.score_files("kafka", cfg)} == {"feedback_a.md"}


def test_run_hook_silent_below_threshold(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_a.md", name="payment", description="payment")
    # частое/единственное слабое совпадение ниже порога → тишина
    assert MR.run("payment", hook_mode=True, cfg=cfg) == "" or "memory:retrieve" in MR.run("payment", True, cfg)


def test_run_hook_includes_path_lessons(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_chat.md",
                 description="правка чата", applies_to="[app/routers/chat.py]")
    out = MR.run("смотрю app/routers/chat.py", hook_mode=True, cfg=cfg)
    assert "feedback_chat.md" in out
    assert "applies_to" in out


def test_run_verbose_no_match(cfg) -> None:
    out = MR.run("zzzznevermatch", hook_mode=False, cfg=cfg)
    assert "нет совпадений" in out
