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
