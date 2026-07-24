"""Сессия читает правила и стражей с диска — и не знает, свежее ли то, что читает.

Половина случаев здесь — на ПРОПУСК. Страж, который шумит без повода, отключают вместе
с настоящими находками; страж, молчащий всегда, неотличим от исправного. Оба вида
случаев обязательны ещё и потому, что bash отдаёт код 0 и при сломанном разборе: набор
из одних «сработал» зеленел бы и на мёртвом хуке.

ЗАМЕР, РАДИ КОТОРОГО НАПИСАНО (2026-07-24, семь рабочих копий этого репозитория):
отставание от общей ветки 0, 18, 24, 24, 58, 66 и 78 коммитов; три копии держали НОЛЬ
стражей и ни одного файла настроек, ещё две — четыре стража из шести. Правила в этих
копиях свежие (доставлены ссылкой) и утверждают, что стражи работают.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "main_checkout_drift_notice.sh"

_GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(каталог: Path, *аргументы: str) -> str:
    p = subprocess.run(["git", "-C", str(каталог), *аргументы],
                       capture_output=True, text=True, env=_GIT_ENV, timeout=60)
    assert p.returncode == 0, f"git {' '.join(аргументы)}: {p.stderr}"
    return p.stdout


def _запустить(cwd: str) -> str:
    p = subprocess.run([str(HOOK)], input=json.dumps({"cwd": cwd}),
                       capture_output=True, text=True, timeout=60,
                       env={**os.environ, "CHECKOUT_DRIFT_STRICT": "1"})
    assert p.returncode == 0, "SessionStart не имеет права ронять старт сессии"
    assert p.stderr == "", f"хук споткнулся молча: {p.stderr}"
    return p.stdout


def _проект(tmp_path: Path) -> tuple:
    """Главная папка + удалённый репозиторий, из которого она отводится."""
    удалённый = tmp_path / "origin.git"
    _bare(удалённый)
    главная = tmp_path / "главная"
    главная.mkdir()
    _git(главная, "init", "-q", "-b", "main", ".")
    (главная / ".claude" / "hooks").mkdir(parents=True)
    (главная / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    (главная / ".claude" / "hooks" / "первый_guard.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (главная / ".claude" / "hooks" / "второй_guard.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    _git(главная, "remote", "add", "origin", str(удалённый))
    _git(главная, "add", "-A")
    _git(главная, "commit", "-qm", "init")
    _git(главная, "push", "-q", "origin", "main")
    return главная, удалённый


def _bare(путь: Path) -> None:
    p = subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(путь)],
                       capture_output=True, text=True, env=_GIT_ENV, timeout=60)
    assert p.returncode == 0, p.stderr


def _увести_общую_ветку_вперёд(главная: Path, удалённый: Path, tmp_path: Path) -> None:
    """Чужая работа слита в общую ветку — главная папка про это ещё не знает."""
    чужая = tmp_path / "чужая"
    _git(tmp_path, "clone", "-q", str(удалённый), str(чужая))
    (чужая / "новое.txt").write_text("x\n", encoding="utf-8")
    _git(чужая, "add", "-A")
    _git(чужая, "commit", "-qm", "чужая работа")
    _git(чужая, "push", "-q", "origin", "main")


def test_отставшая_главная_папка_названа(tmp_path) -> None:
    главная, удалённый = _проект(tmp_path)
    _увести_общую_ветку_вперёд(главная, удалённый, tmp_path)

    вывод = _запустить(str(главная))

    assert "отстала" in вывод and "1 коммит" in вывод
    assert str(главная) in вывод, "без пути непонятно, какую именно папку тянуть"


def test_молчит_когда_главная_папка_свежая(tmp_path) -> None:
    """ПАРНЫЙ на пропуск: всё синхронно — говорить не о чем."""
    главная, _удалённый = _проект(tmp_path)

    assert _запустить(str(главная)) == ""


def test_копия_без_стражей_названа(tmp_path) -> None:
    """Главный замеренный случай: правила свежие, а стражей в копии нет ни одного."""
    главная, _удалённый = _проект(tmp_path)
    копия = главная / ".claude" / "worktrees" / "wt"
    _git(главная, "worktree", "add", "-q", str(копия), "-b", "ветка")
    for мусор in (копия / ".claude" / "hooks").glob("*.sh"):
        мусор.unlink()
    (копия / ".claude" / "settings.json").unlink()

    вывод = _запустить(str(копия))

    assert "settings.json" in вывод, "молчание тут неотличимо от «стражи на месте»"
    assert "первый_guard.sh" in вывод and "второй_guard.sh" in вывод


def test_разошедшийся_страж_назван(tmp_path) -> None:
    главная, _удалённый = _проект(tmp_path)
    копия = главная / ".claude" / "worktrees" / "wt"
    _git(главная, "worktree", "add", "-q", str(копия), "-b", "ветка")
    (копия / ".claude" / "hooks" / "первый_guard.sh").write_text("#!/bin/sh\nстарый\n",
                                                                 encoding="utf-8")

    вывод = _запустить(str(копия))

    assert "РАЗОШЛИСЬ" in вывод and "первый_guard.sh" in вывод


def test_молчит_когда_копия_совпадает_с_главной(tmp_path) -> None:
    """ПАРНЫЙ на пропуск: рабочая ветка от свежей общей — шуметь не о чем.

    Отставание ветки на коммиты здесь НАМЕРЕННО не считается поводом: у рабочей ветки
    оно нормально, и сообщение о нём было бы шумом на каждой сессии.
    """
    главная, _удалённый = _проект(tmp_path)
    копия = главная / ".claude" / "worktrees" / "wt"
    _git(главная, "worktree", "add", "-q", str(копия), "-b", "ветка")

    assert _запустить(str(копия)) == ""


def test_молчит_вне_репозитория(tmp_path) -> None:
    """ПАРНЫЙ на пропуск: не репозиторий — не наше дело."""
    обычный = tmp_path / "просто-папка"
    обычный.mkdir()

    assert _запустить(str(обычный)) == ""


@pytest.mark.parametrize("ввод", ["не json", "", "null", '{"cwd": "/нет/такого"}', "[]"])
def test_мусор_на_входе_проходит_молча(ввод) -> None:
    """ПАРНЫЙ на пропуск: fail-open. Ошибка хука не смеет ронять старт сессии."""
    p = subprocess.run([str(HOOK)], input=ввод, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0
    assert p.stdout == ""
