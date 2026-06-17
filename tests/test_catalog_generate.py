"""Тесты машинной сборки указателя CATALOG."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from claude_memory import catalog_generate as CG
from conftest import write_lesson


def test_parse_frontmatter_toplevel_and_nested() -> None:
    fm = CG.parse_frontmatter(
        "---\nname: x\ndescription: про оплату\nmetadata:\n  topic: legal\n  type: feedback\n---\nтело\n"
    )
    assert fm["name"] == "x" and fm["description"] == "про оплату"
    assert fm["topic"] == "legal" and fm["type"] == "feedback"


def test_render_index_groups_by_topic(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_a.md", description="a", topic="workflow")
    write_lesson(cfg.memory_dir, "feedback_b.md", description="b", topic="testing")
    write_lesson(cfg.memory_dir, "feedback_c.md", description="c", topic="zzz-unknown")
    lessons = CG.collect_lessons(cfg.memory_dir, cfg)
    idx = CG.render_index(lessons, cfg)
    assert "### Workflow & methodology" in idx
    assert "### Testing" in idx
    assert cfg.no_topic_title in idx          # неизвестная тема → ⚠-раздел
    assert idx.index("Workflow") < idx.index("Testing")  # порядок из topic_order


def test_build_catalog_preserves_preamble(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_a.md", description="a", topic="workflow")
    cat = Path(cfg.memory_dir) / "CATALOG.md"
    cat.write_text(
        "# Моя рукописная шапка\n\nважная проза\n\n"
        f"{CG.AUTO_START}\nстарый индекс\n{CG.AUTO_END}\n",
        encoding="utf-8",
    )
    text, _ = CG.build_catalog(cfg.memory_dir, cfg)
    assert text.startswith("# Моя рукописная шапка")
    assert "важная проза" in text
    assert "feedback_a.md" in text            # машинный индекс пересобран
    assert "старый индекс" not in text        # между маркерами затёрто


def test_build_catalog_default_preamble_when_empty(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_a.md", description="a", topic="workflow")
    text, _ = CG.build_catalog(cfg.memory_dir, cfg)
    assert text.startswith(cfg.catalog_preamble)


def test_diagnostics_flags(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_ok.md", description="ok", topic="workflow")
    write_lesson(cfg.memory_dir, "feedback_notopic.md", description="x")          # без темы
    write_lesson(cfg.memory_dir, "feedback_big.md", description="big", topic="core",
                 body="x" * 9500)                                                  # oversize
    write_lesson(cfg.memory_dir, "feedback_link.md", description="l", topic="core",
                 body="см. [сюда](feedback_missing.md)")                           # битая ссылка
    lessons = CG.collect_lessons(cfg.memory_dir, cfg)
    diag = CG.run_diagnostics(cfg.memory_dir, lessons, cfg)
    assert "feedback_notopic.md" in diag["no_topic"]
    assert any(f == "feedback_big.md" for f, _ in diag["oversize"])
    assert ("feedback_link.md", "feedback_missing.md") in diag["broken_links"]


def test_set_frontmatter_field_idempotent_and_insert() -> None:
    text = "---\nname: x\ndescription: d\n---\nтело\n"
    new, changed = CG.set_frontmatter_field(text, "topic", "workflow")
    assert changed and "topic: workflow" in new
    again, changed2 = CG.set_frontmatter_field(new, "topic", "workflow")
    assert changed2 is False and again == new   # повтор — no-op


def test_migrate_frontmatter_dry_then_apply(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_a.md", description="a")  # без topic
    dry = CG.migrate_frontmatter(cfg.memory_dir, {"feedback_a.md": "workflow"}, {}, apply=False, cfg=cfg)
    assert dry["changed"] == ["feedback_a.md"]
    # сухой прогон ничего не записал
    assert "topic:" not in (Path(cfg.memory_dir) / "feedback_a.md").read_text(encoding="utf-8")
    CG.migrate_frontmatter(cfg.memory_dir, {"feedback_a.md": "workflow"}, {}, apply=True, cfg=cfg)
    assert "topic: workflow" in (Path(cfg.memory_dir) / "feedback_a.md").read_text(encoding="utf-8")


def test_topic_taxonomy_configurable(cfg) -> None:
    cfg2 = replace(cfg, topic_order=(("ops", "Operations"),), no_topic_title="NO TOPIC")
    write_lesson(cfg.memory_dir, "feedback_a.md", description="a", topic="ops")
    idx = CG.render_index(CG.collect_lessons(cfg.memory_dir, cfg2), cfg2)
    assert "### Operations" in idx
