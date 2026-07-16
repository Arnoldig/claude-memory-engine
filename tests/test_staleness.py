"""Тесты скана устаревания памяти (SessionEnd)."""
from __future__ import annotations

import datetime
from dataclasses import replace
from pathlib import Path

from claude_memory import staleness as ST
from claude_memory import archive_prune as AP
from conftest import write_lesson

TODAY = datetime.date(2026, 6, 17)


def _write_archived(cfg, name: str, archived_on=None, desc: str = "архивный урок") -> Path:
    """Кладёт урок в archive/<подкаталог> c полем archived_on (или без него)."""
    arc = Path(cfg.memory_dir) / cfg.archive_dir_name / "legacy"
    arc.mkdir(parents=True, exist_ok=True)
    p = arc / name
    fm = ["---", "name: x", f"description: {desc}"]
    if archived_on:
        fm.append(f'archived_on: "{archived_on}"')
    fm += ["---", "тело", ""]
    p.write_text("\n".join(fm), encoding="utf-8")
    return p


def test_scan_finds_expired_reverify(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_old.md", description="просрочено",
                 reverify_after="2026-01-01")
    write_lesson(cfg.memory_dir, "feedback_fresh.md", description="ещё актуально",
                 reverify_after="2099-01-01")
    stale, _ = ST.scan(cfg, today=TODAY)
    names = [n for _, n, _ in stale]
    assert "feedback_old.md" in names and "feedback_fresh.md" not in names


def test_scan_finds_dead_applies_to(cfg) -> None:
    # реальный файл проекта, чтобы repo_files был непустым (иначе проверка пропускается)
    (Path(cfg.project_root) / "app").mkdir()
    (Path(cfg.project_root) / "app" / "live.py").write_text("x", encoding="utf-8")
    write_lesson(cfg.memory_dir, "feedback_live.md", description="живой путь",
                 applies_to="[app/live.py]")
    write_lesson(cfg.memory_dir, "feedback_dead.md", description="путь исчез",
                 applies_to="[app/gone.py]")
    _, broken = ST.scan(cfg, today=TODAY)
    broken_names = [n for n, _ in broken]
    assert "feedback_dead.md" in broken_names
    assert "feedback_live.md" not in broken_names


def test_scan_unparsed_finds_undigested_applies_to(cfg) -> None:
    # Поле есть, глобов нет → урок никогда не всплывёт, а выглядит настроенным.
    # Скан обязан назвать и файл, и поле, и само непонятое значение.
    write_lesson(cfg.memory_dir, "feedback_bad.md", description="d", applies_to="{путь: app/x.py}")
    write_lesson(cfg.memory_dir, "feedback_empty.md", description="d", applies_to="")
    write_lesson(cfg.memory_dir, "feedback_ok.md", description="d", applies_to="app/x.py")     # скаляр — разобран
    write_lesson(cfg.memory_dir, "feedback_list.md", description="d", applies_to="[app/x.py]")  # список — разобран
    write_lesson(cfg.memory_dir, "feedback_none.md", description="d")                           # поля нет — не дефект
    res = ST.scan_unparsed(cfg)
    assert sorted(res) == [("feedback_bad.md", "applies_to", "{путь: app/x.py}"),
                           ("feedback_empty.md", "applies_to", "")]


def test_scan_unparsed_finds_non_iso_dates(cfg) -> None:
    # Дата не в ISO молча не существовала: урок выглядел срочным и жил вечно,
    # архивный — навсегда мимо срока хранения. Теперь про это говорят вслух.
    write_lesson(cfg.memory_dir, "feedback_ru.md", description="d", reverify_after='"01.01.2026"')
    write_lesson(cfg.memory_dir, "feedback_slash.md", description="d", reverify_after="2026/01/01")
    write_lesson(cfg.memory_dir, "feedback_arc.md", description="d", archived_on="01.01.2025")
    write_lesson(cfg.memory_dir, "feedback_iso.md", description="d", reverify_after="2026-01-01")  # ок
    write_lesson(cfg.memory_dir, "feedback_no.md", description="d")                                # поля нет
    res = sorted(ST.scan_unparsed(cfg))
    assert res == [("feedback_arc.md", "archived_on", "01.01.2025"),
                   ("feedback_ru.md", "reverify_after", '"01.01.2026"'),
                   ("feedback_slash.md", "reverify_after", "2026/01/01")]


def test_non_iso_date_does_not_silently_expire_or_archive(cfg) -> None:
    # Обратная сторона той же монеты: непонятая дата НЕ должна притворяться сроком.
    write_lesson(cfg.memory_dir, "feedback_ru.md", description="d", reverify_after="01.01.2026")
    stale, _ = ST.scan(cfg, today=TODAY)
    assert stale == []          # не просрочен: даты нет — но про это теперь жалуются
    assert ST.scan_unparsed(cfg) == [("feedback_ru.md", "reverify_after", "01.01.2026")]


def test_unparsed_goes_into_pending_with_name_field_and_value(cfg) -> None:
    # Жалоба должна дойти до человека через _stale_pending (его SessionStart печатает целиком).
    write_lesson(cfg.memory_dir, "feedback_bad.md", description="d", applies_to="{путь: app/x.py}")
    write_lesson(cfg.memory_dir, "feedback_dt.md", description="d", reverify_after="01.01.2026")
    assert ST.run(cfg, today=TODAY) is True
    body = (Path(cfg.memory_dir) / ST.STALE_FILE).read_text(encoding="utf-8")
    assert "feedback_bad.md" in body and "{путь: app/x.py}" in body
    assert "feedback_dt.md" in body and "01.01.2026" in body and "reverify_after" in body


