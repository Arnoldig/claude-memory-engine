"""Авто-архивация старого контента в memory feedback-файлах.

Два независимых архиватора:
1. `archive_old_precedents` — абзацы-карточки `**<keyword> … YYYY-MM-DD … :**` старше
   N дней → `archive/precedents-YYYY-QN.md`, в источнике остаётся pointer-ссылка.
   Inline-маркеры (внутри параграфа), без даты — НЕ трогаются.
2. `archive_old_session_markers` — HTML-маркеры `<!-- YYYY-MM-DD … -->` (audit-trail
   сессий в session-lessons-файле) старше M дней → `archive/session-end-markers-YYYY-QN.md`,
   переносятся ПОЛНОСТЬЮ (источник транзитный, без pointer). Окно для маркеров короче
   окна прецедентов: маркеры однострочны и плодятся быстро.

Ключевое слово карточки (`<keyword>`) и фраза-указатель берутся из конфига
(MemoryConfig.precedent_keyword / precedent_pointer) — дефолты русские, перекрываются.
Парсинг — регэкспами, без зависимостей (локальный `pytest`, без PyYAML).
"""
from __future__ import annotations

import datetime
import os
import re
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from .config import MemoryConfig, get_config
from .messages import msg

ARCHIVE_DIR_NAME = "archive"
# Маркер сессии: строка-комментарий, начинающаяся в колонке 0 с `<!-- YYYY-MM-DD`.
# Колонка 0 (без lstrip) умышленно — отсекает inline-пример формата в тексте урока.
# Язык-нейтральна (только дата) → не параметризуется.
SESSION_MARKER_RE = re.compile(r"^<!--\s*(\d{4}-\d{2}-\d{2})")


class ArchiveResult(NamedTuple):
    archived_count: int
    archive_files_touched: List[Path]
    real_precedents_after: int
    size_after: int


def _precedent_head_re(cfg: MemoryConfig) -> "re.Pattern[str]":
    """Регэксп заголовка карточки-прецедента `**<keyword> … YYYY-MM-DD … :**`.

    Перед датой стоит ЖАДНЫЙ `[^:\\n]*` (не ленивый) → при нескольких датах в заголовке
    (напр. дата в слаге `#audit-2026-06-11-F` + реальная дата карточки `2026-06-12`)
    захватывается ПОСЛЕДНЯЯ — это дата самой карточки, а не служебная из слага. От неё
    зависят квартал архива и порог архивации, поэтому важно брать верную."""
    kw = re.escape(cfg.precedent_keyword)
    return re.compile(rf"^\*\*{kw}[^:\n]*(\d{{4}}-\d{{2}}-\d{{2}})[^:\n]*?:\*\*", re.MULTILINE)


def _link_re(cfg: MemoryConfig) -> "re.Pattern[str]":
    """Признак уже-перенесённой карточки (pointer-ссылка), чтобы не архивировать дважды.

    Каталог архива берём из cfg.archive_dir_name (а не литерал 'archive/'), иначе при
    кастомном имени каталога pointer не распознаётся → ломается идемпотентность."""
    arc = re.escape(cfg.archive_dir_name)
    return re.compile(re.escape(cfg.precedent_pointer) + r" \[" + arc + r"/precedents-")


def _quarter(month: int) -> int:
    return (month - 1) // 3 + 1


def _archive_path(memory_root: Path, year: int, quarter: int, cfg: MemoryConfig) -> Path:
    return memory_root / cfg.archive_dir_name / f"precedents-{year}-Q{quarter}.md"


