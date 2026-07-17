"""Справка `install.sh --help` обязана быть ЦЕЛОЙ и не врать.

Зачем этот модуль. Справка собирается из головного комментария самого скрипта. Раньше
диапазон был зашит НОМЕРАМИ строк (`sed -n '2,16p'`), и это молча ломалось при любой
правке шапки: добавили строку — хвост справки (весь раздел Usage) переставал печататься,
а `--help` продолжал что-то выводить. Обрезанная справка неотличима от полной, если не
знаешь, что должно быть ниже. Ровно тот класс, что и весь релиз 0.10.x: сообщение
выглядит исправным и молча недоговаривает.

Поймано dogfood'ом: правя шапку в этом же релизе, я сдвинула строки и сама же обрезала
Usage — и заметила это только потому, что печатала справку глазами.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = ROOT / "install.sh"


def _help() -> str:
    r = subprocess.run(["bash", str(INSTALL_SH), "--help"], capture_output=True, text=True, timeout=15)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_help_is_not_truncated() -> None:
    """Справка обязана дойти до КОНЦА шапки, а не до зашитого номера строки.

    `Usage:` — последний раздел головного комментария. Если его нет, справка обрезана."""
    out = _help()
    assert "Usage:" in out, "справка обрезана — раздел Usage не напечатан"
    assert "PROJECT_DIR" in out and "MEMORY_DIR" in out


def test_help_covers_every_comment_line_of_the_header() -> None:
    """Ни одна строка шапки не теряется: печатается весь ведущий блок `#`.

    Проверяем по факту, а не по счётчику: берём сами строки шапки из файла."""
    lines = INSTALL_SH.read_text(encoding="utf-8").splitlines()
    header = []
    for line in lines[1:]:
        if not line.startswith("#"):
            break
        header.append(line[2:] if line.startswith("# ") else line[1:])

    out = _help()
    missing = [h for h in header if h.strip() and h not in out]
    assert missing == [], f"эти строки шапки не попали в --help: {missing[:3]}"


def test_help_does_not_promise_creating_the_memory_dir() -> None:
    """Скрипт НЕ создаёт каталог памяти по выведенному пути — и не должен обещать этого.

    Пустышка рядом с настоящей папкой замаскировала бы жалобу самодиагностики о неверном
    пути, поэтому по догадке каталог не создаётся намеренно (создаётся только при явно
    переданном MEMORY_DIR)."""
    out = _help()
    assert "creates the memory directory if it does not exist yet" not in out
    assert "LOCATES the lessons directory" in out


def test_help_mentions_the_verification_command() -> None:
    """Человеку, который ставит движок, нужен способ проверить путь — иначе разъезд
    каталогов выглядит как «всё хорошо, уроков просто нет»."""
    assert "claude_memory.self_check" in _help()


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_both_help_flags_work(flag: str) -> None:
    r = subprocess.run(["bash", str(INSTALL_SH), flag], capture_output=True, text=True, timeout=15)
    assert r.returncode == 0 and "Usage:" in r.stdout
