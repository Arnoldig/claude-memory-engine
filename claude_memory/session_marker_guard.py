"""Страж формата session-end маркеров.

Правило транзитного session-lessons-файла: новый маркер — ОДНА строка ≤N знаков
(`<!-- YYYY-MM-DD … -->`), а разбор сессии — не в маркер, а ссылкой на drafts/архив.
Правило-напоминание в шапке файла систематически игнорировалось → переведено в
PreToolUse-deny по принципу «страж > напоминание».

Deny НЕ разовый (в отличие от стража выбора модели): повтор проходит только после
исправления маркера — этим достигается 100% соблюдение формата. Fail-open на любой
неоднозначности (не тот файл / нет добавляемого текста / кривой вход).

Имя целевого файла и лимит длины — из конфига (session_lessons_file / marker_limit).
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .config import MemoryConfig, get_config
# Единый источник истины формата маркера — регэксп архиватора (колонка 0 умышленно,
# без lstrip: отсекает inline-примеры формата в теле урока).
from .memory_archive import SESSION_MARKER_RE as MARKER_START_RE


def _added_text(tool_name: str, tool_input: dict) -> str:
    """Текст, который инструмент ДОБАВЛЯЕТ в файл (existing-контент Edit не проверяем)."""
    if tool_name == "Edit":
        return str(tool_input.get("new_string") or "")
    if tool_name == "Write":
        return str(tool_input.get("content") or "")
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        return "\n".join(
            str(e.get("new_string") or "") for e in edits if isinstance(e, dict)
        )
    return ""


def _collect_markers(text: str) -> List[Tuple[str, int]]:
    """Маркеры в добавляемом тексте: [(первая строка маркера, число строк), ...].

    Многострочный маркер — всегда нарушение (правило: одна строка), поэтому его тело
    не собираем: достаточно факта «не закрылся `-->` на своей строке» (число строк = 2).
    """
    lines = text.split("\n")
    found: List[Tuple[str, int]] = []
    for ln in lines:
        if MARKER_START_RE.match(ln):
            found.append((ln, 1 if "-->" in ln else 2))
    return found


def violation_reason(
    tool_name: str, tool_input: object, cfg: Optional[MemoryConfig] = None
) -> Optional[str]:
    """Причина deny, если добавляемый session-маркер нарушает формат. Иначе None."""
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        return None
    if not isinstance(tool_input, dict):
        return None
    cfg = cfg or get_config()
    target = cfg.session_lessons_file
    suffix = "/" + target
    path = str(tool_input.get("file_path") or "")
    if not path.endswith(suffix) and path != target:
        return None
    markers = _collect_markers(_added_text(tool_name, tool_input))
    limit = cfg.marker_limit
    bad = [(m, n) for m, n in markers if len(m) > limit or n > 1]
    if not bad:
        return None
    worst, n_lines = max(bad, key=lambda x: len(x[0]))
    lines_part = "развёрнут на несколько строк" if n_lines > 1 else f"{len(worst)} знаков"
    return (
        f"Session-маркер нарушает формат файла: ОДНА строка ≤{limit} знаков "
        f"(у тебя {lines_part}). Сократи маркер до сути одной строкой "
        "(`<!-- YYYY-MM-DD <hash> #тег — суть -->`), а разбор сессии положи в "
        "drafts/<session>.md или archive/ и сошлись на него. Повтор с исправленным "
        "маркером пройдёт. [session-marker-guard]"
    )
