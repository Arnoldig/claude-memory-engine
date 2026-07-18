"""Страж основания закрытия заявки (`.claude/hooks/issue_close_basis_guard.sh`).

ПОЧЕМУ ТЕСТ НА ПРОПУСК ВАЖНЕЕ ТЕСТА НА БЛОК. Первая версия стража слегла на
синтаксической ошибке bash (литеральная обратная кавычка внутри `$( )`), а bash отдаёт
на ней код возврата 2 — ровно тот, которым PreToolUse ОТКЛОНЯЕТ вызов. Полностью
неработающий страж блокировал ВСЁ подряд и проходил все проверки «блокирует ли он что
надо» зелёными. Отсюда две вещи, без которых этот файл бессмысленен:
  • блок засчитывается ТОЛЬКО вместе с текстом объяснения на stderr;
  • пропуск засчитывается ТОЛЬКО при пустом stderr.
Одного кода возврата недостаточно ни в ту, ни в другую сторону.

Страж собран из двух живых хуков рабочих проектов, у каждого из которых своё слепое
пятно; два случая ниже названы по источнику и держат именно эти дыры закрытыми.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "issue_close_basis_guard.sh"
CLOSE = "gh issue close 5"


def _run(payload: str):
    return subprocess.run([str(HOOK)], input=payload, capture_output=True, text=True, timeout=30)


def _blocks(cmd: str, tool: str = "Bash") -> bool:
    """True — отклонено С ОБЪЯСНЕНИЕМ; False — пропущено МОЛЧА. Иное → провал теста."""
    p = _run(json.dumps({"tool_name": tool, "tool_input": {"command": cmd}}))
    if p.returncode == 2 and "нет основания" in p.stderr:
        return True
    if p.returncode == 0 and not p.stderr.strip():
        return False
    pytest.fail(f"страж сломан: код={p.returncode}, stderr={p.stderr[:200]!r}")


def test_hook_is_present_and_executable() -> None:
    assert HOOK.is_file(), f"{HOOK} отсутствует — страж не сработает ни разу"
    assert HOOK.stat().st_mode & 0o111, f"{HOOK} не исполняем — хук молча не запустится"


@pytest.mark.parametrize("cmd", [
    CLOSE,
    CLOSE + " --reason completed",              # --reason не основание, а классификация
    "/opt/homebrew/bin/" + CLOSE,               # путь к бинарю
    "cd /x && " + CLOSE,                        # не в начале строки
    "echo a\n" + CLOSE,                         # с новой строки
    "GH_TOKEN=x " + CLOSE,                      # префикс-присваивание
    "wc -c f && " + CLOSE,                      # ← дыра первого источника: чужой -c сходил за основание
])
def test_blocks_close_without_basis(cmd: str) -> None:
    assert _blocks(cmd)


@pytest.mark.parametrize("cmd", [
    CLOSE + ' --comment "починено в 0.12.0"',
    CLOSE + ' -c "довод"',
    CLOSE + ' --comment="довод"',
    CLOSE + ' && gh issue comment 5 --body "довод"',   # законный второй путь
    'echo "' + CLOSE + '"',                            # фраза, а не команда
    'grep "foo; ' + CLOSE + '" file',                  # ← дыра второго источника: разделитель в кавычках
    "gh issue list",
    "",
])
def test_allows(cmd: str) -> None:
    assert not _blocks(cmd)


def test_ignores_other_tools() -> None:
    assert not _blocks(CLOSE, tool="Edit")


def test_fail_open_on_broken_input() -> None:
    """Страж не вправе ронять чужой вызов из-за неразобранного ввода."""
    p = _run("не json вовсе")
    assert p.returncode == 0 and not p.stderr.strip()