def test_unparsed_section_capped_so_it_cannot_flood_context(cfg) -> None:
    # После массового импорта таких уроков могут быть десятки, а секция печатается в
    # контекст на КАЖДОМ старте. Показываем первые N + счётчик — сигнал есть, потопа нет.
    for i in range(ST.UNPARSED_CAP + 5):
        write_lesson(cfg.memory_dir, f"feedback_{i:03}.md", description="d", applies_to="[]")
    assert ST.run(cfg, today=TODAY) is True
    body = (Path(cfg.memory_dir) / ST.STALE_FILE).read_text(encoding="utf-8")
    assert body.count("— `applies_to:") == ST.UNPARSED_CAP
    assert "and 5 more" in body


def test_write_pending_creates_and_removes(cfg) -> None:
    out = Path(cfg.memory_dir) / ST.STALE_FILE
    assert ST.write_pending(cfg, stale=[("2026-01-01", "feedback_x.md", "d")], broken=[], today=TODAY)
    assert out.exists() and "reverify_after" in out.read_text(encoding="utf-8")
    # пустой долг → файл удаляется (не шумим на старте)
    assert ST.write_pending(cfg, stale=[], broken=[], today=TODAY) is False
    assert not out.exists()


def test_run_end_to_end(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_old.md", description="d", reverify_after="2026-01-01")
    assert ST.run(cfg, today=TODAY) is True
    assert (Path(cfg.memory_dir) / ST.STALE_FILE).exists()


def test_archive_stale_off_when_zero(cfg) -> None:
    # archive_stale_months=0 → функция выключена; дефолт (6 мес) — включена
    cfg0 = replace(cfg, archive_stale_months=0)
    _write_archived(cfg, "feedback_ancient.md", "2020-01-01")
    assert ST.scan_archive_stale(cfg0, today=TODAY) == []
    # дефолт теперь 6 мес → тот же древний урок флагуется
    assert any(n == "feedback_ancient.md" for _, n, _, _ in ST.scan_archive_stale(cfg, today=TODAY))


def test_archive_stale_respects_threshold_and_field(cfg) -> None:
    cfg12 = replace(cfg, archive_stale_months=12)
    _write_archived(cfg, "feedback_old_arc.md", "2025-01-01")     # 17 мес — кандидат
    _write_archived(cfg, "feedback_recent_arc.md", "2026-06-01")  # 0 мес — нет
    _write_archived(cfg, "feedback_no_field.md", archived_on=None)  # без поля — агрегат, не кандидат
    res = ST.scan_archive_stale(cfg12, today=TODAY)
    names = [n for _, n, _, _ in res]
    assert names == ["feedback_old_arc.md"]
    # выходит и в _stale_pending (отдельная секция)
    assert ST.run(cfg12, today=TODAY) is True
    body = (Path(cfg.memory_dir) / ST.STALE_FILE).read_text(encoding="utf-8")
    assert "feedback_old_arc.md" in body


def test_quoted_description_stripped_in_stale_and_archive(cfg) -> None:
    # description в кавычках → в _stale_pending (просрочка reverify И архив) показывается
    # БЕЗ кавычек, как в CATALOG/поиске. Раньше staleless снимал только пробелы.
    write_lesson(cfg.memory_dir, "feedback_rev.md",
                 description='"просрочено в кавычках"', reverify_after="2026-01-01")
    stale, _ = ST.scan(cfg, today=TODAY)
    assert [d for _, n, d in stale if n == "feedback_rev.md"] == ["просрочено в кавычках"]
    # то же для архивных уроков (scan_archive_stale)
    _write_archived(cfg, "feedback_arc_q.md", "2025-01-01", desc='"архив в кавычках"')
    cfg12 = replace(cfg, archive_stale_months=12)
    arc = ST.scan_archive_stale(cfg12, today=TODAY)
    assert [d for _, n, _, d in arc if n == "feedback_arc_q.md"] == ["архив в кавычках"]


def test_archive_prune_backs_up_then_deletes(cfg) -> None:
    cfg12 = replace(cfg, archive_stale_months=12)
    p = _write_archived(cfg, "feedback_old_arc.md", "2025-01-01")
    # dry-run ничего не трогает
    cands, deleted = AP.prune(cfg12, apply=False, today=TODAY)
    assert [n for _, n, _, _ in cands] == ["feedback_old_arc.md"] and deleted == [] and p.exists()
    # apply: бэкап ДО удаления, оригинал исчезает
    _, deleted = AP.prune(cfg12, apply=True, today=TODAY)
    assert deleted == ["feedback_old_arc.md"] and not p.exists()
    backup = Path(cfg.memory_dir) / AP.BACKUP_DIR / TODAY.isoformat() / "feedback_old_arc.md"
    assert backup.exists()
    # бэкап вне глоба хранения → повторный скан его НЕ переоткрывает
    assert ST.scan_archive_stale(cfg12, today=TODAY) == []
