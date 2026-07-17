"""Инварианты репозитория, которые раньше держались на памяти человека.

Общая мысль файла. У проекта накопился класс граблей: значение обязано совпадать в ДВУХ
местах, ничто этого не проверяет, а расхождение МОЛЧАЛИВОЕ — не падает, не жалуется,
видно только глазами и только если знать, куда смотреть. Каждая такая грабля уже стреляла:

  • `examples/*.json` vs дефолты `config.py` — «синхронь» записано в памяти проекта с
    v0.7.3, и всё равно 0.10.0 вышла со СТАРЫМ `task_close_pattern` (закрыто отдельным
    файлом `test_examples_sync.py`);
  • тег релиза vs версия пакета — PyPI застрял на 0.9.2 при коде 0.9.6 (закрыто
    `.githooks/pre-push`);
  • таблица модулей в README vs состав `claude_memory/` — поймано ВЛАДЕЛЬЦЕМ глазами
    (закрыто здесь);
  • копия правила закрытия в `conftest` vs дефолт — тесты гонялись на правиле, которого
    в проде нет (закрыто здесь).

Вывод, записанный в память проекта: **повторившаяся грабля должна становиться проверкой,
а не ещё одним абзацем в заметке.** Этот файл — исполнение того правила.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
READMES = (ROOT / "README.md", ROOT / "README.en.md")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── версия в двух местах ────────────────────────────────────────────────────────

def test_version_matches_in_both_places() -> None:
    """`pyproject.toml` и `claude_memory/__init__.py` обязаны нести ОДНУ версию.

    Расходятся молча: пакет соберётся с версией из pyproject, а `__version__` (его печатает
    `claude-memory --version` и берут тесты) останется старым. Человек проверит одно место
    и решит, что выпустил."""
    pj = re.search(r'^version = "(.+?)"', _read(ROOT / "pyproject.toml"), re.M)
    ini = re.search(r'^__version__ = "(.+?)"', _read(ROOT / "claude_memory" / "__init__.py"), re.M)
    assert pj and ini, "версия не найдена в одном из мест"
    assert pj.group(1) == ini.group(1), (
        f"версии разошлись: pyproject.toml={pj.group(1)}, __init__.py={ini.group(1)}"
    )


# ── таблица модулей в README ────────────────────────────────────────────────────

@pytest.mark.parametrize("readme", READMES, ids=lambda p: p.name)
def test_every_module_is_listed_in_readme(readme: Path) -> None:
    """Каждый модуль пакета упомянут в README (оба языка).

    README описывает, ИЗ ЧЕГО состоит движок. Завёл модуль — таблица обязана о нём знать,
    иначе README тихо врёт о составе. Поймано владельцем: 0.10.0 добавила `lesson_files` и
    `claude_code_env`, в таблицу они не попали; `model_registry_guard` не попал ещё раньше.
    """
    text = _read(readme)
    mods = sorted(
        f[:-3] for f in os.listdir(ROOT / "claude_memory")
        if f.endswith(".py") and f != "__init__.py"
    )
    missing = [m for m in mods if f"`{m}`" not in text]
    assert missing == [], (
        f"{readme.name}: модули пакета не упомянуты: {missing}\n"
        f"Добавьте их в таблицу возможностей — README описывает состав движка."
    )


# ── бейдж тестов ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("readme", READMES, ids=lambda p: p.name)
def test_tests_badge_matches_real_count(readme: Path) -> None:
    """Число в бейдже `tests-NNN` равно реальному числу тестов.

    Дата-бомба ручной синхронизации: за один день 0.10.0→0.10.1 бейдж правился руками
    четырежды (200+ → 364 → 377 → 381 → 387 → 390) и каждый раз устаревал к следующему
    коммиту. Либо число проверяется, либо его не должно быть.

    Считаем СБОРОМ (`--collect-only`), а не запуском: быстро и без рекурсии.
    """
    m = re.search(r"tests-(\d+)-", _read(readme))
    assert m, f"{readme.name}: бейдж tests-NNN не найден"
    claimed = int(m.group(1))

    out = subprocess.run(
        ["python3", "-m", "pytest", str(ROOT / "tests"), "--collect-only", "-q"],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": ""},
    ).stdout
    real = re.search(r"(\d+) tests? collected", out)
    assert real, f"не удалось посчитать тесты: {out[-300:]}"
    assert claimed == int(real.group(1)), (
        f"{readme.name}: бейдж обещает {claimed} тестов, реально {real.group(1)}"
    )


# ── копия правила закрытия в тестах ─────────────────────────────────────────────

def test_conftest_pattern_covers_all_github_keywords() -> None:
    """Проектный шаблон из `conftest` знает все девять слов-закрытий GitHub — как дефолт.

    `RU_EN_CLOSE_PATTERN` — копия правила «задача закрыта», на которой гоняются тесты. Она
    отстала от дефолта: 0.10.0 научила код семье `resolve`, копия осталась с шестью словами
    — и тесты проверяли движок правилом, которого в проде нет. Сверяем не строку (копия
    ЛОКАЛИЗОВАНА и обязана отличаться), а ПОВЕДЕНИЕ на формах закрытия.
    """
    from claude_memory.stop_check import extract_closed_task
    from conftest import RU_EN_CLOSE_PATTERN

    nine = ("Close", "Closes", "Closed", "Fix", "Fixes", "Fixed",
            "Resolve", "Resolves", "Resolved")
    for word in nine:
        assert extract_closed_task(f"feat: {word} #42", RU_EN_CLOSE_PATTERN) == "42", (
            f"conftest-шаблон не узнаёт `{word} #42` — тесты гоняются на устаревшем правиле"
        )
    # локализованная ветка — то, ради чего копия вообще существует
    assert extract_closed_task("#widget-7 закрыт", RU_EN_CLOSE_PATTERN) == "widget-7"


# ── поддержка Python ────────────────────────────────────────────────────────────

def test_ci_tests_the_oldest_supported_python() -> None:
    """CI обязан гонять тесты на САМОЙ СТАРОЙ заявленной версии Python.

    `pyproject.toml` обещает `requires-python = ">=3.9"`, а workflow гонял `python-version:
    "3.x"` — то есть самую свежую. Обещание никем не проверялось: синтаксис 3.10+ (например
    `X | Y` в аннотациях) прошёл бы CI зелёным и сломался бы у половины пользователей при
    установке. Молча — на их стороне, не на нашей.
    """
    pj = _read(ROOT / "pyproject.toml")
    m = re.search(r'requires-python = ">=(\d+\.\d+)"', pj)
    assert m, "requires-python не найден в pyproject.toml"
    oldest = m.group(1)

    workflows = list((ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows, "нет workflow-файлов"
    covered = any(f'"{oldest}"' in _read(w) or f"'{oldest}'" in _read(w) for w in workflows)
    assert covered, (
        f"pyproject обещает Python >={oldest}, но ни один workflow не гоняет тесты на {oldest} "
        f"(гонять только свежий — значит не проверять обещание)"
    )
