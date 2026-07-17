"""Единое понятие «урок» (lesson_files) — и его ЭКВИВАЛЕНТНОСТЬ у всех потребителей.

Главный тест файла — `test_all_consumers_see_the_same_set`: пока каталог, ретривер и
страж физически смотрят на ОДИН набор, баг «страж требует урок, который сам же не
видит» воскреснуть не может. Он и завёлся оттого, что такого теста не было.
"""
from __future__ import annotations

import os

from claude_memory import lesson_files as LF
from conftest import write_lesson


def _mixed_corpus(cfg) -> None:
    """Каталог со ВСЕМИ живыми формами имён — как в боевых корпусах."""
    md = cfg.memory_dir
    write_lesson(md, "feedback_a.md", name="feedback_a", description="d", type="feedback")
    write_lesson(md, "reference_b.md", name="reference_b", description="d", type="reference")
    write_lesson(md, "user_profile.md", name="user_profile", description="d", type="user")
    write_lesson(md, "role_senior.md", name="role_senior", description="d", type="role")
    write_lesson(md, "kebab-case-lesson.md", name="kebab-case-lesson", description="d", type="project")
    write_lesson(md, "no-type-field.md", name="no-type-field", description="d")
    write_lesson(md, cfg.core_file, name="core", description="ядро — НЕ урок")
    write_lesson(md, cfg.catalog_file, name="catalog", description="указатель — НЕ урок")
    write_lesson(md, "_private.md", name="private", description="приватный — НЕ урок")
    (os.path.join(md, "notes.txt"))
    open(os.path.join(md, "notes.txt"), "w").write("не markdown — НЕ урок")
    open(os.path.join(md, "_retrieve_cache.sqlite3"), "w").write("служебная БД — НЕ урок")
    os.mkdir(os.path.join(md, "archive"))
    write_lesson(os.path.join(md, "archive"), "precedents-2026.md", name="arc", description="подпапка — НЕ урок")


EXPECTED = {
    "feedback_a.md", "reference_b.md", "user_profile.md",
    "role_senior.md", "kebab-case-lesson.md", "no-type-field.md",
}


def test_is_lesson_file(cfg) -> None:
    assert LF.is_lesson_file("kebab-case-lesson.md", cfg) is True
    assert LF.is_lesson_file("feedback_a.md", cfg) is True
    assert LF.is_lesson_file("no-type-field.md", cfg) is True   # тип не влияет на «урок ли»
    assert LF.is_lesson_file(cfg.core_file, cfg) is False
    assert LF.is_lesson_file(cfg.catalog_file, cfg) is False
    assert LF.is_lesson_file("_private.md", cfg) is False
    assert LF.is_lesson_file("notes.txt", cfg) is False
    assert LF.is_lesson_file("_retrieve_cache.sqlite3", cfg) is False


def test_lesson_paths_root_only(cfg) -> None:
    _mixed_corpus(cfg)
    assert {os.path.basename(p) for p in LF.lesson_paths(cfg)} == EXPECTED


def test_lesson_paths_sorted(cfg) -> None:
    _mixed_corpus(cfg)
    got = LF.lesson_paths(cfg)
    assert got == sorted(got)


def test_lesson_type_from_flat_and_nested(cfg) -> None:
    from claude_memory.catalog_generate import parse_frontmatter

    flat = "---\nname: x\ntype: feedback\n---\nтело\n"
    assert LF.lesson_type(parse_frontmatter(flat)) == "feedback"

    # форма, которую пишет авто-память Claude Code
    nested = (
        "---\nname: pravovaya-ramka-rkn\ndescription: \"д\"\n"
        "metadata:\n  node_type: memory\n  type: project\n"
        "  originSessionId: 08df08b5-d6b3-404f-a5db-103ac1040541\n---\nтело\n"
    )
    assert LF.lesson_type(parse_frontmatter(nested)) == "project"


def test_lesson_type_absent_is_empty_not_guessed(cfg) -> None:
    """Поля нет → пустой тип. НЕ угадываем по приставке имени: это было второе
    определение урока, из-за которого и завёлся баг."""
    from claude_memory.catalog_generate import parse_frontmatter

    assert LF.lesson_type(parse_frontmatter("---\nname: feedback_x\n---\nтело\n")) == ""
    assert LF.lesson_type({}) == ""


def test_all_consumers_see_the_same_set(cfg) -> None:
    """АНТИ-ВОСКРЕШЕНИЕ. Каталог, ретривер и страж обязаны видеть ОДИН набор уроков.

    Именно расхождение этих наборов и было багом: страж требовал зафиксировать урок,
    урок записывали в принятом в проекте стиле, а страж его не видел — и требовал снова.
    """
    from claude_memory import catalog_generate as CG, memory_retrieve as MR, stop_check as SC

    _mixed_corpus(cfg)

    canonical = {os.path.basename(p) for p in LF.lesson_paths(cfg)}
    catalog = {b for _, b in CG._lesson_paths(cfg.memory_dir, cfg)}
    retriever = {os.path.basename(p) for p in MR._candidate_files(cfg)}

    assert canonical == EXPECTED
    assert catalog == canonical, "каталог видит не тот набор, что единый источник истины"
    assert retriever == canonical, "ретривер видит не тот набор, что единый источник истины"

    # Страж: сверяем НАБОР ПОЭЛЕМЕНТНО, а не одно число. Сравнение только max(mtime) слабое:
    # страж мог бы смотреть на ДРУГОЙ набор с тем же максимумом и остаться зелёным. Поэтому
    # щупаем каждый файл — делаем его самым свежим и требуем, чтобы страж это заметил.
    stamp = 2_000_000_000
    for i, path in enumerate(LF.lesson_paths(cfg)):
        os.utime(path, (stamp + i, stamp + i))
        assert SC.newest_lesson_mtime(cfg) == float(stamp + i), (
            f"страж не увидел урок {os.path.basename(path)} — его набор ýже каталога"
        )

    # ...и обратное: не-урок, ставший самым свежим, страж видеть НЕ должен — иначе он снимет
    # блок за человека (указатель движок пересобирает сам на каждом старте).
    for base in (cfg.core_file, cfg.catalog_file, "_private.md"):
        os.utime(os.path.join(cfg.memory_dir, base), (stamp + 999, stamp + 999))
    assert SC.newest_lesson_mtime(cfg) < float(stamp + 999), (
        "страж принял за урок ядро/указатель/приватный файл — они меняются сами"
    )
