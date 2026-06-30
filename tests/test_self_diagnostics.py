"""Тесты самодиагностики (self_check) + новых сигналов пульса (wiki-ссылки, счётчик уроков)."""
from __future__ import annotations

from dataclasses import replace

from conftest import write_lesson


# ── self_check: сверка плейсхолдеров override ⊆ дефолта ─────────────────────────

def test_self_check_flags_bad_placeholder(cfg) -> None:
    from claude_memory import self_check
    cfg2 = replace(cfg, messages={"precedent.index_preamble": "битый {len(cards)} тут"})
    issues = self_check.message_placeholder_issues(cfg2)
    assert any(k == "precedent.index_preamble" and "len(cards)" in extra for k, extra in issues)
    assert self_check.run(cfg2)  # непустое предупреждение


def test_self_check_clean_config_silent(cfg) -> None:
    from claude_memory import self_check
    assert self_check.message_placeholder_issues(cfg) == []
    assert self_check.run(cfg) == ""


def test_self_check_valid_subset_ok(cfg) -> None:
    from claude_memory import self_check
    # override без плейсхолдеров (⊆ любого дефолта) — не нарушение
    cfg2 = replace(cfg, messages={"health.no_topic": "тем нет"})
    assert self_check.message_placeholder_issues(cfg2) == []


def test_self_check_ignores_orphan_key(cfg) -> None:
    from claude_memory import self_check
    # ключа нет в дефолтах → не предмет этой проверки (формат не ломается)
    cfg2 = replace(cfg, messages={"my.custom.key": "raw {whatever}"})
    assert self_check.message_placeholder_issues(cfg2) == []


def test_self_check_both_real_bugs_caught(cfg) -> None:
    from claude_memory import self_check
    cfg2 = replace(cfg, messages={
        "precedent.index_preamble": "{len(cards)}",
        "marker.violation_reason": "{lines_part} {limit}",
    })
    keys = {k for k, _ in self_check.message_placeholder_issues(cfg2)}
    assert keys == {"precedent.index_preamble", "marker.violation_reason"}


# ── битые [[wiki]]-ссылки между уроками ────────────────────────────────────────

def test_find_broken_wikilinks(cfg) -> None:
    from claude_memory import catalog_generate as cg
    write_lesson(cfg.memory_dir, "feedback_a.md", description="a", topic="workflow",
                 body="см. [[feedback_b]] и [[feedback_gone]]")
    write_lesson(cfg.memory_dir, "feedback_b.md", description="b", topic="workflow")
    broken = cg.find_broken_wikilinks(cfg.memory_dir, cfg)
    assert ("feedback_a.md", "feedback_gone") in broken
    assert not any(t == "feedback_b" for _, t in broken)   # файл есть → не битая


def test_wikilink_to_name_slug_ok(cfg) -> None:
    from claude_memory import catalog_generate as cg
    write_lesson(cfg.memory_dir, "feedback_x.md", name="feedback_special-slug",
                 description="x", topic="workflow", body="ссылка [[feedback_special-slug]]")
    assert cg.find_broken_wikilinks(cfg.memory_dir, cfg) == []   # цель = name-слаг, существует


def test_wikilink_with_md_extension_ok(cfg) -> None:
    from claude_memory import catalog_generate as cg
    # обе конвенции: `[[feedback_b]]` и `[[feedback_b.md]]` — на существующий файл не битые
    write_lesson(cfg.memory_dir, "feedback_a.md", description="a", topic="workflow",
                 body="с расширением [[feedback_b.md]] и без [[feedback_b]]")
    write_lesson(cfg.memory_dir, "feedback_b.md", description="b", topic="workflow")
    assert cg.find_broken_wikilinks(cfg.memory_dir, cfg) == []


def test_wikilink_ignores_non_lesson_refs(cfg) -> None:
    from claude_memory import catalog_generate as cg
    write_lesson(cfg.memory_dir, "feedback_p.md", description="p", topic="workflow",
                 body="произвольная [[заметка в скобках]] — не ссылка-урок")
    assert cg.find_broken_wikilinks(cfg.memory_dir, cfg) == []   # не начинается с префикса урока


def test_broken_wikilinks_in_pulse(cfg) -> None:
    from claude_memory import catalog_generate as cg
    write_lesson(cfg.memory_dir, "feedback_a.md", description="a", topic="workflow",
                 body="[[feedback_missing]]")
    _, diag = cg.build_catalog(cfg.memory_dir, cfg)
    assert len(diag["broken_wikilinks"]) == 1
    assert cg.format_health_pulse(diag, cfg)   # пульс не молчит при битой wiki-ссылке


# ── нудж «много уроков → проверь дубли» (только дедуп, без обобщения) ───────────

def test_pulse_many_lessons_nudges(cfg) -> None:
    from claude_memory import catalog_generate as cg
    cfg2 = replace(cfg, lesson_count_warn=2)
    for i in range(3):
        write_lesson(cfg.memory_dir, f"feedback_{i}.md", name=f"урок {i}",
                     description=f"d{i}", topic="workflow")
    _, diag = cg.build_catalog(cfg2.memory_dir, cfg2)
    pulse = cg.format_health_pulse(diag, cfg2)
    assert "3" in pulse and "duplicat" in pulse.lower()


def test_pulse_silent_under_threshold(cfg) -> None:
    from claude_memory import catalog_generate as cg
    cfg2 = replace(cfg, lesson_count_warn=100)
    write_lesson(cfg.memory_dir, "feedback_a.md", name="урок a",
                 description="a", topic="workflow")
    _, diag = cg.build_catalog(cfg2.memory_dir, cfg2)
    assert cg.format_health_pulse(diag, cfg2) == ""


def test_pulse_count_check_off_when_zero(cfg) -> None:
    from claude_memory import catalog_generate as cg
    cfg0 = replace(cfg, lesson_count_warn=0)   # дефолт теперь 500 — выключаем явно
    for i in range(5):
        write_lesson(cfg.memory_dir, f"feedback_{i}.md", name=f"урок {i}",
                     description=f"d{i}", topic="workflow")
    _, diag = cg.build_catalog(cfg.memory_dir, cfg0)
    assert cg.format_health_pulse(diag, cfg0) == ""
