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

from .messages import msg

# (событие, matcher, имя-события-для-cme_hook, timeout). Один источник истины набора хуков.
HOOK_REGISTRATIONS: List[Tuple[str, str, str, int]] = [
    ("UserPromptSubmit", "", "retrieve", 10),
    ("SessionStart", "startup|resume|clear|compact", "session-start", 15),
    ("PreToolUse", "Edit|Write|MultiEdit", "pre-edit-guard", 10),
    ("PostToolUse", "Read|Write|Edit|MultiEdit", "post-record", 10),
    ("PostToolUse", "Write|Edit|MultiEdit", "bloat-check", 10),
    # Bash: замечаем совершённое `gh issue close` (второй источник сигнала о закрытии
    # задачи — коммита такая команда не создаёт, и до 0.13.0 страж её не видел).
    ("PostToolUse", "Bash", "issue-close-watch", 10),
    ("PreToolUse", "Agent", "agent-guard", 10),
    ("PostToolUse", "Agent", "agent-log", 10),
    # Суб-агент не наследует ни правил проекта, ни уроков — и подбор до него не доходит
    # никогда: он печатается на UserPromptSubmit, а у суб-агента такого события нет.
    # Matcher ПУСТОЙ намеренно: перечень типов, которым правила не достаются, задаёт
    # клиент, он уже менялся между версиями, и выпавший из перечня тип остался бы без
    # указателей молча. См. заявку #17 и `tests/test_subagent_start_context.py`.
    ("SubagentStart", "", "subagent-start", 10),
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
    if not isinstance(settings, dict):
        settings = {}                       # валидный, но не-объектный JSON → как пустой
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
        data = json.loads(p.read_text(encoding="utf-8") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}  # валидный, но не-объектный JSON → {}


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


# Маркеры наших записей — те же, что использует merge для идемпотентности
# (`cme_hook.sh <event>`). Один источник истины: набор событий из HOOK_REGISTRATIONS.
_ENGINE_MARKERS: Tuple[str, ...] = tuple(f"cme_hook.sh {ev}" for _, _, ev, _ in HOOK_REGISTRATIONS)


def _is_engine_hook(hook: object) -> bool:
    """Запись хука — наша? Опознаётся по той же подстроке, что добавляет merge_settings."""
    if not isinstance(hook, dict):
        return False
    command = str(hook.get("command", ""))
    return any(marker in command for marker in _ENGINE_MARKERS)


def remove_engine_hooks(settings: Dict) -> Tuple[Dict, int]:
    """Обратная операция к merge_settings: снять ВСЕ регистрации движка, сохранив чужие.

    Запись опознаётся по тому же маркеру `cme_hook.sh <event>`, что и при установке.
    Группа (matcher), в которой не осталось хуков из-за нашего удаления, выбрасывается;
    ключ `hooks` удаляется, если в нём не осталось ни одного события. Чужие хуки и ранее
    пустые группы не трогаются. Возвращает (новый_settings, число_снятых).
    """
    if not isinstance(settings, dict):
        return {}, 0                        # валидный, но не-объектный JSON → снимать нечего
    out = json.loads(json.dumps(settings))  # глубокая копия, не мутируем вход
    hooks = out.get("hooks")
    if not isinstance(hooks, dict):
        return out, 0
    removed = 0
    for event_name in list(hooks.keys()):
        groups = hooks.get(event_name)
        if not isinstance(groups, list):
            continue
        kept_groups: List = []
        for g in groups:
            if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
                kept_groups.append(g)  # чужая/нестандартная запись — не трогаем
                continue
            entries = g["hooks"]
            kept = [h for h in entries if not _is_engine_hook(h)]
            removed += len(entries) - len(kept)
            if kept:
                g["hooks"] = kept
                kept_groups.append(g)
            elif not entries:
                kept_groups.append(g)  # группа была пуста ДО нас — сохраняем как есть
            # иначе: в группе были только наши хуки — выбрасываем её целиком
        if kept_groups:
            hooks[event_name] = kept_groups
        else:
            del hooks[event_name]
    if not hooks:
        out.pop("hooks", None)
    return out, removed


def uninstall_from_settings(path: str) -> int:
    """Прочитать settings.json, снять регистрации движка, записать атомарно (если были правки).
    Возвращает число снятых регистраций (0 — файла нет или наших хуков не было)."""
    cleaned, removed = remove_engine_hooks(load_settings(path))
    if removed:
        write_settings(path, cleaned)
    return removed


def main() -> None:
    import sys

    if len(sys.argv) < 3:
        print(msg(None, "installer.usage"))
        sys.exit(1)
    added = install_into_settings(sys.argv[1], sys.argv[2])
    print(msg(None, "installer.done", added=added))


if __name__ == "__main__":
    main()
