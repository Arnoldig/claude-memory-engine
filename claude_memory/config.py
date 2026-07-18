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
своим конфигом, включая операторские сообщения (поле messages поверх английских
дефолтов из claude_memory.messages).
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
    # УСТАРЕЛО с 0.10.0 и БОЛЬШЕ НЕ ОТВЕЧАЕТ на вопрос «что такое урок» — на него отвечает
    # `lesson_files.is_lesson_file` (любой *.md в корне, кроме ядра/указателя/приватных), а
    # тип урока берётся из поля `type` frontmatter (`lesson_files.lesson_type`).
    # Почему: файлы уроков пишет НЕ движок, а авто-память Claude Code, и она именует их по
    # своим правилам. Требовать приставку — требовать того, чем не управляешь; страж слеп на
    # всём, что под неё не попало, и требовал записать уже записанное.
    # Поле СОХРАНЕНО (конфиги внешних потребителей не ломаем) в узких ролях: фолбэк для
    # `size_warn_prefixes` и `precedent_files`, ссылки в `precedent_index`.
    # `user` добавлен в дефолт: официальный словарь типов Claude Code — user | feedback |
    # project | reference, и его отсутствие делало урок типа `user` невидимым для стража в
    # ЛЮБОМ проекте на дефолтах (замер: по одному такому уроку в каждом боевом корпусе).
    lesson_prefixes: Tuple[str, ...] = ("feedback", "reference", "project", "user")

    # — таксономия указателя (catalog_generate) —
    topic_order: Tuple[Tuple[str, str], ...] = _DEFAULT_TOPIC_ORDER
    no_topic_title: str = "⚠ No topic (add `topic:` to the frontmatter to file it here)"
    catalog_preamble: str = "# Lessons Catalog (read on demand)"
    desc_max: int = 150                   # обрезка описания в строке указателя
    oversize_bytes: int = 9000            # урок крупнее — кандидат на разбиение (инфо, не ошибка)
    # `description` длиннее — предупреждаем при записи урока (инфо, не ошибка; 0 = выкл).
    # Это КРАТКОЕ содержание: его целиком печатают страж правки и ретривер, поэтому цена
    # раздутого описания — стена текста, которую перестают читать. 500 знаков — ~2.5×
    # медианы боевых корпусов (202 и 173), то есть порог задевает не «подробные» уроки, а
    # те, где тело переехало в описание или склеены две темы (на корпусах: 5 из 427 и
    # 12 из 87). Срабатывает только на ЗАПИСИ урока — существующие не нудят.
    description_warn_chars: int = 500
    # Порог числа уроков для нуджа «проверь дубли» в пульсе здоровья (0 — выкл). Не про
    # «обобщить/слить» (это теряет детали) — только сигнал поискать ТОЧНЫЕ повторы, пока
    # коллекция управляема. Дефолт 500: на больших коллекциях сигналит, малым не мешает.
    lesson_count_warn: int = 500
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
    # Тип суб-агента, который harness подставляет, когда `subagent_type` ОПУЩЕН. И страж,
    # и журнал эффективности резолвят пустой тип в него: опущенный тип ведёт себя так же,
    # как явно указанный default_subagent_type (иначе самый частый «забыл и тип, и model»
    # спавн проскакивал бы мимо стража и считался бы «?»-не-рутиной в журнале). Если он
    # есть в routine_subagent_types — опущенный тип считается рутинным.
    default_subagent_type: str = "general-purpose"
    # Подстрока(и) id «самой сильной» модели. Строка ИЛИ список строк — совпадение по
    # любой (гибко под смену поколений и разное число премиальных моделей). Страж НЕ
    # перечисляет доступные модели (хук этого не умеет) — это настраиваемый ярлык.
    # Дефолт — текущее сильнейшее поколение; проект перекрывает при смене линейки.
    strongest_model_substr: object = "opus"

    # — реестр моделей (model_registry guard, SessionStart): подстраховка от устаревания —
    # Подстроки id ИЗВЕСТНЫХ моделей. Если сессия идёт на модели, чей id не содержит ни
    # одной из них → SessionStart мягко напомнит «новая модель, обнови реестр». Дефолт —
    # текущие семейства Claude (как strongest_model_substr="opus"); самокорректируется:
    # вышла новая → разовый нудж → обнови список. Пусто → проверка ВЫКЛ. Периодическое
    # напоминание сверить линейку — отдельно, через model_registry_verified_on (по умолч. выкл).
    known_model_substrs: Tuple[str, ...] = ("opus", "sonnet", "haiku", "fable")
    # Дата последней ручной сверки линейки моделей (YYYY-MM-DD). None → таймер-напоминание
    # ВЫКЛ. Ловит ДЕАКТИВАЦИЮ модели (её по «текущей модели сессии» не увидеть): если сверка
    # старше model_registry_max_age_days — SessionStart напомнит пересверить линейку.
    model_registry_verified_on: Optional[str] = None
    model_registry_max_age_days: int = 60

    # — страж актуальности LLM (llm_actuality, SessionStart + чек-лист) —
    # Суточная просьба ассистенту сверить линейку моделей (делегировать дешёвой модели +
    # веб-поиск); итог пишется командой llm-verified/llm-changes в _llm_registry_state.json
    # (он же троттлит «раз в сутки» между сессиями и хранит подтверждённый список семейств —
    # сид из known_model_substrs). Реактивная «незнакомая модель» — оттуда же. False → выкл.
    llm_actuality_enabled: bool = True
    llm_actuality_interval_hours: int = 24

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
    # Какие уроки получают размер-warning — СУЖАЮЩАЯ ручка (приставки имён). None → ВСЕ
    # уроки (`lesson_files.is_lesson_file`). До 0.10.0 None значил «все lesson_prefixes»,
    # и урок без приставки рос без единого предупреждения.
    size_warn_prefixes: Optional[Tuple[str, ...]] = None
    size_warn_skip_archive: bool = True   # не предупреждать о размере файлов в archive/
    size_exempt: Tuple[str, ...] = ()     # имена файлов БЕЗ размер-warning (реестры/индексы)
    size_override: dict = field(default_factory=dict)  # имя файла → свой лимит (байты)
    precedent_count_warn: int = 3         # warning при ≥N живых блоках «Прецедент» (0 — выкл)
    # Файлы-накопители прецедентов (приставки имён) — кандидаты на авто-архивацию старых
    # карточек. Это путь ЗАПИСИ: он вырезает карточки из файла и переносит в архив, поэтому
    # определение здесь НАМЕРЕННО узкое (расширение на «любой урок» переселяло бы чужие
    # файлы). None → историческое поведение: первый элемент `lesson_prefixes`.
    # Заведено в 0.10.0: контракт «lesson_prefixes[0] — это файл прецедентов» был
    # позиционным и нигде не описанным, и проект с другим порядком префиксов молча целился
    # не в тот файл. Теперь порядок в `lesson_prefixes` ничего не решает — задавайте явно.
    precedent_files: Optional[Tuple[str, ...]] = None
    precedent_archive_days: int = 30      # прецеденты старше → в архив
    marker_archive_days: int = 7          # session-маркеры старше → в архив
    archive_dir_name: str = "archive"     # подкаталог архива внутри memory_dir
    # Срок хранения архивных уроков (месяцев): урок-файл в archive_dir_name с полем
    # `archived_on: YYYY-MM-DD` старше N месяцев → кандидат на удаление (показывается в
    # _stale_pending на SessionStart; удаление — командой archive_prune с бэкапом). 6 по
    # умолчанию (включаем стражей из коробки): архивные уроки старше полугода «холодные»,
    # страж лишь ПОКАЗЫВАЕТ их на пересмотр. 0 — выкл. Память НЕ самоудаляется — решает человек.
    archive_stale_months: int = 6

    # — авто-архив прецедентов (memory_archive) —
    # Ключевое слово карточки-прецедента и фраза-указатель «перенесён».
    #
    # ЛОМАЮЩАЯ СМЕНА ДЕФОЛТА в 0.11.0: было `"Прецедент"` / `"перенесён в"` (русские, по
    # происхождению проекта). Это был ДЕФОЛТ, КОТОРЫЙ НИКОГДА НЕ РАБОТАЛ у большинства:
    # `memory_archive._precedent_re` строится из `re.escape(precedent_keyword)`, поэтому
    # у любого, кто пишет карточки по-английски и не лез в конфиг, авто-архивация не
    # срабатывала НИ РАЗУ, а `precedent_count_warn` не считал НИЧЕГО — молча, при
    # включённом механизме. Тот же класс, что и `~/.claude/memory` в 0.10.0: папка, в
    # которую не пишет никто.
    #
    # Правило (см. урок про стража, узнающего событие по одной формулировке): generic-дефолт
    # библиотеки языко-НЕЙТРАЛЕН, специфика языка проекта — в проектном конфиге. Русские
    # формы переехали в `examples/claude-memory.config.ru.json`.
    # Кто полагался на прежний дефолт — задаёт явно (две строки в конфиге); это названо
    # ЛОМАЮЩИМ в CHANGELOG обоих языков, а `hooks_cli.ev_bloat_check` предупреждает вслух,
    # если в файле есть карточки с ПРЕЖНИМ русским словом, а дефолт уже английский.
    precedent_keyword: str = "Precedent"
    precedent_pointer: str = "moved to"

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
    # Шаблон коммита-закрытия. По умолчанию — ВСЕ девять официальных слов-закрытий GitHub:
    # close/closes/closed, fix/fixes/fixed, resolve/resolves/resolved (id — число ИЛИ слаг:
    # `#58`, `#memory-lib-cutover`). Группа 1 = номер задачи.
    # Семья `resolve` добавлена в 0.10.0: её не было, то есть треть законных форм закрытия
    # GitHub привратник молча не узнавал. Поймано при переводе трекера на GitHub Issues —
    # и это ровно тот класс дефекта, что уже описан в памяти проекта: страж, узнающий
    # событие по НЕПОЛНОМУ списку форм, недосрабатывает молча, а «не узнал» неотличимо от
    # «события не было». Список форм здесь обязан зеркалить источник (документацию GitHub),
    # а не интуицию автора.
    # Граница слева — негативный lookbehind `(?<![\w-])`, а НЕ `\b`: `\b` срабатывает и
    # после дефиса, из-за чего `prefixed-closes #10` / `auto-closes #10` ложно читались бы
    # как закрытие #10. Lookbehind пропускает `Closes #id` в начале строки, после пробела
    # или двоеточия, но не `<слово>-closes #id`. Найдено red-team-проверкой.
    task_close_pattern: str = (
        r"(?i)(?<![\w-])(?:clos(?:e|es|ed)|fix(?:es|ed)?|resolv(?:e|es|ed))\s+#([\w-]+)"
    )
    # ВТОРОЙ ИСТОЧНИК сигнала о закрытии — сама команда `gh issue close` (PostToolUse на
    # Bash), а не только текст коммита. Заведён в 0.13.0, см. `issue_close_watch`.
    # Почему это отдельная ручка, а не часть task_close_lesson_gate: гейт остаётся
    # мастер-выключателем (выкл → не работает ничто), а эта ручка глушит ТОЛЬКО новый
    # источник — например там, где закрытие задачи и запись урока разнесены по разным
    # сессиям намеренно. Дефолт ВКЛючён, и это осознанно: у движка уже была история
    # «дефолт, который никогда не работал» (русское `Прецедент` в англоязычных проектах
    # до 0.11.0), а страж, выключенный по умолчанию, не закрывает дыру, ради которой
    # заведён, — он лишь позволяет сказать, что она закрыта.
    task_close_command_watch: bool = True
    # Срок годности метки о закрытии (секунды). Метка старше — подметается молча, даже
    # если урок так и не записан. Страж, способный запереть сессию насмерть, хуже
    # отсутствующего: его выключают целиком вместе с пользой. 4 часа = то же окно, что у
    # `stop_commit_age_limit_seconds`, чтобы «свежее закрытие» и «свежий коммит» значили
    # одно и то же. 0 — срок годности выключен (метка живёт до записи урока).
    task_close_marker_ttl_seconds: int = 14400

    # — страж устаревших уроков (stale_reconcile): чек-лист памяти на фразу закрытия —
    # Когда сообщение пользователя совпадает с session_close_pattern, на UserPromptSubmit
    # выводится чек-лист итогов сессии: уроки, привязанные к правленым файлам и НЕ
    # актуализированные («не устарели ли?»), смысловой список связанных, статус сроков/архива
    # и список включённых/выключенных стражей. Плюс тихий бэкстоп: те же кандидаты в
    # _stale_pending на SessionEnd (покажет следующий SessionStart — если фразу не написали).
    # True по умолчанию (включаем стражей из коробки); чек-лист всегда честно показывает,
    # что включено и что найдено. `/compact` id сессии НЕ меняет (метки видны), `/clear`
    # страхуется SessionEnd-бэкстопом.
    stale_reconcile_gate: bool = True
    # Шаблон фразы закрытия сессии (regex). При совпадении с сообщением пользователя
    # выводится чек-лист stale_reconcile. Дефолт — нейтральная англ. фраза; проект задаёт
    # свои формы. Пусто → чек-лист по фразе не выводится (бэкстоп SessionEnd остаётся).
    session_close_pattern: str = r"\bclose session\b"
    # Учитывать ли регистр при сверке session_close_pattern. True → точное совпадение
    # регистра («Done» ≠ «done»), это убирает ложные срабатывания на частых словах.
    # False → регистр игнорируется. Привязано только к фразе закрытия (не к task_close_pattern).
    session_close_case_sensitive: bool = False

    # — проектные строки-напоминания на SessionStart (печатаются в контекст как есть) —
    # Дефолт пуст; проект задаёт операционные ноты (напр. как логиниться в dev).
    session_start_notes: Tuple[str, ...] = ()

    # — i18n: переопределения операторских сообщений (ключ → шаблон), поверх англ.
    # дефолтов в claude_memory.messages.DEFAULT_MESSAGES. См. messages.msg().
    messages: dict = field(default_factory=dict)

    # — служебное, НЕ задаётся человеком: ключи JSON-конфига, которых движок не знает и
    # потому выбросил (`_coerce`). Их надо ПОМНИТЬ, иначе self_check физически не сможет
    # сказать про опечатку в имени ключа: к нему конфиг приходит уже очищенным, и
    # `session_close_patterns` (лишняя s) неотличим от «поле не задавали».
    # Заполняется только `load()`; значение из JSON игнорируется (см. `_coerce`).
    unknown_config_keys: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Приводит к каноническому виду поля, где описка ОДНОЗНАЧНА по намерению.

        Здесь движок НЕ жалуется, а молча делает то, что человек имел в виду — и это не
        противоречит правилу «не отменять молча явно заданное», а следует ему: настройка
        начинает РАБОТАТЬ, а не тихо выпадает. Жалоба уместна там, где намерение неясно
        (опечатка в ИМЕНИ ключа → `self_check.typo_key_issues` спрашивает). А `.py` не
        может означать ничего, кроме `py`, — спрашивать не о чем, надо понять.

        Что чинилось молча до этого (каждое воспроизведено):
          • `retrieve_extensions: [".py"]` → `_path_re` собирал `\\.(?:\\.py)`, то есть
            требовал `..py` → канал «уроки по пути из запроса» не находил НИЧЕГО;
          • `watched_dirs: ["app/"]` → там же требовалось `app//` → то же самое;
          • `lesson_prefixes: ["feedback_"]` → `catalog_generate` клеил `feedback__`, а
            `stop_check` искал `feedback__*.md` → движок считал, что уроков нет вообще,
            и Stop-страж переставал видеть записанное (при этом `startswith` в другом
            месте работал — то есть поведение ещё и расходилось между частями движка);
          • `staleness_skip_dirs: [".git/"]` → сверка идёт с ГОЛЫМ именем каталога от
            `os.walk` (`.git`) → пропуск не срабатывал, обход лез в тяжёлые каталоги.
        Нормализация идёт в `__post_init__`, а не в `_coerce`: инвариант обязан держаться
        при ЛЮБОМ способе создания конфига (из JSON, из дефолтов, через `replace`), иначе
        разойдутся боевой путь и тесты. frozen → присваиваем через object.__setattr__.
        """
        norm = {
            # ведущая точка — не часть имени расширения: ".py" → "py"
            "retrieve_extensions": lambda v: v.strip().lstrip("."),
            # каталог: "./app/" → "app" (сравнение идёт с путём без хвостового слэша)
            "watched_dirs": lambda v: v.strip().removeprefix("./").rstrip("/"),
            # сверка с голым именем каталога от os.walk: ".git/" → ".git"
            "staleness_skip_dirs": lambda v: v.strip().rstrip("/"),
            # разделитель движок добавляет сам: "feedback_" → "feedback"
            "lesson_prefixes": lambda v: v.strip().rstrip("_"),
        }
        for field_name, fix in norm.items():
            raw = getattr(self, field_name, None)
            if not raw:
                continue
            cleaned = tuple(x for x in (fix(str(v)) for v in raw) if x)
            if cleaned != tuple(raw):
                object.__setattr__(self, field_name, cleaned)

    def topic_titles(self) -> dict:
        return dict(self.topic_order)


# ── Загрузка ────────────────────────────────────────────────────────────────

# Поля, чьи списки-кортежи приходят из JSON как list → нормализуем в tuple.
_TUPLE_FIELDS = {
    "lesson_prefixes", "watched_dirs", "stopwords", "routine_subagent_types",
    "staleness_skip_dirs", "retrieve_extensions", "size_warn_prefixes",
    "size_exempt", "session_start_notes", "known_model_substrs", "precedent_files",
}


def _coerce(data: dict) -> dict:
    """Готовит dict из JSON к передаче в MemoryConfig: list→tuple, topic_order→tuple пар."""
    out = dict(data)
    if "topic_order" in out and out["topic_order"] is not None:
        out["topic_order"] = tuple(tuple(pair) for pair in out["topic_order"])
    for k in _TUPLE_FIELDS:
        if k in out and out[k] is not None:
            out[k] = tuple(out[k])
    # Выкидываем неизвестные ключи, чтобы чужой конфиг не падал на новых/чужих полях
    # (forward-compat: старый движок × конфиг новой версии). Но ПОМНИМ выброшенное:
    # молчаливое отбрасывание не отличает «ключ из будущей версии» от опечатки, а
    # опечатка тихо оставляет английский дефолт (так уже было со стражем закрытия).
    # Решает не тут, а self_check: он сузит до похожих на известные (difflib).
    # `unknown_config_keys` — служебное поле, из JSON его не принимаем: иначе конфиг
    # смог бы подделать собственный отчёт о своих же опечатках.
    known = {f for f in MemoryConfig.__dataclass_fields__}  # type: ignore[attr-defined]
    out.pop("unknown_config_keys", None)
    dropped = tuple(sorted(k for k in out if k not in known))
    coerced = {k: v for k, v in out.items() if k in known}
    coerced["unknown_config_keys"] = dropped
    return coerced


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


def _fallback_memory_dir(project_root: str) -> str:
    """Каталог авто-памяти Claude Code для проекта — запасное значение memory_dir.

    Импорт локальный — он экономит ИМПОРТ модуля на общем пути загрузки конфига, но НЕ
    subprocess: у кого memory_dir не задан ни в конфиге, ни в env, git-вызов (~14 мс)
    платится в каждом хуке. Популяция узкая (установщик путь всегда пишет), и размен
    честный: было «мгновенно и мимо цели», стало «14 мс и в цель».

    `except Exception` намеренно широкий: `load()` обязан не падать НИКОГДА, иначе умрут
    все хуки разом. `KeyboardInterrupt`/`SystemExit` он не ловит (они не от Exception), так
    что Ctrl-C не проглатывается. Маскировки нет: `resolve_auto_memory_dir` глотает свои
    ошибки сам и всегда возвращает путь — эта ветка практически недостижима, она ремень
    поверх подтяжек."""
    try:
        from .claude_code_env import resolve_auto_memory_dir

        resolved, _trusted = resolve_auto_memory_dir(project_root)
        if resolved:
            return resolved
    except Exception:
        pass
    return str(Path.home() / ".claude" / "memory")


def load(path: Optional[str] = None) -> MemoryConfig:
    """Загружает конфиг из JSON (или дефолты). Пути memory_dir/project_root, если не заданы
    ни в файле, ни в env (CLAUDE_MEMORY_DIR / CLAUDE_PROJECT_ROOT), выводятся: project_root
    — текущий каталог, memory_dir — каталог авто-памяти Claude Code для этого проекта.

    Про memory_dir. До 0.10.0 запасным значением было `~/.claude/memory` — папка, в которую
    НЕ ПИШЕТ НИКТО: уроки создаёт авто-память Claude Code, а держит она их в
    `~/.claude/projects/<slug>/memory`. Молчаливый откат на заведомо пустой каталог — та же
    болезнь, что и у стража: движок делал вид, что настроен, и честно не находил ничего.
    Путь здесь ВЫВОДИТСЯ, а не подтверждается (подтверждение и жалобы — дело `self_check`),
    но выводится к той папке, где уроки действительно есть.
    """
    cfg_file = _find_config_file(path)
    data: dict = {}
    if cfg_file is not None:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    data = _coerce(data)

    # пути — отдельной логикой (env как запасной источник). project_root ПЕРВЫМ: от него
    # выводится memory_dir.
    if "project_root" not in data:
        data["project_root"] = os.environ.get("CLAUDE_PROJECT_ROOT") or str(Path.cwd())
    if "memory_dir" not in data:
        data["memory_dir"] = os.environ.get("CLAUDE_MEMORY_DIR") or _fallback_memory_dir(
            data["project_root"]
        )
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


def render_cli(args) -> str:
    """Текст для CLI печати конфига: `get <field>` → значение поля, иначе весь конфиг JSON.

    Чистая (возвращает строку, не печатает) — переиспользуется и модульным CLI
    (`python3 -m claude_memory.config`), и пакетным (`claude-memory config`)."""
    cfg = get_config()
    if len(args) >= 2 and args[0] == "get":
        val = getattr(cfg, args[1], "")
        if isinstance(val, (tuple, list)):
            return "\n".join(str(x) for x in val)
        return str(val)
    from dataclasses import asdict

    return json.dumps(asdict(cfg), ensure_ascii=False, indent=2)


def main() -> None:
    """CLI: `python3 -m claude_memory.config [get <field>]` — печать конфига/поля.

    Нужно обёрткам-хукам на bash, чтобы прочитать одно значение конфига одной строкой
    (напр. `MEM=$(python3 -m claude_memory.config get memory_dir)`)."""
    import sys

    print(render_cli(sys.argv[1:]))


if __name__ == "__main__":
    main()
