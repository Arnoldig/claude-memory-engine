"""CLI движка памяти: `claude-memory <команда>` — точка входа pip-пакета (вариант C).

Главная команда — `init`: разворачивает в проект тонкий слой (обёртку-хук, конфиг,
регистрацию хуков в settings.json) ОДНОЙ командой, без клонирования репозитория и без
вшивания пакета в проект. Логика движка остаётся в установленном pip-пакете
(site-packages); сгенерированная обёртка зовёт ТОТ интерпретатор, которым поставлен
пакет (см. ниже, почему фиксируем).

Отличие от install.sh (вариант A): install.sh вшивает копию пакета в
`<project>/.claude/memory_engine/` и обёртка ставит на неё PYTHONPATH. Здесь пакет НЕ
копируется — обёртка ссылается на установленный интерпретатор. Обе формы поставки
совместимы и не мешают друг другу.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

from . import __version__
from .installer import install_into_settings

# Шаблон обёртки для pip-режима. Интерпретатор зафиксирован на установку, из которой
# запускали init: голый `python3` мог бы указывать на ДРУГОЙ python без пакета, и хуки
# (они fail-open) тихо выключились бы — память молча умерла бы без ошибки. Фиксация
# гарантирует, что пакет найдётся. PYTHONPATH на вшитый движок НЕ ставим — пакет лежит
# в site-packages зафиксированного интерпретатора. Конфиг резолвится относительно
# расположения самой обёртки (как в варианте A) — переносимо внутри проекта.
_WRAPPER_TEMPLATE = """\
#!/usr/bin/env bash
# claude-memory-engine — тонкая обёртка хуков (вариант C: pip + CLI).
# Сгенерировано `claude-memory init`. Интерпретатор зафиксирован на ту установку
# claude-memory-engine, из которой запускали init. Сменили окружение/переустановили
# пакет — выполните `claude-memory init` повторно, чтобы перезакрепить.
set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$(cd "$HOOK_DIR/.." && pwd)"

export CLAUDE_MEMORY_CONFIG="${CLAUDE_MEMORY_CONFIG:-$CLAUDE_DIR/claude-memory.config.json}"

exec @@PYTHON@@ -m claude_memory.hooks_cli "$@"
"""


def _render_wrapper(python_exe: str) -> str:
    # shlex.quote на случай пробелов/спецсимволов в пути интерпретатора (напр. venv в
    # каталоге со пробелом). Подстановка через сентинел, а не .format — чтобы не экранировать
    # все `${...}` фигурные скобки bash.
    return _WRAPPER_TEMPLATE.replace("@@PYTHON@@", shlex.quote(python_exe))


def _write_config_if_absent(config_path: Path, memory_dir: str, project_root: str) -> bool:
    """Создаёт минимальный конфиг, если его ещё нет. True — если создал (не затираем чужой)."""
    if config_path.exists():
        return False
    config_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = {"memory_dir": memory_dir, "project_root": project_root}
    config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def _import_ok(python_exe: str) -> bool:
    """Проверяет, что пакет claude_memory импортируется под зафиксированным интерпретатором."""
    try:
        r = subprocess.run(
            [python_exe, "-c", "import claude_memory"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return r.returncode == 0
    except OSError:
        return False


def cmd_init(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir or Path.cwd()).expanduser().resolve()
    memory_dir = Path(args.memory_dir or (Path.home() / ".claude" / "memory")).expanduser()
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_dir = memory_dir.resolve()

    claude_dir = project_dir / ".claude"
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = hooks_dir / "cme_hook.sh"
    config_path = claude_dir / "claude-memory.config.json"
    settings_path = claude_dir / "settings.json"

    python_exe = sys.executable or "python3"

    # 1. обёртка хуков — всегда перезаписываем (она наша, генерируемая)
    wrapper_path.write_text(_render_wrapper(python_exe), encoding="utf-8")
    wrapper_path.chmod(0o755)

    # 2. конфиг — не затираем существующий
    created_cfg = _write_config_if_absent(config_path, str(memory_dir), str(project_dir))

    # 3. регистрация хуков в settings.json — идемпотентно, чужие хуки сохраняются
    added = install_into_settings(str(settings_path), str(wrapper_path))

    print(f"claude-memory init → {project_dir}")
    print(f"  движок (pip):  {python_exe}")
    print(f"  обёртка:       {wrapper_path}")
    print(f"  конфиг:        {config_path}" + ("" if created_cfg else "  (уже был, оставил)"))
    print(f"  settings.json: +{added} регистраций хуков")
    print(f"  память:        {memory_dir}")

    # 4. проверка импорта под зафиксированным интерпретатором — иначе обёртка молча мертва
    if not _import_ok(python_exe):
        print(
            f"\n⚠ пакет claude_memory НЕ импортируется под {python_exe}.\n"
            f"  Установите его в это окружение и повторите init:\n"
            f"      pip install claude-memory-engine",
            file=sys.stderr,
        )
        return 1

    print("✓ пакет claude_memory импортируется под этим интерпретатором")
    print("Готово. Хуки активируются со следующей сессии Claude Code в этом проекте.")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from . import self_check
    from .config import get_config

    issues = self_check.warnings(get_config())
    if not issues:
        print("✓ конфиг в порядке")
        return 0
    for w in issues:
        print(w, file=sys.stderr)
    return 1


def cmd_config(args: argparse.Namespace) -> int:
    from .config import render_cli

    print(render_cli(args.rest))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-memory",
        description="Движок памяти уроков для Claude Code — установка и обслуживание.",
    )
    parser.add_argument(
        "--version", action="version", version=f"claude-memory-engine {__version__}"
    )
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser(
        "init", help="развернуть хуки памяти в проект одной командой (pip-режим)"
    )
    p_init.add_argument(
        "project_dir", nargs="?", default=None,
        help="корень проекта (по умолчанию: текущий каталог)",
    )
    p_init.add_argument(
        "memory_dir", nargs="?", default=None,
        help="каталог уроков памяти (по умолчанию: ~/.claude/memory)",
    )
    p_init.set_defaults(func=cmd_init)

    p_doctor = sub.add_parser("doctor", help="самодиагностика конфига (плейсхолдеры сообщений)")
    p_doctor.set_defaults(func=cmd_doctor)

    p_config = sub.add_parser("config", help="печать конфига целиком или поля: config [get FIELD]")
    p_config.add_argument("rest", nargs=argparse.REMAINDER)
    p_config.set_defaults(func=cmd_config)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