def archive_old_precedents(
    feedback_path: Path,
    today: Optional[datetime.date] = None,
    threshold_days: int = 30,
    cfg: Optional[MemoryConfig] = None,
) -> ArchiveResult:
    """Архивирует прецеденты >threshold_days в feedback_path, заменяя их ссылками.

    Атомарная запись через tempfile + os.replace. Если кандидатов нет — no-op.
    Возвращает ArchiveResult с метриками (для warning'ов в hook'е).
    """
    cfg = cfg or get_config()
    head_re = _precedent_head_re(cfg)
    link_re = _link_re(cfg)
    if today is None:
        today = datetime.date.today()
    threshold = today - datetime.timedelta(days=threshold_days)

    memory_root = feedback_path.parent
    content = feedback_path.read_text(encoding="utf-8")

    paragraphs = content.split("\n\n")
    new_paragraphs: List[str] = []
    archive_appends: Dict[Path, Tuple[int, int, List[Tuple[str, str, str]]]] = {}
    fname = feedback_path.name

    i = 0
    while i < len(paragraphs):
        para = paragraphs[i]
        if link_re.search(para):
            new_paragraphs.append(para)
            i += 1
            continue
        m = head_re.match(para)
        if not m:
            new_paragraphs.append(para)
            i += 1
            continue
        date_str = m.group(1)
        try:
            d = datetime.date.fromisoformat(date_str)
        except ValueError:
            new_paragraphs.append(para)
            i += 1
            continue
        if d > threshold:
            new_paragraphs.append(para)
            i += 1
            continue
        # Карточка может занимать НЕСКОЛЬКО абзацев: заголовок + последующие абзацы тела
        # до следующего заголовка-карточки / pointer-ссылки / markdown-заголовка (`#`).
        # Забираем их вместе, иначе хвост осиротеет в источнике под pointer-строкой
        # «перенесён в архив» (формально неверной — переехал только заголовок).
        block_paras = [para]
        j = i + 1
        while j < len(paragraphs):
            nxt = paragraphs[j]
            if head_re.match(nxt) or link_re.search(nxt) or nxt.lstrip().startswith("#"):
                break
            block_paras.append(nxt)
            j += 1
        block_text = "\n\n".join(block_paras)
        i = j
        quarter = _quarter(d.month)
        qfile = _archive_path(memory_root, d.year, quarter, cfg)
        qrelative = f"{cfg.archive_dir_name}/{qfile.name}"
        archive_appends.setdefault(qfile, (d.year, quarter, []))[2].append(
            (date_str, fname, block_text)
        )
        new_paragraphs.append(
            msg(
                cfg,
                "archive.precedent_pointer_line",
                keyword=cfg.precedent_keyword,
                date_str=date_str,
                pointer=cfg.precedent_pointer,
                qrelative=qrelative,
            )
        )

    archived_count = sum(len(v[2]) for v in archive_appends.values())

    if archived_count == 0:
        size_after = feedback_path.stat().st_size
        real_precedents = count_real_precedents(content, cfg)
        return ArchiveResult(0, [], real_precedents, size_after)

    for qfile, (year, quarter, blocks) in archive_appends.items():
        qfile.parent.mkdir(parents=True, exist_ok=True)
        existing = qfile.read_text(encoding="utf-8") if qfile.exists() else ""
        if not existing:
            existing = msg(
                cfg,
                "archive.precedent_file_header",
                keyword=cfg.precedent_keyword,
                year=year,
                quarter=quarter,
            )
            qfile.write_text(existing, encoding="utf-8")
        with qfile.open("a", encoding="utf-8") as f:
            for date_str, source_name, block_text in blocks:
                # Дедуп: если крах в окне «append прошёл → os.replace источника упал» оставил
                # карточку в архиве, повторный прогон НЕ должен её задвоить (append не идемпотентен).
                if block_text.strip() and block_text.strip() in existing:
                    continue
                chunk = msg(
                    cfg,
                    "archive.precedent_block_header",
                    date_str=date_str,
                    source_name=source_name,
                    block_text=block_text,
                )
                f.write(chunk)
                existing += chunk

    new_content = "\n\n".join(new_paragraphs)
    tmp_path = feedback_path.with_name(feedback_path.name + ".tmp")
    tmp_path.write_text(new_content, encoding="utf-8")
    os.replace(tmp_path, feedback_path)

    real_precedents_after = count_real_precedents(new_content, cfg)
    size_after = feedback_path.stat().st_size

    return ArchiveResult(
        archived_count=archived_count,
        archive_files_touched=list(archive_appends.keys()),
        real_precedents_after=real_precedents_after,
        size_after=size_after,
    )


def _session_archive_path(memory_root: Path, year: int, quarter: int, cfg: MemoryConfig) -> Path:
    return memory_root / cfg.archive_dir_name / f"session-end-markers-{year}-Q{quarter}.md"


