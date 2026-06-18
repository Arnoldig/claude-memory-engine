"""Реестр моделей (model_registry guard, SessionStart): подстраховка от устаревания
линейки моделей. Без ИИ, без сети, ноль токенов.

Проблема: имя «самой сильной» модели и ярусы делегирования (strongest_model_substr,
проектные доки) со временем устаревают — модель деактивируют или выходит новое
поколение, а конфиг молча остаётся прежним. Этот страж делает устаревание ВИДИМЫМ.

Две независимые проверки на SessionStart:
1. Неизвестная модель — сессия идёт на модели, чей id не содержит ни одной из
   `known_model_substrs` → вышла новая модель / сменился id; напоминаем обновить реестр.
   (Опт-ин: пустой `known_model_substrs` → проверка выключена.)
2. Просрочка сверки — `model_registry_verified_on` старше `model_registry_max_age_days`
   → модель могла ДЕАКТИВИРОВАТЬСЯ (этого по «текущей модели» не увидеть — мы просто на
   ней не работаем); напоминаем пересверить линейку вручную.

Модель сессии берём из поля `model` события SessionStart, а если его нет — из последнего
assistant-сообщения транскрипта (`message.model`). Обе — детерминированные источники,
без сети и без обращения к ИИ. Любая ошибка — тихо (пустой результат), хук не падает.
"""
from __future__ import annotations

import datetime
import json
import os
from typing import List, Optional

from .config import MemoryConfig, get_config
from .messages import msg


def model_from_transcript(transcript_path: str) -> Optional[str]:
    """Последняя модель ассистента из транскрипта (.jsonl) или None (фолбэк к event.model)."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return None
    model: Optional[str] = None
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                m = o.get("message")
                if isinstance(m, dict) and m.get("role") == "assistant" and m.get("model"):
                    model = str(m["model"])
    except OSError:
        return None
    return model


def resolve_model(event: dict, cfg: MemoryConfig) -> Optional[str]:
    """Модель текущей сессии: поле event['model'] → последняя из транскрипта → None."""
    if not isinstance(event, dict):
        return None
    m = event.get("model")
    if m:
        return str(m)
    return model_from_transcript(str(event.get("transcript_path") or ""))


def _is_known(model: str, known) -> bool:
    ml = model.lower()
    return any(k and k.lower() in ml for k in known)


def nudges(cfg: MemoryConfig, model: Optional[str], today: datetime.date) -> List[str]:
    """Список напоминаний реестра моделей (0, 1 или 2 строки)."""
    out: List[str] = []
    # 1) неизвестная модель — только если реестр известных задан (opt-in)
    if cfg.known_model_substrs and model and not _is_known(model, cfg.known_model_substrs):
        out.append(msg(cfg, "model_registry.unknown_model", model=model))
    # 2) просрочка ручной сверки линейки
    if cfg.model_registry_verified_on:
        try:
            d: Optional[datetime.date] = datetime.date.fromisoformat(
                str(cfg.model_registry_verified_on)
            )
        except (ValueError, TypeError):
            d = None
        if d is not None and (today - d).days > cfg.model_registry_max_age_days:
            out.append(msg(cfg, "model_registry.stale", days=(today - d).days, date=d.isoformat()))
    return out


def run(
    event: dict, cfg: Optional[MemoryConfig] = None, today: Optional[datetime.date] = None
) -> str:
    """Готовый текст напоминаний реестра моделей (или '') для встраивания в SessionStart."""
    cfg = cfg or get_config()
    today = today or datetime.date.today()
    return "\n".join(nudges(cfg, resolve_model(event, cfg), today))
