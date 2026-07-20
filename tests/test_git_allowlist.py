"""При непустом дереве разрешены только перечисленные формы git, остальное блокируется.

Прежнее правило перечисляло ЗАПРЕЩЁННОЕ, и перечень протекал трижды подряд:
сначала нашлись пропущенные написания флагов, потом пять глаголов, которых в
списке не было вовсе, потом одиннадцать обходов через оболочку из двадцати
четырёх. Это свойство приёма, а не недосмотр: список опасного пополняется после
каждой новой потери, то есть всегда задним числом.

Перевёрнутое правило конечно по построению. Замер по журналам сессий: 8431 вызов
git, 85 различных глаголов, из них двадцать покрывают 90% работы. Пробел в
перечне даёт видимую блокировку (снимается за минуту), а не молчаливую утрату.

Половина файла — случаи на ПРОПУСК. Без них набор зеленеет на страже, который
блокирует вообще всё, а таким стражем работать нельзя: его снимут в первый день
вместе с настоящей защитой.
"""
import json
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "destructive_git_guard.sh"


def _грязное(корень: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(корень)], check=True)
    (корень / "f.txt").write_text("сохранено\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(корень), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(корень), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    (корень / "f.txt").write_text("НЕСОХРАНЁННАЯ ПРАВКА\n", encoding="utf-8")
    return корень


def _блокирует(cmd: str, cwd: Path) -> bool:
    p = subprocess.run(
        [str(HOOK)],
        input=json.dumps({"tool_name": "Bash", "cwd": str(cwd),
                          "tool_input": {"command": cmd}}),
        capture_output=True, text=True, timeout=30)
    if p.returncode == 2 and p.stderr.strip():
        return True
    if p.returncode == 0 and not p.stderr.strip():
        return False
    pytest.fail(f"страж сломан: код={p.returncode}, stderr={p.stderr[:200]!r}")


# ── Разрешённое: замерено по журналам, 90% работы ───────────────────────────

РАЗРЕШЁННЫЕ = [
    "git status", "git log --oneline -5", "git diff", "git show HEAD",
    "git rev-parse HEAD", "git branch", "git remote -v", "git ls-files",
    "git merge-base main HEAD", "git rev-list --count HEAD", "git check-ignore -v f.txt",
    "git add -A", "git commit -m 'правка'", "git push origin main", "git fetch origin",
    "git worktree list", "git config --get user.email", "git stash list",
]


@pytest.mark.parametrize("cmd", РАЗРЕШЁННЫЕ)
def test_разрешённые_формы_проходят(cmd, tmp_path) -> None:
    """ПАРНЫЕ на пропуск. Это ежедневная работа — блокировать её нельзя."""
    assert not _блокирует(cmd, _грязное(tmp_path / "р")), (
        f"{cmd} — обычная работа, страж не вправе мешать")


# ── Не перечисленное: блокируется, даже если сегодня безобидно ──────────────

НЕ_РАЗРЕШЁННЫЕ = [
    ("git checkout-index -a -f", "глагола нет в перечне вовсе"),
    ("git read-tree -u --reset HEAD", "то же"),
    ("git apply -R правка.patch", "то же"),
    ("git submodule deinit -f sub", "то же"),
    ("git filter-branch -f --tree-filter true HEAD", "то же"),
    ("git reflog expire --expire=now --all", "то же"),
]


@pytest.mark.parametrize("cmd,почему", НЕ_РАЗРЕШЁННЫЕ, ids=[c.split()[1] for c, _ in НЕ_РАЗРЕШЁННЫЕ])
def test_неперечисленное_блокируется(cmd, почему, tmp_path) -> None:
    """Смысл перевёрнутого правила: неизвестное останавливаем, а не пропускаем.

    Часть этих команд сегодня безвредна — git сам откажется. Но проверять это
    заново после каждого обновления git никто не будет, а цена ошибки необратима.
    """
    assert _блокирует(cmd, _грязное(tmp_path / "н")), f"{cmd}: {почему}"


def test_обход_через_переменную_блокируется(tmp_path) -> None:
    """Раньше это был обход: глагол собирался подстановкой, шаблон его не видел.

    При перечислении разрешённого «не разобрал» означает БЛОК, а не пропуск, —
    в этом главный выигрыш перевода.
    """
    for cmd in ("G=git; $G checkout -- .", "git $(echo checkout) -- .",
                'git chec""kout -- .'):
        assert _блокирует(cmd, _грязное(tmp_path / f"о{hash(cmd) % 999}")), cmd


def test_согласование_владельца_снимает_блок(tmp_path) -> None:
    """ПАРНЫЙ на пропуск: без обхода страж становится тупиком, а тупик снимают
    выключением стража целиком."""
    assert not _блокирует("git checkout-index -a -f  # согласовано с владельцем",
                          _грязное(tmp_path / "с"))


def test_на_чистом_дереве_разрешено_всё(tmp_path) -> None:
    """ПАРНЫЙ на пропуск и главная граница: терять нечего — мешать не за чем."""
    чистое = tmp_path / "ч"
    subprocess.run(["git", "init", "-q", str(чистое)], check=True)
    (чистое / "f.txt").write_text("всё сохранено\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(чистое), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(чистое), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)

    for cmd in ("git checkout-index -a -f", "git clean -xfd", "git reset --hard"):
        assert not _блокирует(cmd, чистое), f"{cmd} на чистом дереве ничего не теряет"


# ── checkout: ветка или файл, спрашиваем сам git ────────────────────────────

def test_checkout_ветки_проходит(tmp_path) -> None:
    """ПАРНЫЙ на пропуск: переключение ветки правок не теряет."""
    репо = _грязное(tmp_path / "в")
    subprocess.run(["git", "-C", str(репо), "branch", "другая"], check=True)

    assert not _блокирует("git checkout другая", репо)
    assert not _блокирует("git checkout -b новая", репо)


def test_checkout_файла_блокируется(tmp_path) -> None:
    """Исходная причина потери: аргумент — путь, правка затирается молча."""
    репо = _грязное(tmp_path / "ф")

    assert _блокирует("git checkout f.txt", репо)
    assert _блокирует("git checkout -- .", репо)