def archive_old_session_markers(
    feedback_path: Path,
    today: Optional[datetime.date] = None,
    threshold_days: int = 7,
    cfg: Optional[MemoryConfig] = None,
) -> ArchiveResult:
    """Архивирует HTML-маркеры `<!-- YYYY-MM-DD … -->` старше threshold_days.

    Блок маркера = строка, начинающаяся с `<!--` (колонка 0), и все следующие
    строки до очередного такого `<!--`. Переносятся ПОЛНОСТЬЮ (без pointer-замены:
    источник транзитный, архив — полный лог). Атомарно. Нет кандидатов — no-op.
    """
    cfg = cfg or get_config()
    if today is None:
        today = datetime.date.today()
    threshold = today - datetime.timedelta(days=threshold_days)

    memory_root = feedback_path.parent
    lines = feedback_path.read_text(encoding="utf-8").splitlines(keepends=True)

    marker_starts = [i for i, ln in enumerate(lines) if ln.startswith("<!--")]
    if not marker_starts:
        return ArchiveResult(0, [], 0, feedback_path.stat().st_size)

    header = lines[: marker_starts[0]]
    region = lines[marker_starts[0]:]

    blocks: List[List[str]] = []
    cur: List[str] = []
    for ln in region:
        if ln.startswith("<!--"):
            if cur:
                blocks.append(cur)
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        blocks.append(cur)

    kept_blocks: List[List[str]] = []
    archive_appends: Dict[Path, Tuple[int, int, List[str]]] = {}
    for b in blocks:
        text = "".join(b)
        m = SESSION_MARKER_RE.match(text)
        if not m:
            kept_blocks.append(b)
            continue
        try:
            d = datetime.date.fromisoformat(m.group(1))
        except ValueError:
            kept_blocks.append(b)
            continue
        if d > threshold:
            kept_blocks.append(b)
            continue
        quarter = _quarter(d.month)
        qfile = _session_archive_path(memory_root, d.year, quarter, cfg)
        archive_appends.setdefault(qfile, (d.year, quarter, []))[2].append(
            text.rstrip() + "\n"
        )

    archived_count = sum(len(v[2]) for v in archive_appends.values())
    if archived_count == 0:
        return ArchiveResult(0, [], 0, feedback_path.stat().st_size)

    for qfile, (year, quarter, texts) in archive_appends.items():
        qfile.parent.mkdir(parents=True, exist_ok=True)
        if not qfile.exists():
            qfile.write_text(
                msg(
                    cfg,
                    "archive.session_markers_file_header",
                    year=year,
                    quarter=quarter,
                ),
                encoding="utf-8",
            )
        with qfile.open("a", encoding="utf-8") as f:
            f.write(
                msg(
                    cfg,
                    "archive.session_markers_section_header",
                    today=today.isoformat(),
                    threshold_days=threshold_days,
                )
            )
            for t in texts:
                f.write(t)

    body = "".join("".join(b) for b in kept_blocks)
    if header:
        new_content = "".join(header).rstrip() + "\n\n" + body
    else:
        new_content = body
    new_content = new_content.rstrip() + "\n"

    tmp_path = feedback_path.with_name(feedback_path.name + ".tmp")
    tmp_path.write_text(new_content, encoding="utf-8")
    os.replace(tmp_path, feedback_path)

    return ArchiveResult(
        archived_count=archived_count,
        archive_files_touched=list(archive_appends.keys()),
        real_precedents_after=0,
        size_after=feedback_path.stat().st_size,
    )


def count_real_precedents(text: str, cfg: Optional[MemoryConfig] = None) -> int:
    """Считает живые карточки-прецеденты (без перенесённых pointer-ссылок).

    Публичная функция — используется и `archive_old_precedents`, и hook'ом
    обслуживания для определения warning-уровня.
    """
    cfg = cfg or get_config()
    # Считаем ТЕМ ЖЕ регэкспом заголовка, что и архиватор (_precedent_head_re): иначе
    # карточки с датой в скобках/после слага («**Прецедент #61 (2026-04-27):**») невидимы
    # счётчику, и warning о количестве живых прецедентов под-срабатывает.
    total = len(_precedent_head_re(cfg).findall(text))
    linked = len(_link_re(cfg).findall(text))
    return max(0, total - linked)
