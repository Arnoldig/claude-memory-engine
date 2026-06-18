"""Журнал запусков суб-агентов для замера эффективности делегирования.

PostToolUse-хук на инструмент `Agent` пишет ОДНУ JSONL-строку на каждый спавн →
накапливается распределение (model пустой=наследует / haiku / sonnet / ..., рутинный
ли тип, длина промта). Анализ — периодически: много INHERITED → страж выбора модели
недостаёт; короткий промт + сильная модель → стоило читать самому. Это превращает
«оправдана ли схема младших суб-агентов» из догадки в замеримый факт.

Fail-OPEN всегда: замер НЕ должен ломать работу. Любая ошибка → молча ничего не пишем.

Рутинные типы берутся из конфига (тот же источник, что у стража выбора модели), чтобы
флаг `routine` означал ровно то, что страж считает рутиной.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .config import get_config


def format_record(
    session_id: str,
    tool_input: object,
    now_iso: str,
    routine_types: Optional[frozenset] = None,
    default_type: Optional[str] = None,
) -> Optional[str]:
    """Чистая логика (без файловой системы): JSONL-строка о спавне или None.

    None — если tool_input не dict. `model` пустой → "INHERITED" (наследование модели
    главного потока — то, что мы и хотим ловить замером). routine_types по умолчанию —
    из конфига.

    Опущенный `subagent_type` → harness берёт default_type (general-purpose) → пишем его
    в `type` (журнал честно отражает, что РЕАЛЬНО запустилось — рутинный general-purpose,
    а не «?»-не-рутина) и ставим `type_implicit: true`, чтобы анализ «забыл указать тип»
    оставался виден. default_type по умолчанию — из конфига.
    """
    if not isinstance(tool_input, dict):
        return None
    if routine_types is None:
        routine_types = frozenset(get_config().routine_subagent_types)
    raw_type = str(tool_input.get("subagent_type") or "").strip()
    if raw_type:
        subagent_type = raw_type
    else:
        if default_type is None:
            default_type = get_config().default_subagent_type
        subagent_type = default_type
    model = str(tool_input.get("model") or "").strip() or "INHERITED"
    prompt = tool_input.get("prompt")
    prompt_chars = len(prompt) if isinstance(prompt, str) else 0
    record = {
        "ts": now_iso,
        "session": session_id or "nosess",
        "type": subagent_type,
        "type_implicit": not raw_type,
        "model": model,
        "routine": subagent_type in routine_types,
        "prompt_chars": prompt_chars,
    }
    return json.dumps(record, ensure_ascii=False) + "\n"


def append_record(log_path: str, line: Optional[str]) -> bool:
    """Дописать строку в журнал (создаёт файл/каталог). Fail-open: ошибка → False.

    Пустая строка/None — no-op (False). Append-режим: конкуррентные дозаписи коротких
    строк атомарны на практике (одна строка < PIPE_BUF), общего lock'а не требуют.
    """
    if not line:
        return False
    try:
        p = Path(log_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(line)
        return True
    except OSError:
        return False
