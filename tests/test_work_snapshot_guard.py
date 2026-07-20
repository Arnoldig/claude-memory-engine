"""Снимок несохранённой работы перед любой командой, которая может её унести.

Зачем отдельный слой. Страж git-команд не покрывает потерю не через git: `rm -rf`,
`sed -i`, перенаправление в файл, скрипт на python. Прецедент 2026-07-19 был
git-командой, но класс шире, и перечнем команд его не закрыть — обходы находятся
быстрее, чем пишутся шаблоны.

Снимок меняет постановку: вместо «предотвратить потерю» — «сделать потерю
восстановимой». Работа кладётся в служебную ссылку git, рабочее дерево при этом
не трогается вовсе.

Границы названы вслух:
  • берутся ОТСЛЕЖИВАЕМЫЕ изменения (`git stash create`). Неотслеживаемые файлы
    в снимок не попадают — это ограничение самого git, и оно названо, а не скрыто;
  • снимок не отменяет стражей: он последний рубеж, когда всё прочее пропустило.
"""
import json
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "work_snapshot.sh"
ССЫЛКИ = "refs/claude-snapshots"


def _git(репо: Path, *args) -> str:
    return subprocess.run(["git", "-C", str(репо)] + list(args),
                          capture_output=True, text=True).stdout.strip()


def _репо(корень: Path, грязное: bool = True) -> Path:
    subprocess.run(["git", "init", "-q", str(корень)], check=True)
    (корень / "f.txt").write_text("сохранено\n", encoding="utf-8")
    _git(корень, "add", "-A")
    _git(корень, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    if грязное:
        (корень / "f.txt").write_text("ВАЖНАЯ НЕСОХРАНЁННАЯ ПРАВКА\n", encoding="utf-8")
    return корень


def _вызвать(команда: str, репо: Path) -> subprocess.CompletedProcess:
    вход = json.dumps({"tool_name": "Bash", "cwd": str(репо),
                       "tool_input": {"command": команда}})
    return subprocess.run([str(HOOK)], input=вход, capture_output=True,
                          text=True, timeout=30)


def _снимки(репо: Path) -> list:
    вывод = _git(репо, "for-each-ref", "--format=%(refname)", ССЫЛКИ)
    return [s for s in вывод.splitlines() if s.strip()]


def test_снимок_делается_перед_опасной_командой(tmp_path) -> None:
    репо = _репо(tmp_path / "репо")
    p = _вызвать("rm -rf f.txt", репо)

    assert p.returncode == 0, "снимок не имеет права мешать работе"
    assert _снимки(репо), "снимок не сделан — терять было что"


def test_работа_восстановима_из_снимка(tmp_path) -> None:
    """ГЛАВНОЕ свойство: после потери содержимое достаётся обратно."""
    репо = _репо(tmp_path / "репо")
    _вызвать("rm -rf f.txt", репо)
    ссылка = _снимки(репо)[0]

    subprocess.run("rm -f f.txt", shell=True, cwd=str(репо), timeout=30)
    assert not (репо / "f.txt").exists(), "работа уничтожена"

    вернулось = _git(репо, "show", f"{ссылка}:f.txt")
    assert вернулось == "ВАЖНАЯ НЕСОХРАНЁННАЯ ПРАВКА", "из снимка достаётся не то"


def test_рабочее_дерево_не_тронуто(tmp_path) -> None:
    """Снимок обязан быть незаметным: он не должен ни прятать правки, ни менять их."""
    репо = _репо(tmp_path / "репо")
    до = (репо / "f.txt").read_text()

    _вызвать("rm -rf f.txt", репо)

    assert (репо / "f.txt").read_text() == до, "снимок изменил рабочее дерево"
    assert _git(репо, "status", "--porcelain"), "снимок спрятал несохранённое"


# ── ПАРНЫЕ случаи на ПРОПУСК ────────────────────────────────────────────────

def test_на_чистом_дереве_снимка_нет(tmp_path) -> None:
    """Терять нечего — служебные ссылки не плодим."""
    репо = _репо(tmp_path / "репо", грязное=False)
    p = _вызвать("rm -rf f.txt", репо)

    assert p.returncode == 0
    assert not _снимки(репо)


def test_читающая_команда_снимка_не_требует(tmp_path) -> None:
    """Команда ничего не меняет — снимок был бы лишней работой на каждый вызов."""
    репо = _репо(tmp_path / "репо")
    _вызвать("git status", репо)

    assert not _снимки(репо)


@pytest.mark.parametrize("мусор", ["не json", "", "null", "[]", '{"tool_name":"Read"}'])
def test_мусор_на_входе_проходит_молча(мусор) -> None:
    """fail-open: ошибка снимка не смеет ронять работу."""
    p = subprocess.run([str(HOOK)], input=мусор, capture_output=True,
                       text=True, timeout=30)
    assert p.returncode == 0
    assert not p.stdout.strip()


def test_вне_репозитория_проходит(tmp_path) -> None:
    вне = tmp_path / "просто-папка"
    вне.mkdir()
    p = subprocess.run([str(HOOK)],
                       input=json.dumps({"tool_name": "Bash", "cwd": str(вне),
                                         "tool_input": {"command": "rm -rf x"}}),
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0
