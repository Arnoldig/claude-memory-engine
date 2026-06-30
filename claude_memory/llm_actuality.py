"""Страж актуальности линейки моделей (llm_actuality, SessionStart + чек-лист).

Один страж вместо двух: реактивная «незнакомая модель» + суточная сверка линейки.
Сам движок офлайн и про модели не знает, поэтому суточная часть НЕ ходит в сеть, а
ПРОСИТ ассистента сверить линейку (делегировав дешёвой модели + веб-поиск) и записать
итог командой `llm-verified` / `llm-changes`. Файл состояния (приватный, не индексируется)
хранит дату последней сверки, итог и подтверждённый список семейств — он же троттлит
«раз в сутки» между сессиями и кормит реактивную проверку «незнакомой модели».

Чистые функции принимают `now` явно (тестируемость); граница (SessionStart, CLI) берёт
реальное время. Fail-open: любая ошибка → пустой результат / не блокируем.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import List, Optional

from .config import MemoryConfig, get_config
from .messages import msg
from .model_registry_guard import _is_known, resolve_model

STATE_FILE = "_llm_registry_state.json"


def state_path(cfg: MemoryConfig) -> Path:
    return Path(cfg.memory_dir) / STATE_FILE


def load_state(cfg: MemoryConfig) -> dict:
    """Состояние сверки линейки ({} если файла нет / битый JSON). Fail-open."""
    try:
        data = json.loads(state_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def families(cfg: MemoryConfig, state: Optional[dict] = None) -> tuple:
    """Подтверждённый список семейств: из состояния, иначе сид из known_model_substrs."""
    state = load_state(cfg) if state is None else state
    fam = state.get("families")
    if isinstance(fam, list) and fam:
        return tuple(str(x) for x in fam)
    return tuple(cfg.known_model_substrs or ())


def _parse_iso(s) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None


def is_due(state: dict, now: datetime.datetime, interval_hours: int) -> bool:
    """Пора ли просить сверку: нет verified_on ИЛИ прошло ≥ interval_hours."""
    d = _parse_iso(state.get("verified_on"))
    if d is None:
        return True
    try:
        elapsed = (now - d).total_seconds()
    except TypeError:  # несовместимые aware/naive (ручная правка состояния) → считаем «пора»
        return True
    return elapsed >= interval_hours * 3600


def record_state(
    cfg: MemoryConfig, now: datetime.datetime, result: str, fam: Optional[List[str]] = None
) -> bool:
    """Записать итог сверки (verified_on=now, result, families). fam не задан → прежний
    список (или сид). Возвращает True при успехе. Fail-open."""
    cur = load_state(cfg)
    new_fam = [str(x) for x in fam] if fam else list(families(cfg, cur))
    data = {
        "verified_on": now.replace(microsecond=0).isoformat(),
        "result": result,
        "families": new_fam,
    }
    try:
        state_path(cfg).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return True
    except OSError:
        return False


def session_start_nudge(
    event: dict, cfg: Optional[MemoryConfig] = None, now: Optional[datetime.datetime] = None
) -> str:
    """Текст для SessionStart: реактивная «незнакомая модель» + (если пора) суточная просьба
    сверить линейку. '' если ничего."""
    cfg = cfg or get_config()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    state = load_state(cfg)
    fam = families(cfg, state)
    out: List[str] = []
    # 1) реактивно: модель сессии не из подтверждённых семейств
    model = resolve_model(event, cfg)
    if fam and model and not _is_known(model, fam):
        out.append(msg(cfg, "model_registry.unknown_model", model=model))
    # 2) суточно: пора просить сверку линейки (делегировать дешёвой модели + веб)
    if getattr(cfg, "llm_actuality_enabled", False) and is_due(
        state, now, cfg.llm_actuality_interval_hours
    ):
        out.append(
            msg(cfg, "llm_actuality.verify_ask",
                families=", ".join(fam) or "—", interval=cfg.llm_actuality_interval_hours)
        )
    return "\n".join(out)


def checklist_line(cfg: MemoryConfig, now: Optional[datetime.datetime] = None) -> str:
    """Строка статуса актуальности LLM для чек-листа закрытия. '' если страж выключен."""
    if not getattr(cfg, "llm_actuality_enabled", False):
        return ""
    state = load_state(cfg)
    d = _parse_iso(state.get("verified_on"))
    if d is None:
        return msg(cfg, "llm_actuality.checklist_never")
    ts = d.replace(microsecond=0).isoformat()
    result = str(state.get("result") or "")
    if result and result != "confirmed":
        return msg(cfg, "llm_actuality.checklist_changes", ts=ts, note=result)
    return msg(cfg, "llm_actuality.checklist_verified", ts=ts)
