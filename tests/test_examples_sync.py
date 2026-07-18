"""Файл-пример настроек обязан быть синхронен с кодом.

Зачем этот модуль. `examples/claude-memory.config.json` — это то, что люди КОПИРУЮТ себе
при подключении движка. Расхождение примера с кодом не ломает тесты, не ломает сборку и
не видно на ревю — но каждый, кто скопировал пример, получает поведение старой версии.
Молча: ровно тот класс дефекта, вокруг которого крутится вся эта библиотека.

Живой прецедент (0.10.0). Дефолт `task_close_pattern` в коде починили — он стал узнавать
все ДЕВЯТЬ слов-закрытий GitHub вместо шести (вся семья `resolve` пропускалась молча). В
примере осталась старая шестёрка. То есть человек ставил свежий движок, копировал пример
и возвращал себе ровно ту поломку, которую релиз чинил. Поймано ревизией README, а не
тестом — потому что теста не было.

«Синхронить examples с дефолтами» уже значилось в памяти проекта как грабля процесса
выпуска. Грабля, известная человеку, но не проверяемая машиной, — это ненадёжно: она
воспроизводится ровно тогда, когда релиз спешит. Здесь она становится проверяемой.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from claude_memory.config import MemoryConfig, _coerce

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.json"))

# Поля, чьё значение в примере обязано СОВПАДАТЬ с дефолтом библиотеки.
#
# Не все поля примера таковы: пути (`memory_dir`, `project_root`) — плейсхолдеры,
# `topic_order`/`catalog_preamble`/`stopwords` — иллюстрация настройки под проект, и они
# ОБЯЗАНЫ отличаться. Сюда попадает то, у чего нет проектной специфики: нейтральные
# английские regex-дефолты и переключатели. Для них пример = дефолт, и любое расхождение
# означает, что дефолт починили, а пример забыли.
MUST_MIRROR_DEFAULT = (
    "task_close_pattern",
    "session_close_pattern",
    "session_close_case_sensitive",
    "stop_commit_age_limit_seconds",
    "stop_lessons_enabled",
    "task_close_lesson_gate",
    "task_close_command_watch",
    "task_close_marker_ttl_seconds",
    "stale_reconcile_gate",
    "llm_actuality_enabled",
    "retrieve_cache_enabled",
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_is_valid_json(path: Path) -> None:
    assert isinstance(_load(path), dict)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_has_no_unknown_keys(path: Path) -> None:
    """Опечатка в ИМЕНИ ключа примера тиражируется всем, кто его скопировал.

    Движок молча отбрасывает неизвестные ключи (forward-compat), поэтому такая опечатка
    не падает и не жалуется — настройка просто не работает. `self_check.typo_key_issues`
    поймает её у пользователя на старте, но правильнее не отдавать битый пример вовсе.
    Ключи с ведущим `_` — принятая конвенция заметок внутри JSON, их пропускаем."""
    known = set(MemoryConfig.__dataclass_fields__)  # type: ignore[attr-defined]
    unknown = [k for k in _load(path) if not k.startswith("_") and k not in known]
    assert unknown == [], f"{path.name}: ключи, которых нет в MemoryConfig: {unknown}"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_loads_into_config(path: Path) -> None:
    """Пример обязан не просто быть валидным JSON, а собираться в рабочий конфиг."""
    data = _coerce(_load(path))
    data.setdefault("memory_dir", "/tmp/memory")
    data["memory_dir"] = "/tmp/memory"
    data["project_root"] = "/tmp/project"
    known = set(MemoryConfig.__dataclass_fields__)  # type: ignore[attr-defined]
    cfg = MemoryConfig(**{k: v for k, v in data.items() if k in known})
    assert cfg.memory_dir == "/tmp/memory"


@pytest.mark.parametrize("field", MUST_MIRROR_DEFAULT)
def test_neutral_defaults_mirror_the_library(field: str) -> None:
    """Нейтральные дефолты в англоязычном примере == дефолты кода.

    Только для `examples/claude-memory.config.json`: `.ru.json` — локализованный пример,
    он ПО ЗАМЫСЛУ переопределяет шаблоны под русские формулировки (см. урок про стража,
    узнающего событие по одной формулировке).
    """
    data = _load(ROOT / "examples" / "claude-memory.config.json")
    if field not in data:
        pytest.skip(f"{field} не задан в примере — дефолт кода и так в силе")
    default = MemoryConfig.__dataclass_fields__[field].default  # type: ignore[attr-defined]
    assert data[field] == default, (
        f"examples/claude-memory.config.json: `{field}` разошёлся с дефолтом библиотеки.\n"
        f"  пример: {data[field]!r}\n"
        f"  код:    {default!r}\n"
        f"Пример копируют себе пользователи — расхождение молча возвращает им старое поведение."
    )


RU_EXAMPLE = ROOT / "examples" / "claude-memory.config.ru.json"


def test_ru_example_translates_every_message() -> None:
    """Локализованный пример переводит ВСЕ фразы каталога — иначе он врёт названием.

    Отставал на шесть релизов: 76 переводов из 133. Не переведены были целыми блоками
    ровно те, ради которых делались последние выпуски (вся самопроверка, всё про
    устаревание, уборка архива, жалобы «не понял поле»). Ничего не падало: `msg()` штатно
    подставляет английский дефолт (fail-soft), поэтому «русский пример» молча отдавал вывод
    на двух языках вперемешку, и это неотличимо от нормы.

    Почему не поймали раньше: `test_example_has_no_unknown_keys` смотрит только ключи
    ВЕРХНЕГО уровня, внутрь блока `messages` не заглядывал никто.
    """
    from claude_memory.messages import DEFAULT_MESSAGES

    ru = _load(RU_EXAMPLE).get("messages", {})
    missing = sorted(set(DEFAULT_MESSAGES) - set(ru))
    assert missing == [], (
        f"ru-пример не переводит {len(missing)} фраз: {missing[:8]}…\n"
        f"Русскоязычный пользователь получит их по-английски — молча."
    )


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_messages_are_valid_overrides(path: Path) -> None:
    """Переводы в примере: ключи существуют, плейсхолдеры и машинные метки сохранены.

    Три способа сломать перевод молча, и все три уже случались в этом проекте:
      • ключ-сирота (переименовали в коде) → `msg()` его никогда не спросит, перевод мёртв;
      • ЛИШНИЙ плейсхолдер `{x}` → `.format()` уронит подстановку; это ровно контракт
        движка (`self_check.message_placeholder_issues`: плейсхолдеры override ⊆ дефолта).
        Отсутствие плейсхолдера — НЕ ошибка: перевод вправе не показывать значение, если
        так лучше по-русски (напр. `archive.precedent_file_header` пишет «Прецеденты
        сессий» вместо `{keyword}` — множественное число вместо единственного);
      • потерянная метка `[memory]`/`[stop-lessons]` — это машинный ярлык канала, не проза.
        Замер при переводе 0.11.0: метка была потеряна в ВОСЬМИ существующих переводах.

    Проверяем ровно контракт движка, не строже: тест, придирчивее кода, заставляет
    подгонять правильные переводы под чужую придирку.
    """
    from claude_memory.messages import DEFAULT_MESSAGES

    ph = lambda s: set(re.findall(r"\{([^{}]+)\}", s))
    tags = lambda s: set(re.findall(r"\[[a-z0-9 -]+\]", s))

    orphans, bad_ph, bad_tags = [], [], []
    for key, text in (_load(path).get("messages") or {}).items():
        default = DEFAULT_MESSAGES.get(key)
        if default is None:
            orphans.append(key)
            continue
        extra = ph(text) - ph(default)
        if extra:
            bad_ph.append((key, sorted(extra)))
        if tags(default) - tags(text):
            bad_tags.append((key, sorted(tags(default) - tags(text))))

    assert orphans == [], f"{path.name}: ключей нет в каталоге движка: {orphans}"
    assert bad_ph == [], f"{path.name}: плейсхолдеры, которых нет в дефолте: {bad_ph}"
    assert bad_tags == [], f"{path.name}: потеряны машинные метки: {bad_tags}"


def test_example_close_pattern_knows_all_nine_github_keywords() -> None:
    """Поведенческая проверка поверх строкового равенства.

    Равенство строк ловит «дефолт починили, пример забыли». Эта проверка ловит другое:
    пример, который РАЗОШЁЛСЯ осмысленно (кто-то отредактировал), но при этом перестал
    узнавать часть форм закрытия. Именно неполный список форм и был багом: шесть слов из
    девяти, «не узнал» неотличимо от «закрытия не было»."""
    from claude_memory.stop_check import extract_closed_task

    pattern = _load(ROOT / "examples" / "claude-memory.config.json")["task_close_pattern"]
    nine = ("Close", "Closes", "Closed", "Fix", "Fixes", "Fixed",
            "Resolve", "Resolves", "Resolved")
    for word in nine:
        assert extract_closed_task(f"feat: {word} #42", pattern) == "42", (
            f"пример не узнаёт форму закрытия `{word} #42` — страж промолчит на ней молча"
        )
