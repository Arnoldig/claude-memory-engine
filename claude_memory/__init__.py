"""claude-memory-engine — переиспользуемый движок «памяти уроков» для Claude Code.

Универсальная (не привязанная к конкретному проекту) часть системы памяти: файловое
хранилище уроков с frontmatter, авто-указатель CATALOG, офлайн-ретривер, всплытие
уроков по пути файла, авто-обслуживание, страж параллельных правок и страж выбора
модели суб-агентов. Все проектные значения — в claude_memory.config.MemoryConfig.

Происхождение: извлечено из инструментария проекта ЧеКи (#memory-tooling-library).
"""
from __future__ import annotations

__version__ = "0.5.1"

from .config import MemoryConfig, get_config, load

__all__ = ["MemoryConfig", "get_config", "load", "__version__"]
