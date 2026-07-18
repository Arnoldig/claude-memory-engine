"""Знание о среде-хозяине: где Claude Code держит авто-память и включена ли она.

Всё оффлайн: git-репозитории собираются во временных каталогах, settings.json пишутся
руками, домашний каталог подменяется общей фикстурой `isolated_home` (conftest, autouse) —
ни один тест не читает реальный `~/.claude` автора.

Почему изоляция обязательна именно здесь: `_read_settings` ВСЕГДА добавляет
`~/.claude/settings.json` слабейшей областью. Без подмены HOME тесты «при битом/относительном
значении молчим» проверяли бы не код, а содержимое домашней папки того, кто их запускает:
у автора там нет `autoMemoryDirectory` — зелено; у любого, кто его задал (законная
настройка!), — красные тесты и никакого объяснения. Поймано ревизией 0.10.1 прогоном сюиты
с подменённым HOME: падало пять тестов, из них ТРИ старых, которых правка не касалась.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from claude_memory import claude_code_env as E


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _settings(root: Path, scope: str, data: dict) -> None:
    p = root / scope
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


# ── слаг ────────────────────────────────────────────────────────────────────────

def test_project_slug_ascii() -> None:
    assert E.project_slug("/Users/v/Claude/Website") == "-Users-v-Claude-Website"


def test_project_slug_underscore_becomes_dash() -> None:
    assert E.project_slug("/a/proj_001") == "-a-proj-001"


def test_project_slug_cyrillic_one_dash_per_char() -> None:
    """Каждый символ вне [a-zA-Z0-9] даёт СВОЙ дефис — в т.ч. каждая кириллическая буква.
    Закреплено по боевому пути с кириллицей: `/Тест/` (6 символов) → 6 дефисов."""
    assert E.project_slug("/Users/v/Claude/Тест/Projects/proj_001") == \
        "-Users-v-Claude------Projects-proj-001"


# ── основной чекаут ─────────────────────────────────────────────────────────────

def test_main_checkout_plain_repo(tmp_path: Path) -> None:
    _git_init(tmp_path)
    assert os.path.realpath(E.main_checkout(str(tmp_path))) == os.path.realpath(str(tmp_path))


def test_main_checkout_from_worktree_returns_main(tmp_path: Path) -> None:
    """Из git-worktree обязан вернуться ОСНОВНОЙ чекаут: каталог авто-памяти общий на весь
    репозиторий, и worktree не должен получить свой (иначе память раздробится)."""
    main = tmp_path / "main"
    main.mkdir()
    _git_init(main)
    (main / "f.txt").write_text("x", encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(main), "add", "-A"], check=True, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(main), "commit", "-qm", "init"], check=True, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(wt), "-b", "br"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    assert os.path.realpath(E.main_checkout(str(wt))) == os.path.realpath(str(main))


def test_main_checkout_not_a_repo_is_none(tmp_path: Path) -> None:
    assert E.main_checkout(str(tmp_path / "nope")) is None


# ── форма пути авто-памяти ──────────────────────────────────────────────────────

def test_default_auto_memory_dir_shape_is_pinned_literally(tmp_path: Path, isolated_home: Path) -> None:
    """Форма `~/.claude/projects/<slug>/memory` закреплена ЛИТЕРАЛАМИ сегментов.

    Почему не через вызов проверяемой функции: соседние тесты берут ожидаемое значение из
    самой `default_auto_memory_dir` (им нужен путь, а не его форма) — и такая проверка
    тавтологична. Замени в коде `projects` на `WRONG` — движок начнёт искать уроки в
    несуществующей папке, то есть вернётся ровно тот дефект (`~/.claude/memory`, куда никто
    не пишет), ради которого выпущена 0.10.0, — а тавтологичные тесты останутся зелёными.
    Форма пути — центральный факт релиза, и у неё нет вышестоящего контракта: правило слага
    не задокументировано и выведено обратной инженерией. Значит закреплять надо здесь.
    """
    _git_init(tmp_path)
    got = Path(E.default_auto_memory_dir(str(tmp_path)))

    assert got.name == "memory"
    assert got.parent.parent.name == "projects"
    assert got.parent.parent.parent.name == ".claude"
    assert got.parent.parent.parent.parent == isolated_home
    # слаг — от каталога ОСНОВНОГО чекаута (правило закреплено отдельными тестами выше)
    assert got.parent.name == E.project_slug(E.main_checkout(str(tmp_path)))


# ── подтверждение диском ────────────────────────────────────────────────────────

def test_existing_auto_memory_dir_none_when_empty(tmp_path: Path, monkeypatch) -> None:
    """Пустая (или отсутствующая) папка — НЕ подтверждение: догадка остаётся догадкой."""
    _git_init(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert E.existing_auto_memory_dir(str(tmp_path)) is None


def test_existing_auto_memory_dir_confirmed_by_lessons(tmp_path: Path, monkeypatch) -> None:
    _git_init(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    guess = Path(E.default_auto_memory_dir(str(tmp_path)))
    guess.mkdir(parents=True)
    (guess / "some-lesson.md").write_text("x", encoding="utf-8")

    assert E.existing_auto_memory_dir(str(tmp_path)) == str(guess)
    path, trusted = E.resolve_auto_memory_dir(str(tmp_path))
    assert (path, trusted) == (str(guess), True)


def test_resolve_untrusted_when_nothing_on_disk(tmp_path: Path, monkeypatch) -> None:
    _git_init(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    path, trusted = E.resolve_auto_memory_dir(str(tmp_path))
    assert trusted is False and path.endswith("/memory")


# ── autoMemoryDirectory ─────────────────────────────────────────────────────────

def test_configured_dir_expands_tilde(tmp_path: Path, monkeypatch) -> None:
    """`~/` — законная форма значения у Claude Code, и она обязана разворачиваться:
    иначе сравнение с memory_dir дало бы ложное расхождение и жалобу на ровном месте.
    HOME правим через env: `Path.expanduser` смотрит именно на него, а не на `Path.home`."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _settings(tmp_path, ".claude/settings.json", {"autoMemoryDirectory": "~/mem"})
    got = E.configured_auto_memory_dir(str(tmp_path))
    assert got == (str(home / "mem"), ".claude/settings.json")


