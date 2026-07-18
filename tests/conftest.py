"""Общие фикстуры тестов движка памяти.

Все тесты — чистый stdlib, без сети/БД. Пакет `claude_memory` импортируется из корня
репозитория (pytest добавляет rootdir в sys.path). Конфиг в тестах строится явно и
указывает на временный каталог памяти — глобальный singleton конфига не используется.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Корень репозитория (где лежит пакет claude_memory) — в sys.path для импорта.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_memory.config import MemoryConfig  # noqa: E402

# Реалистичный ПРОЕКТНЫЙ шаблон закрытия задачи: англ. «Closes #id» (id ПОСЛЕ слова)
# + локализованная рус. «#id закрыт[а|о|ы]» (id ДО слова). Две capture-группы по веткам —
# покрытие для тестов «extract_closed_task берёт первую непустую группу, а не группу 1».
# Рус. ветка узкая (`закрыт[аоы]?\b`, а не `закры\w*`): ловит ровно закрыт/закрыта/закрыто/
# закрыты, но не отглагольные закрытие/закрытость/закрытый/закрывать (red-team-уточнение).
# Граница слева у англ. ветки — `(?<![\w-])`, а НЕ `\b` (зеркалит фикс библиотечного
# дефолта): `\b` ловил бы `<слово>-closes #id` как закрытие. В библиотечный дефолт паттерн
# целиком НЕ кладётся (он generic/английский) — это пример проекта.
# Английская ветка ОБЯЗАНА покрывать все девять слов-закрытий GitHub, как и дефолт
# библиотеки: этот шаблон — «реалистичный проектный», и если он отстаёт от дефолта, тесты
# гоняются на правиле, которого в проде нет. Так и случилось: дефолт в 0.10.0 научили семье
# `resolve`, а копия здесь осталась с шестью словами — рассинхрон прожил до 0.11.0 и
# закрыт `test_conftest_pattern_covers_all_github_keywords` ниже по файлу.
# С 0.15.0 английская ветка несёт ОБА документированных написания (`Closes #id` и
# `Closes: #id`), как и дефолт: копия, отставшая по написанию, — тот же класс дефекта,
# что копия, отставшая по слову, и сюита не должна гоняться на правиле, которого в проде
# нет. Заодно эта строка — репетиция миграции проектных конфигов: ровно так их шаблоны
# пересобираются поверх нового дефолта.
RU_EN_CLOSE_PATTERN = (
    r"(?i)(?<![\w-])(?:clos(?:e|es|ed)|fix(?:es|ed)?|resolv(?:e|es|ed))"
    r"(?::\s*#([0-9]+)|\s+#([\w-]+))"
    r"|#([\w-]+)\s+закрыт[аоы]?\b"
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch) -> Path:
    """Пустой временный домашний каталог для КАЖДОГО теста сюиты.

    Зачем глобально. С 0.10.0 движок читает настройки ХОЗЯИНА — Claude Code, — и слабейшая
    их область `~/.claude/settings.json` (`claude_code_env._read_settings`). Значит любой
    тест, дёргающий `self_check.warnings()`/`settings_issues()`, начинает зависеть от
    домашней папки того, кто запускает сюиту: у автора там нет `autoMemoryDirectory` —
    зелено; у человека, который его задал (законная настройка!), — красные тесты и никакого
    объяснения. Проверено: с HOME, где лежит `{"autoMemoryDirectory": "/somewhere/else"}`,
    падало пять тестов — включая ТРИ старых, которых правка не касалась вовсе.

    Тест, зависящий от чужого ноутбука, закрепляет ноутбук, а не поведение. Поэтому изоляция
    тут не гигиена, а условие осмысленности всей сюиты — и место ей в conftest, а не в
    отдельных модулях: подмешивать её обязаны ВСЕ тесты, включая завтрашние.

    Патчим ОБА канала: `Path.home` (его зовёт `default_auto_memory_dir`) и env `HOME`
    (на него смотрит `Path.expanduser`, а `Path.home` его не покрывает). Тесту, которому
    нужен свой home, достаточно поставить собственный monkeypatch — он применится позже.
    """
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def cfg(tmp_path: Path) -> MemoryConfig:
    """MemoryConfig с дефолтами, memory_dir/project_root → во временный каталог."""
    mem = tmp_path / "memory"
    mem.mkdir()
    return MemoryConfig(memory_dir=str(mem), project_root=str(tmp_path))


def write_lesson(memory_dir: str, base: str, **fm) -> Path:
    """Создаёт файл-урок с frontmatter из kwargs (значения — как есть) + опц. body=...."""
    body = fm.pop("body", "")
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append(body)
    p = Path(memory_dir) / base
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
