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


def test_archive_stale_disabled_by_default(cfg) -> None:
    # archive_stale_months=0 (дефолт) → функция выключена, ничего не флагуется
    _write_archived(cfg, "feedback_ancient.md", "2020-01-01")
    assert ST.scan_archive_stale(cfg, today=TODAY) == []


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
