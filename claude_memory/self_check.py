"""Самодиагностика конфигурации (SessionStart + CLI). Без сети, ноль токенов.

Ловит ошибки настройки, которые иначе тихо портят работу весь сеанс. Сейчас —
сверка плейсхолдеров: в каждом messages-override плейсхолдеры `{x}` должны быть
ПОДМНОЖЕСТВОМ плейсхолдеров дефолта того же ключа. Иначе `.format` не подставит
значение (как было `{len(cards)}` вместо `{card_count}`, `{lines_part}` вместо
`{actual_length_or_multiline}`). `msg()` теперь деградирует на дефолт и не падает,
но текст выходит неверный/английский — поэтому чиним в источнике (конфиге проекта).

Триггер: SessionStart, КАЖДУЮ сессию (не throttle) — битая настройка актуальна,
пока её не исправят, и должна быть видна на старте. Плюс ручной CLI для setup.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from .config import MemoryConfig, get_config
from .messages import DEFAULT_MESSAGES, msg

_PH_RE = re.compile(r"\{([^{}]+)\}")


def _placeholders(template: str) -> set:
    """Имена плейсхолдеров `{x}` в шаблоне (включая невалидные вроде `len(cards)`)."""
    return {m.group(1) for m in _PH_RE.finditer(template)}


def message_placeholder_issues(cfg: MemoryConfig) -> List[Tuple[str, set]]:
    """[(ключ, лишние_плейсхолдеры)] для override'ов, чьи плейсхолдеры НЕ ⊆ дефолта.

    Ключ-сирота (нет в дефолтах) пропускаем — он не ломает форматирование (msg()
    отдаёт его как есть); отдельная гигиена, не предмет этой проверки.
    """
    issues: List[Tuple[str, set]] = []
    overrides = getattr(cfg, "messages", None) or {}
    for key, template in overrides.items():
        default = DEFAULT_MESSAGES.get(key)
        if default is None:
            continue
        extra = _placeholders(str(template)) - _placeholders(default)
        if extra:
            issues.append((key, extra))
    return sorted(issues, key=lambda x: x[0])


def warnings(cfg: MemoryConfig = None) -> List[str]:
    """Готовые строки-предупреждения самодиагностики (пусто, если всё чисто)."""
    cfg = cfg or get_config()
    return [
        msg(cfg, "self_check.bad_placeholder", msg_key=key, extras=", ".join(sorted(extra)))
        for key, extra in message_placeholder_issues(cfg)
    ]


def run(cfg: MemoryConfig = None) -> str:
    """Текст самодиагностики для встраивания в SessionStart (или '')."""
    return "\n".join(warnings(cfg))


def main() -> None:
    """CLI: `python3 -m claude_memory.self_check` — проверить конфиг при настройке проекта."""
    import sys

    cfg = get_config()
    issues = warnings(cfg)
    if not issues:
        print(msg(cfg, "self_check.ok"))
        return
    for w in issues:
        print(w, file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
