"""Страж выбора модели суб-агентов.

Правило ярусов: объёмное чтение/поиск/механику отдавать суб-агенту на дешёвую модель,
задачи с суждением — на среднюю, деликатные делегируемые проверки — на верхнюю
делегируемую, а самую сильную модель сессии (главный поток) оставлять на критический
путь. Эмпирика: большинство запусков суб-агентов идут БЕЗ `model` (=наследование самой
дорогой модели), потому что «худший дефолт»: забыл указать → берётся самая дорогая.

Решение — PreToolUse-страж на инструмент `Agent`: при ПЕРВОМ за сессию «забывчивом»
запуске (тип рутинный И `model` не указан) hook делает deny с напоминанием; ассистент
перевыпускает вызов с осознанной моделью (или повторяет как есть — повтор проходит).
Маркер на сессию снимает блок → нудж разовый. Fail-OPEN на любой неоднозначности.

Рутинные типы и подстрока «самой сильной модели» — из конфига (routine_subagent_types /
strongest_model_substr): обновляются без правки кода при смене поколений моделей.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config import MemoryConfig, get_config
from .messages import msg

MARKER_PREFIX = "claude-subagent-model-"
STRONGEST_MARKER_PREFIX = "claude-subagent-strongest-"


def _reason(subagent_type: str, cfg: Optional[MemoryConfig] = None) -> str:
    cfg = cfg or get_config()
    return msg(cfg, "model_guard.no_model_reason", subagent_type=subagent_type)


def _routine_call(tool_name: str, tool_input: object, cfg: MemoryConfig) -> Optional[tuple]:
    """(subagent_type, model) для рутинного Agent-вызова; иначе None (fail-open)."""
    if tool_name != "Agent":
        return None
    if not isinstance(tool_input, dict):
        return None
    subagent_type = str(tool_input.get("subagent_type") or "").strip()
    if subagent_type not in cfg.routine_subagent_types:
        return None
    model = str(tool_input.get("model") or "").strip()
    return subagent_type, model


def decide(tool_name: str, tool_input: object, cfg: Optional[MemoryConfig] = None) -> Optional[str]:
    """Чистая логика (без файловой системы): текст-причина нуджа или None.

    Fail-OPEN во всех неоднозначностях. Нудж только при `model` пустом И рутинном типе;
    явно указанный `model` — осознанный выбор → None. Явный выбор САМОЙ СИЛЬНОЙ модели
    для рутины — отдельный нудж, см. decide_strongest().
    """
    cfg = cfg or get_config()
    call = _routine_call(tool_name, tool_input, cfg)
    if call is None:
        return None
    _subagent_type, model = call
    if model:
        return None  # явный выбор модели уважаем
    return _reason(call[0], cfg)


def _strongest_reason(
    subagent_type: str, model: str, cfg: Optional[MemoryConfig] = None
) -> str:
    cfg = cfg or get_config()
    return msg(
        cfg, "model_guard.strongest_model_reason", subagent_type=subagent_type, model=model
    )


def decide_strongest(
    tool_name: str, tool_input: object, cfg: Optional[MemoryConfig] = None
) -> Optional[str]:
    """Нудж на ЯВНЫЙ выбор сильнейшей модели для рутинного типа. Fail-OPEN."""
    cfg = cfg or get_config()
    call = _routine_call(tool_name, tool_input, cfg)
    if call is None:
        return None
    subagent_type, model = call
    if not model:
        return None
    # strongest_model_substr может быть строкой ИЛИ списком подстрок — гибко под смену
    # поколений/разное число «премиальных» моделей (Fable/Opus/…). Совпадение по любой.
    subs = cfg.strongest_model_substr
    subs = [subs] if isinstance(subs, str) else list(subs)
    ml = model.lower()
    if not any(str(s).lower() in ml for s in subs if s):
        return None
    return _strongest_reason(subagent_type, model, cfg)


def _marker_path_for(prefix: str, session_id: str, tmpdir: str) -> Path:
    return Path(tmpdir) / f"{prefix}{session_id or 'nosess'}"


def marker_path(session_id: str, tmpdir: str) -> Path:
    """Путь разового маркера сессии: <tmpdir>/claude-subagent-model-<session_id>."""
    return _marker_path_for(MARKER_PREFIX, session_id, tmpdir)


def strongest_marker_path(session_id: str, tmpdir: str) -> Path:
    """Маркер разового нуджа «рутина на сильнейшей модели» (отдельный от «забыл model»)."""
    return _marker_path_for(STRONGEST_MARKER_PREFIX, session_id, tmpdir)


def _fire_once(marker: Path, reason: str) -> Optional[str]:
    """Вернуть reason и поставить маркер, если маркера ещё нет; иначе None (fail-silent)."""
    if marker.exists():
        return None
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("1", encoding="utf-8")
    except OSError:
        pass  # tmp недоступен → всё равно нуджим сейчас
    return reason


def gate(
    session_id: str,
    tool_name: str,
    tool_input: object,
    tmpdir: str,
    cfg: Optional[MemoryConfig] = None,
) -> Optional[str]:
    """decide()/decide_strongest() + разовость (по отдельному маркеру на каждый вид нуджа)."""
    cfg = cfg or get_config()
    reason = decide(tool_name, tool_input, cfg)
    if reason is not None:
        return _fire_once(marker_path(session_id, tmpdir), reason)
    strongest = decide_strongest(tool_name, tool_input, cfg)
    if strongest is not None:
        return _fire_once(strongest_marker_path(session_id, tmpdir), strongest)
    return None