def test_configured_dir_local_beats_project(tmp_path: Path) -> None:
    _settings(tmp_path, ".claude/settings.json", {"autoMemoryDirectory": "/from/project"})
    _settings(tmp_path, ".claude/settings.local.json", {"autoMemoryDirectory": "/from/local"})
    assert E.configured_auto_memory_dir(str(tmp_path))[0] == "/from/local"


def test_configured_dir_relative_is_ignored(tmp_path: Path) -> None:
    """Claude Code требует абсолютный путь или `~/`. Относительный не работает и у хозяина —
    молча игнорируем, а не выдаём за настройку."""
    _settings(tmp_path, ".claude/settings.json", {"autoMemoryDirectory": "relative/mem"})
    assert E.configured_auto_memory_dir(str(tmp_path)) is None


def test_broken_json_is_silent(tmp_path: Path) -> None:
    """Fail-open: чужой битый файл не наше дело и не повод падать."""
    p = tmp_path / ".claude" / "settings.json"
    p.parent.mkdir(parents=True)
    p.write_text("{ это не json", encoding="utf-8")
    assert E.configured_auto_memory_dir(str(tmp_path)) is None
    assert E.auto_memory_disabled(str(tmp_path)) is None


def test_missing_settings_is_silent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "nohome"))
    assert E.configured_auto_memory_dir(str(tmp_path)) is None


# ── выключение авто-памяти ──────────────────────────────────────────────────────

def test_auto_memory_disabled_via_settings(tmp_path: Path) -> None:
    _settings(tmp_path, ".claude/settings.json", {"autoMemoryEnabled": False})
    assert E.auto_memory_disabled(str(tmp_path)) == ".claude/settings.json"


def test_auto_memory_enabled_true_is_not_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "nohome"))
    _settings(tmp_path, ".claude/settings.json", {"autoMemoryEnabled": True})
    assert E.auto_memory_disabled(str(tmp_path)) is None


def test_auto_memory_disabled_via_env(tmp_path: Path, monkeypatch) -> None:
    """Env-киллсвитч бьёт мимо settings.json — не проверять его значило бы соврать
    «включена»."""
    monkeypatch.setenv(E.DISABLE_ENV, "1")
    assert E.auto_memory_disabled(str(tmp_path)) == f"env {E.DISABLE_ENV}"


@pytest.mark.parametrize("val", ["", "0", "false", "False"])
def test_env_falsy_values_are_not_disabled(tmp_path: Path, monkeypatch, val: str) -> None:
    monkeypatch.setenv(E.DISABLE_ENV, val)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "nohome"))
    assert E.auto_memory_disabled(str(tmp_path)) is None


# ── сравнение каталогов ─────────────────────────────────────────────────────────

def test_same_dir_via_symlink_and_tilde(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert E.same_dir(str(real), str(link)) is True
    assert E.same_dir(str(real), str(tmp_path / "other")) is False


def test_same_dir_none_is_false() -> None:
    assert E.same_dir(None, "/x") is False
    assert E.same_dir("/x", None) is False
