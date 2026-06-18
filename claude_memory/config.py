"""Конфигурация движка памяти (claude-memory-engine).

Здесь собраны ВСЕ значения, которые раньше были захардкожены в отдельных модулях
(пути конкретной машины, таксономия тем, имя самой сильной модели, пороги). Любой
проект задаёт их в JSON-файле конфига и переключает поведение движка без правки кода.

Где берётся конфиг (первый сработавший источник):
  1. путь, переданный в load(path) явно (тесты, CLI);
  2. переменная окружения CLAUDE_MEMORY_CONFIG (её ставит install.sh в каждой обёртке);
  3. файл claude-memory.config.json в текущем каталоге.
Если ничего нет — берутся дефолты ниже (движок работает «из коробки» на нейтральных
значениях; пути памяти при этом всё равно нужно задать через env/конфиг).

Дефолты намеренно НЕЙТРАЛЬНЫ (без привязки к какому-либо проекту): таксономия тем —
общая для разработки, тексты-указатели — английские. Реальный проект перекрывает их
своим конфигом. Перевод операторских сообщений модулей на другие языки — отдельная
задача (см. README → Roadmap).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import List, Optional, Tuple

# Нейтральная таксономия тем по умолчанию: (слаг topic: , человеко-читаемый заголовок).
# Порядок здесь = порядок разделов в указателе CATALOG.
_DEFAULT_TOPIC_ORDER: Tuple[Tuple[str, str], ...] = (
    ("workflow", "Workflow & methodology"),
    ("testing", "Testing"),
    ("infra", "Infrastructure & CI"),
    ("security", "Security"),
    ("docs", "Documentation"),
    ("core", "Hot core (mirrored in the core file)"),
)

# Стоп-слова ретривера по умолчанию (английские служебные + слишком общие). Русский
# проект добавляет свои в конфиге (ключ "stopwords" ПОЛНОСТЬЮ заменяет этот набор).
_DEFAULT_STOPWORDS: Tuple[str, ...] = (
    "the", "and", "for", "with", "this", "that", "are", "was", "were", "but",
    "not", "you", "your", "from", "have", "has", "can", "will", "any", "all",
    "new", "add", "make", "use", "set", "get", "via", "per", "into", "onto",
    "file", "files", "step", "part", "after", "before",
)


@dataclass(frozen=True)
class MemoryConfig:
    """Все параметры движка. frozen — конфиг неизменяем после загрузки (один источник истины)."""

    # — пути —
    memory_dir: str                       # каталог уроков (markdown-файлы памяти)
    project_root: str                     # корень репозитория проекта (для applies_to/staleness)

    # — особые файлы памяти —
    core_file: str = "MEMORY.md"          # горячее ядро (грузится всегда, есть бюджет)
    catalog_file: str = "CATALOG.md"      # авто-указатель (собирается из frontmatter)
    session_lessons_file: str = "feedback_session_end_lessons.md"  # транзитный файл маркеров
    lesson_prefixes: Tuple[str, ...] = ("feedback", "reference", "project")  # префиксы файлов-уроков

    # — таксономия указателя (catalog_generate) —
    topic_order: Tuple[Tuple[str, str], ...] = _DEFAULT_TOPIC_ORDER
    no_topic_title: str = "⚠ No topic (add `topic:` to the frontmatter to file it here)"
    catalog_preamble: str = "# Lessons Catalog (read on demand)"
    desc_max: int = 150                   # обрезка описания в строке указателя
    oversize_bytes: int = 9000            # урок крупнее — кандидат на разбиение (инфо, не ошибка)
    # Порог числа уроков для нуджа «проверь дубли» в пульсе здоровья (0 — выкл). Не про
    # «обобщить/слить» (это теряет детали) — только сигнал поискать ТОЧНЫЕ повторы, пока
    # коллекция управляема. Ловит рост per-prompt стоимости ретривера на длинном горизонте.
    lesson_count_warn: int = 0
    # Маркеры авто-блока CATALOG. Конфигурируемы: проект со своими маркерами (или с
    # другим языком в шапке) задаёт их тут — первая пересборка узнаёт существующий файл
    # (иначе шапка не распознается и осиротеет). Дефолт — нейтральный английский.
    catalog_auto_start: str = (
        "<!-- AUTO-INDEX:START — managed by catalog_generate; edits between markers are overwritten -->"
    )
    catalog_auto_end: str = "<!-- AUTO-INDEX:END -->"
    # Префикс «приватных» файлов (исключаются из указателя/ретривера). Дефолт «_».
    private_file_prefix: str = "_"
    # Пульс здоровья на SessionStart: троттлинг (раз/день + при смене долга). False —
    # показывать каждый старт.
    health_pulse_throttle: bool = True

    # — ретривер (memory_retrieve) —
    watched_dirs: Tuple[str, ...] = ("app", "tests", "src", "lib", "scripts", "docs")
    # Расширения файлов, распознаваемые как «путь» в тексте запроса (для applies_to).
    retrieve_extensions: Tuple[str, ...] = (
        "py", "html", "js", "ts", "css", "sh", "yaml", "yml", "json", "md", "rs", "go", "java", "rb",
    )
    retrieve_top_n: int = 6
    retrieve_threshold: float = 6.0       # порог тишины в режиме хука
    retrieve_stem: int = 5                # длина префикса-стема
    retrieve_min_token: int = 3           # игнорировать токены короче
    retrieve_body_chars: int = 1500       # сколько символов тела урока индексировать
    stopwords: Tuple[str, ...] = _DEFAULT_STOPWORDS
    # — SQLite-кэш ретривера (sqlite_index): ускоряет score_files, не меняя ранжирование —
    # Кэш разобранных токенов уроков поверх markdown (файлы — источник истины). Свежесть
    # сверяется по mtime+size ПРИ ЧТЕНИИ; формула весов не меняется. Любая ошибка кэша →
    # тихий откат на полный file-scan. False — киллсвитч (всегда file-scan, ноль изменений).
    retrieve_cache_enabled: bool = True
    # Имя файла-БД в memory_dir. Держите приватный префикс (private_file_prefix) — тогда
    # она и WAL-спутники вне глобов движка. Не .md → ретривером всё равно не выдаётся.
    retrieve_cache_file: str = "_retrieve_cache.sqlite3"
    # Сколько мс ждать освобождения блокировки записи (параллельные сессии), а не падать.
    retrieve_cache_busy_timeout_ms: int = 4000

    # — страж модели суб-агентов (subagent_model_guard) —
    routine_subagent_types: Tuple[str, ...] = ("Explore", "general-purpose", "claude-code-guide")
    # Подстрока(и) id «самой сильной» модели. Строка ИЛИ список строк — совпадение по
    # любой (гибко под смену поколений и разное число премиальных моделей). Страж НЕ
    # перечисляет доступные модели (хук этого не умеет) — это настраиваемый ярлык.
    # Дефолт — текущее сильнейшее поколение; проект перекрывает при смене линейки.
    strongest_model_substr: object = "opus"

    # — реестр моделей (model_registry guard, SessionStart): подстраховка от устаревания —
    # Подстроки id ИЗВЕСТНЫХ проекту моделей. Если сессия идёт на модели, чей id не
    # содержит ни одной из них → SessionStart мягко напомнит «новая модель, обнови реестр».
    # Пусто → проверка «неизвестной модели» ВЫКЛ (opt-in).
    known_model_substrs: Tuple[str, ...] = ()
    # Дата последней ручной сверки линейки моделей (YYYY-MM-DD). None → таймер-напоминание
    # ВЫКЛ. Ловит ДЕАКТИВАЦИЮ модели (её по «текущей модели сессии» не увидеть): если сверка
    # старше model_registry_max_age_days — SessionStart напомнит пересверить линейку.
    model_registry_verified_on: Optional[str] = None
    model_registry_max_age_days: int = 60

    # — страж формата маркеров (session_marker_guard) —
    marker_limit: int = 200               # макс. длина однострочного session-маркера

    # — пороги обслуживания (предупреждения о размере файлов памяти) —
    core_budget_bytes: int = 15000        # бюджет горячего ядра (единица — core_size_unit)
    # Единица измерения ГОРЯЧЕГО ЯДРА: "chars" (честно для не-латиницы — ядро всегда в
    # контексте, важна длина контента, не байты на диске) или "bytes". Уроки/oversize
    # меряются в байтах всегда. Дефолт — chars.
    core_size_unit: str = "chars"
    # Ранний нудж ядра: предупредить уже при core_warn_ratio·бюджета (а не только сверх
    # 100%). None — выключить ранний нудж (только превышение бюджета).
    core_warn_ratio: Optional[float] = 0.8
    feedback_warn_bytes: int = 4000       # предупреждение о крупном уроке (байты)
    # Какие префиксы уроков получают размер-warning. None → все lesson_prefixes. Проект
    # может сузить (напр. только "feedback").
    size_warn_prefixes: Optional[Tuple[str, ...]] = None
    size_warn_skip_archive: bool = True   # не предупреждать о размере файлов в archive/
    size_exempt: Tuple[str, ...] = ()     # имена файлов БЕЗ размер-warning (реестры/индексы)
    size_override: dict = field(default_factory=dict)  # имя файла → свой лимит (байты)
    precedent_count_warn: int = 3         # warning при ≥N живых блоках «Прецедент» (0 — выкл)
    precedent_archive_days: int = 30      # прецеденты старше → в архив
    marker_archive_days: int = 7          # session-маркеры старше → в архив
    archive_dir_name: str = "archive"     # подкаталог архива внутри memory_dir

    # — авто-архив прецедентов (memory_archive) —
    # Ключевое слово карточки-прецедента и фраза-указатель «перенесён». Дефолты
    # русские (происхождение проекта); англоязычный проект задаёт свои в конфиге.
    precedent_keyword: str = "Прецедент"
    precedent_pointer: str = "перенесён в"

    # — скан устаревания (staleness, SessionEnd) —
    # Каталоги, которые НЕ обходим при проверке «протухших» applies_to-привязок.
    staleness_skip_dirs: Tuple[str, ...] = (
        ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
        ".mypy_cache", "dist", "build", ".ruff_cache", ".claude",
    )

    # — напоминание про уроки при завершении (Stop) —
    stop_lessons_enabled: bool = True            # блокировать Stop, если есть свежий коммит без урока
    stop_commit_age_limit_seconds: int = 14400   # «свежий» коммит — моложе этого (4 часа)

    # — привратник закрытия задачи (Stop): коммит-закрытие без записанного про задачу урока —
    task_close_lesson_gate: bool = True
    # Шаблон коммита-закрытия. По умолчанию — стандарт GitHub `Closes/Fixes #<id>`
    # (id — число ИЛИ слаг: `#58`, `#memory-lib-cutover`). Группа 1 = номер задачи.
    task_close_pattern: str = r"(?i)\b(?:clos(?:e|es|ed)|fix(?:es|ed)?)\s+#([\w-]+)"

    # — проектные строки-напоминания на SessionStart (печатаются в контекст как есть) —
    # Дефолт пуст; проект задаёт операционные ноты (напр. как логиниться в dev).
    session_start_notes: Tuple[str, ...] = ()

    # — i18n: переопределения операторских сообщений (ключ → шаблон), поверх англ.
    # дефолтов в claude_memory.messages.DEFAULT_MESSAGES. См. messages.msg().
    messages: dict = field(default_factory=dict)

    def topic_titles(self) -> dict:
        return dict(self.topic_order)


# ── Загрузка ────────────────────────────────────────────────────────────────

# Поля, чьи списки-кортежи приходят из JSON как list → нормализуем в tuple.
_TUPLE_FIELDS = {
    "lesson_prefixes", "watched_dirs", "stopwords", "routine_subagent_types",
    "staleness_skip_dirs", "retrieve_extensions", "size_warn_prefixes",
    "size_exempt", "session_start_notes", "known_model_substrs",
}


def _coerce(data: dict) -> dict:
    """Готовит dict из JSON к передаче в MemoryConfig: list→tuple, topic_order→tuple пар."""
    out = dict(data)
    if "topic_order" in out and out["topic_order"] is not None:
        out["topic_order"] = tuple(tuple(pair) for pair in out["topic_order"])
    for k in _TUPLE_FIELDS:
        if k in out and out[k] is not None:
            out[k] = tuple(out[k])
    # выкидываем неизвестные ключи, чтобы чужой конфиг не падал на новых/чужих полях
    known = {f for f in MemoryConfig.__dataclass_fields__}  # type: ignore[attr-defined]
    return {k: v for k, v in out.items() if k in known}


def _find_config_file(explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    env = os.environ.get("CLAUDE_MEMORY_CONFIG")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
    cwd_cfg = Path.cwd() / "claude-memory.config.json"
    if cwd_cfg.is_file():
        return cwd_cfg
    return None


def load(path: Optional[str] = None) -> MemoryConfig:
    """Загружает конфиг из JSON (или дефолты). Пути memory_dir/project_root, если не
    заданы ни в файле, ни в env, берутся из CLAUDE_MEMORY_DIR / CLAUDE_PROJECT_ROOT,
    иначе — нейтральные дефолты (~/.claude/memory и текущий каталог)."""
    cfg_file = _find_config_file(path)
    data: dict = {}
    if cfg_file is not None:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    data = _coerce(data)

    # пути — отдельной логикой (env как запасной источник)
    if "memory_dir" not in data:
        data["memory_dir"] = os.environ.get("CLAUDE_MEMORY_DIR") or str(
            Path.home() / ".claude" / "memory"
        )
    if "project_root" not in data:
        data["project_root"] = os.environ.get("CLAUDE_PROJECT_ROOT") or str(Path.cwd())
    data["memory_dir"] = str(Path(data["memory_dir"]).expanduser())
    data["project_root"] = str(Path(data["project_root"]).expanduser())
    return MemoryConfig(**data)


_CACHED: Optional[MemoryConfig] = None


def get_config() -> MemoryConfig:
    """Singleton-конфиг для обёрток-хуков (грузится один раз за процесс). Тесты и
    переиспользуемые функции принимают cfg явно и этот кэш не трогают."""
    global _CACHED
    if _CACHED is None:
        _CACHED = load()
    return _CACHED


def reset_cache() -> None:
    """Сброс singleton (для тестов, меняющих env/конфиг между кейсами)."""
    global _CACHED
    _CACHED = None


def main() -> None:
    """CLI: `python3 -m claude_memory.config [get <field>]` — печать конфига/поля.

    Нужно обёрткам-хукам на bash, чтобы прочитать одно значение конфига одной строкой
    (напр. `MEM=$(python3 -m claude_memory.config get memory_dir)`)."""
    import sys

    args = sys.argv[1:]
    cfg = get_config()
    if len(args) >= 2 and args[0] == "get":
        val = getattr(cfg, args[1], "")
        if isinstance(val, (tuple, list)):
            print("\n".join(str(x) for x in val))
        else:
            print(val)
        return
    # без аргументов — весь конфиг как JSON (диагностика)
    from dataclasses import asdict

    print(json.dumps(asdict(cfg), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
