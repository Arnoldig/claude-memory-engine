"""Идемпотентное слияние регистраций хуков движка в settings.json целевого проекта.

Самая хрупкая часть установки — settings.json может уже содержать ЧУЖИЕ хуки (проекта).
Их нельзя затирать. Поэтому слияние:
- добавляет наши записи в нужное событие/matcher, СОХРАНЯЯ чужие;
- идемпотентно: повторный install не плодит дубли (наши записи опознаются по
  подстроке `cme_hook.sh <event>` в command);
- ничего не удаляет.

Функция merge_settings — чистая (dict→dict), покрыта тестом. install.sh лишь читает/пишет файл.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

# (событие, matcher, имя-события-для-cme_hook, timeout). Один источник истины набора хуков.
HOOK_REGISTRATIONS: List[Tuple[str, str, str, int]] = [
    ("UserPromptSubmit", "", "retrieve", 10),
    ("SessionStart", "startup|resume|clear|compact", "session-start", 15),
    ("PreToolUse", "Edit|Write|MultiEdit", "pre-edit-guard", 10),
    ("PostToolUse", "Read|Write|Edit|MultiEdit", "post-record", 10),
    ("PostToolUse", "Write|Edit|MultiEdit", "bloat-check", 10),
    ("PreToolUse", "Agent", "agent-guard", 10),
    ("PostToolUse", "Agent", "agent-log", 10),
    ("PreCompact", "", "pre-compact", 10),
    ("SessionEnd", "clear|resume|logout|prompt_input_exit|bypass_permissions_disabled|other", "session-end", 10),
    ("Stop", "", "stop-check", 10),
]


def _command(hook_script: str, event: str) -> str:
    return f"bash {hook_script} {event}"


def merge_settings(settings: Dict, hook_script_abspath: str) -> Tuple[Dict, int]:
    """Вмёрджить регистрации движка в settings (dict). Возвращает (новый_settings, добавлено).

    Идемпотентно: записи, уже присутствующие (по подстроке `cme_hook.sh <event>`), не
    дублируются. Чужие хуки не трогаются.
    """
    out = json.loads(json.dumps(settings))  # глубокая копия, не мутируем вход
    hooks = out.setdefault("hooks", {})
    added = 0
    for event_name, matcher, ev, timeout in HOOK_REGISTRATIONS:
        groups = hooks.setdefault(event_name, [])
        marker = f"cme_hook.sh {ev}"
        # уже зарегистрировано где-либо в этом событии?
        already = any(
            marker in str(h.get("command", ""))
            for g in groups
            if isinstance(g, dict)
            for h in g.get("hooks", [])
            if isinstance(h, dict)
        )
        if already:
            continue
        entry = {"type": "command", "command": _command(hook_script_abspath, ev), "timeout": timeout}
        # найти группу с тем же matcher; иначе создать
        target = next(
            (g for g in groups if isinstance(g, dict) and g.get("matcher", "") == matcher),
            None,
        )
        if target is None:
            groups.append({"matcher": matcher, "hooks": [entry]})
        else:
            target.setdefault("hooks", []).append(entry)
        added += 1
    return out, added


def load_settings(path: str) -> Dict:
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8") or "{}")
    except ValueError:
        return {}


def write_settings(path: str, settings: Dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def install_into_settings(path: str, hook_script_abspath: str) -> int:
    """Прочитать settings.json, вмёрджить, записать атомарно. Возвращает число добавленных."""
    merged, added = merge_settings(load_settings(path), hook_script_abspath)
    write_settings(path, merged)
    return added


def main() -> None:
    import sys

    if len(sys.argv) < 3:
        print("usage: python3 -m claude_memory.installer <settings.json> <abs/cme_hook.sh>")
        sys.exit(1)
    added = install_into_settings(sys.argv[1], sys.argv[2])
    print(f"settings.json: добавлено хуков движка: {added} (повторные пропущены).")


if __name__ == "__main__":
    main()
