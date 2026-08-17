"""Рассылка универсальных shell-стражей потребителям + сверка дрейфа.

Класс, который закрывается: страж, скопированный в проект РУКАМИ, — отставшая
копия. Она верна в день копирования и устаревает беззвучно: правка в
репозитории движка до ручной копии не доезжает никогда, а тест, который сверял
бы два репозитория разом, невозможен по построению. Замерено 2026-08-17: у
потребителей лежали одноимённые копии пяти стражей; у одного они совпадали с
движком байт-в-байт, при этом очередная правка стража уже была готова в движке
— расхождение было вопросом часов, и ни один прогон тестов его бы не показал.

Механизм из трёх частей:
  • эталоны стражей едут В ПАКЕТЕ (claude_memory/guards/*.sh); их равенство
    рабочим стражам самого репозитория движка держит тест
    test_package_guards_are_byte_identical_to_repo_hooks — пакет не может
    завести собственную отставшую копию;
  • `claude-memory sync-guards` кладёт эталоны в `<проект>/.claude/hooks/` и
    идемпотентно регистрирует их в settings.json (переносимой командой через
    "$CLAUDE_PROJECT_DIR" — см. installer.portable_script_ref);
  • doctor (verbose) называет дрейф вслух: копия в проекте отличается от
    эталона пакета → подсказка запустить sync-guards. Только verbose: проект
    вправе НАМЕРЕННО держать свою редакцию, и жалоба на каждом SessionStart
    заставила бы выключить проверку целиком (потеря тут обратима).

Пять стражей универсальны без параметров: снимок работы и git-страж
репо-нейтральны по коду; у стража приватных слов проектные исключения и так
живут в `.claude/private-words-allow.txt`; доставка правил и уведомление о
дрейфе главной папки опираются только на общую конвенцию `.claude/worktrees/`.
Стражи основания закрытия задач в пакет НЕ едут: они привязаны к трекеру
конкретного репозитория.
"""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import List

from .installer import load_settings, merge_hook_entries, write_settings
from .messages import msg

GUARD_DIR = Path(__file__).parent / "guards"

# (файл, событие, matcher, timeout). Таймауты зеркалят .claude/settings.json
# репозитория движка — равенство держит test_registration_timeouts_match_repo_settings.
GUARD_REGISTRATIONS = [
    ("destructive_git_guard.sh", "PreToolUse", "Bash", 60),
    ("work_snapshot.sh", "PreToolUse", "Bash", 15),
    ("private_words_guard.sh", "PreToolUse", "Bash", 15),
    ("rules_delivery_guard.sh", "SessionStart", "startup|resume|clear|compact", 20),
    ("main_checkout_drift_notice.sh", "SessionStart", "startup|resume|clear|compact", 30),
]


def guard_names() -> List[str]:
    return [name for name, _e, _m, _t in GUARD_REGISTRATIONS]


def _entries():
    """Регистрации в формате installer.merge_hook_entries.

    Маркер идемпотентности — хвост пути `/.claude/hooks/<имя>`: он одинаков и в
    переносимой команде, и в исторической абсолютной, поэтому повторный sync не
    задваивает записи и у проектов, регистрировавших стражей руками."""
    out = []
    for name, event, matcher, timeout in GUARD_REGISTRATIONS:
        command = '"$CLAUDE_PROJECT_DIR"/.claude/hooks/' + name
        out.append((event, matcher, command, timeout, f"/.claude/hooks/{name}"))
    return out


def sync(project_dir: str, register: bool = True, cfg=None) -> List[str]:
    """Донести эталоны стражей до проекта. Возвращает строки для человека.

    Копия перезаписывается ТОЛЬКО при расхождении байтов (лишних правок файлов
    нет), исполняемый бит ставится всегда. register=True дополнительно
    регистрирует стражей в `<проект>/.claude/settings.json` идемпотентно, чужие
    хуки не трогая (та же механика, что у установщика движка)."""
    hooks_dir = Path(project_dir) / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    for name in guard_names():
        src = GUARD_DIR / name
        dst = hooks_dir / name
        if not dst.exists():
            shutil.copyfile(src, dst)
            lines.append(msg(cfg, "sync_guards.installed", name=name))
        elif dst.read_bytes() != src.read_bytes():
            shutil.copyfile(src, dst)
            lines.append(msg(cfg, "sync_guards.updated", name=name))
        else:
            lines.append(msg(cfg, "sync_guards.unchanged", name=name))
        dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if register:
        settings_path = str(Path(project_dir) / ".claude" / "settings.json")
        merged, added = merge_hook_entries(load_settings(settings_path), _entries())
        if added:
            write_settings(settings_path, merged)
        lines.append(msg(cfg, "sync_guards.registered", added=added))
    return lines


def drift_issues(cfg) -> List[str]:
    """Жалобы «копия стража разошлась с эталоном пакета» (для doctor, verbose).

    Отсутствующая копия — НЕ жалоба: страж не установлен, это выбор проекта.
    Fail-open на любой ошибке чтения: диагностика, потеря обратима."""
    out: List[str] = []
    hooks_dir = Path(cfg.project_root) / ".claude" / "hooks"
    for name in guard_names():
        dst = hooks_dir / name
        try:
            if not dst.is_file():
                continue
            if dst.read_bytes() != (GUARD_DIR / name).read_bytes():
                out.append(msg(cfg, "self_check.guard_drift", path=str(dst)))
        except OSError:
            continue
    return out


def main() -> None:
    import sys
    project = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    for line in sync(project):
        print(line)


if __name__ == "__main__":
    main()
