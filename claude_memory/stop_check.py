"""Напоминание про уроки при завершении (Stop): если есть свежий коммит, после которого
урок в память НЕ записан — блокируем завершение turn'а с просьбой зафиксировать вывод.

Текстовое напоминание «записывай уроки» легко игнорируется; блокирующий страж в точке
завершения — нет. Срабатывает, только если последний коммит свежее самого свежего
файла-урока И не старше окна (по умолчанию 4 часа). Fail-open на любой ошибке.

Это ОБЩИЙ kernel. Проектные расширения (напр. требование записи в архив прецедентов при
коммите-закрытии задачи) сюда НЕ входят — их держат отдельным проектным хуком.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from .config import MemoryConfig, get_config
from .lesson_files import lesson_paths
from .messages import msg

# Девять официальных слов-закрытий GitHub. ЕДИНСТВЕННЫЙ ИСТОЧНИК ИСТИНЫ: перечень
# зеркалит документацию GitHub, а не интуицию автора, и он ЗАКРЫТ — платформа закроет
# задачу на любом из этих слов независимо от того, знает ли о нём чей-то шаблон.
#
# Почему константа появилась только в 0.14.0, хотя перечень старше. Он жил тремя
# несвязанными копиями: кортеж в `tests/test_examples_sync.py`, кортеж в
# `tests/test_repo_invariants.py` и проза в комментарии `config.py`. Ровно тот класс,
# который чинит заявка #8: копия эталона замерзает в день копирования и расходится
# молча. Добавить четвёртую копию ради проверки на отставание значило бы воспроизвести
# дефект внутри его же починки, поэтому перечень переехал сюда, а копии стали импортом.
#
# Живёт в `stop_check`, потому что здесь же лежит `extract_closed_task` — поведение,
# которое этот перечень потребляет. Импортировать отсюда безопасно: модуль тянет только
# `config`/`lesson_files`/`messages`, цикла с `self_check` нет.
GITHUB_CLOSE_KEYWORDS: tuple = (
    "Close", "Closes", "Closed",
    "Fix", "Fixes", "Fixed",
    "Resolve", "Resolves", "Resolved",
)

# Вторая координата эталона: НАПИСАНИЕ формы. У закрытия их две, и обе документированы —
# «KEYWORD #N» и «KEYWORD: #N» (дословно: «The keywords can be followed by colons or in
# uppercase. For example: `Closes: #10`, `CLOSES #10`, or `CLOSES: #10`»).
# Пара — (суффикс метки, шаблон зонда). Суффикс попадает в текст жалобы, поэтому
# `Close:` читается как готовый образец и `example` не вырождается в «Close: #42 #42».
#
# ЗДЕСЬ ТОЛЬКО ДОКУМЕНТИРОВАННОЕ. Слитной формы `Closes:#42` в эталоне НЕТ, хотя дефолт
# её принимает: эталон — это то, чего вправе требовать библиотека от чужого шаблона, и
# требовать сверх документации она не может. Дефолт при этом СОЗНАТЕЛЬНО шире эталона —
# см. обоснование в `config.py`. Разводить их в разные стороны нельзя: эталон уже, дефолт
# шире, и это направление проверяется тестом.
#
# Число зондов = len(GITHUB_CLOSE_KEYWORDS) × len(GITHUB_CLOSE_SYNTAXES). Считать его
# ИМЕННО так, а не константой: у `self_check.close_pattern_lag_issues` есть выход «узнано
# ноль форм = законная полная замена», и он сверяется с ОБЩИМ числом зондов. Замороженное
# число (или множитель «×2») при добавлении третьего написания даст ту же инверсию, что
# уже воспроизведена прогоном: отставший шаблон промолчит, а законная замена пожалуется.
GITHUB_CLOSE_SYNTAXES: tuple = (
    ("", "{word} #42"),
    (":", "{word}: #42"),
)


def decide(commit_ts: int, feedback_ts: float, now_ts: float, age_limit: int) -> bool:
    """Чистая логика: блокировать ли. True, если коммит свежий (моложе age_limit) И новее урока."""
    return commit_ts > 0 and (now_ts - commit_ts) < age_limit and commit_ts > feedback_ts


def newest_lesson_mtime(cfg: MemoryConfig) -> float:
    """mtime самого свежего файла-урока (0.0, если уроков нет).

    Урок — по `lesson_files.is_lesson_file`, ТОТ ЖЕ набор, что видят каталог и ретривер.
    До 0.10.0 здесь был glob по маске `f"{prefix}_*.md"`, и это ломало стража насмерть:
    урок, названный не по приставке (а имена файлов пишет авто-память Claude Code, не
    движок), сюда не попадал → страж требовал зафиксировать урок, урок писали, страж
    требовал снова. При каталоге вообще без приставок функция возвращала ровно 0.0 —
    «уроков нет» при полной папке.

    Страж не имеет права требовать того, чего не умеет замечать: набор здесь обязан
    совпадать с каталогом. Расширение строго МЯГЧЕ — оно может только снять блок, но
    не создать новый."""
    return max((os.path.getmtime(f) for f in lesson_paths(cfg)), default=0.0)


def last_commit_ts(cwd: str) -> int:
    """Unix-время последнего git-коммита в cwd (0, если не git / нет коммитов / ошибка)."""
    return _git(cwd, "%ct", as_int=True)


def last_commit_msg(cwd: str) -> str:
    """ПОЛНОЕ сообщение последнего git-коммита (тема + тело, `%B`) в cwd ("" если не git /
    нет коммитов / ошибка).

    Именно `%B`, не `%s` (только тема): `Closes #N` часто кладут в ТЕЛО коммита (GitHub так
    и распознаёт авто-закрытие). С `%s` привратник закрытия `closure_reminder` молча НЕ
    срабатывал бы на body-based закрытие.
    Поймано dogfood'ом на закрытии #memory-stale-lesson-guard (2026-06-28)."""
    return _git(cwd, "%B")


def last_commit_sha(cwd: str) -> str:
    """Полный sha последнего git-коммита в cwd ("" если не git / нет коммитов / ошибка).

    Нужен stale_reconcile для разовости нуджа по (сессия, закрывающий коммит)."""
    return _git(cwd, "%H")


def _git(cwd: str, fmt: str, as_int: bool = False):
    try:
        out = subprocess.check_output(
            ["git", "-C", cwd, "log", "-1", f"--format={fmt}"],
            stderr=subprocess.DEVNULL, text=True, timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return 0 if as_int else ""
    if as_int:
        try:
            return int(out) if out else 0
        except ValueError:
            return 0
    return out


def reminder_message(cfg: MemoryConfig) -> str:
    """Generic-текст напоминания (без проектной методологии — её добавляет проектный хук)."""
    return msg(cfg, "stop_check.reminder_message")


def should_remind(cfg: Optional[MemoryConfig], cwd: str, now_ts: float) -> Optional[str]:
    """Текст блокировки или None. Учитывает флаг включения и окно свежести из конфига."""
    cfg = cfg or get_config()
    if not cfg.stop_lessons_enabled:
        return None
    commit_ts = last_commit_ts(cwd)
    feedback_ts = newest_lesson_mtime(cfg)
    if decide(commit_ts, feedback_ts, now_ts, cfg.stop_commit_age_limit_seconds):
        return reminder_message(cfg)
    return None


# ── Привратник закрытия задачи (Closes #N без записанного урока про задачу) ──────

def extract_closed_task(commit_msg: str, pattern: str) -> Optional[str]:
    """Номер закрываемой задачи из коммита по шаблону, или None.

    Возвращает ПЕРВУЮ непустую capture-группу совпадения — не жёстко группу 1.
    Зачем: разные формы закрытия ставят id по РАЗНЫЕ стороны ключевого слова, и
    одной capture-группой обе не покрыть (англ. «Closes #id» — id ПОСЛЕ слова;
    рус. «#id закрыт» — id ДО слова). Шаблон-альтернатива кладёт id в РАЗНЫЕ группы
    по ветке, первая непустая = сработавшая ветка. Для одногруппового шаблона
    (дефолт) первая непустая группа и есть группа 1 → поведение не меняется; шаблон
    без групп больше не падает (None вместо IndexError на m.group(1))."""
    if not commit_msg:
        return None
    try:
        m = re.search(pattern, commit_msg)
    except re.error:
        return None
    if not m:
        return None
    return next((g for g in m.groups() if g), None)


def task_lesson_recorded(cfg: MemoryConfig, task_id: str) -> bool:
    """Есть ли уже запись про задачу `#task_id`: в ЛЮБОМ файле-уроке или в архиве
    прецедентов. Ищем хэштег-форму `#<id>` с границей справа — точно и без ложных
    совпадений (`#58` НЕ матчит `#580`/`#58-foo`; id бывает числом ИЛИ слагом).

    Набор уроков — общий (`lesson_files`), как у стража завершения и каталога: до 0.10.0
    перебирались только приставки, и урок про задачу, названный иначе, привратник
    закрытия не находил — требовал записать уже записанное."""
    needle_re = re.compile(r"#" + re.escape(task_id) + r"(?![\w-])")
    mem = Path(cfg.memory_dir)
    candidates: list = list(lesson_paths(cfg))
    candidates += glob.glob(str(mem / cfg.archive_dir_name / "*.md"))
    for path in candidates:
        try:
            if needle_re.search(Path(path).read_text(encoding="utf-8")):
                return True
        except OSError:
            continue
    return False


def closure_reminder(cfg: Optional[MemoryConfig], cwd: str) -> Optional[str]:
    """Блок-текст, если ПОСЛЕДНИЙ КОММИТ — закрытие задачи, а урока про неё нет. Иначе None.

    ГРАНИЦА, названная вслух (её незнание стоило двум живым проектам почти всех уроков
    по закрытым задачам): здесь читается ТОЛЬКО текст последнего коммита — `git log -1`.
    Никакого другого способа закрыть задачу этот привратник не знает и знать не может.
    Значит закрытие, не оставившее коммита, он пропускает молча: `gh issue close`,
    закрытие в вебе, закрытие чужими руками. Пока трекер вели в markdown, закрытие И
    БЫЛО коммитом, и граница не проявлялась; после перехода на GitHub Issues она стала
    дырой размером с весь трекер.
    Второй источник сигнала (перехват самой команды `gh issue close`) живёт отдельно —
    `issue_close_watch`, с 0.13.0; расширять ШАБЛОН для этого бессмысленно, он верен.
    Правя эту функцию, помните: она отвечает за коммит-путь и только за него."""
    cfg = cfg or get_config()
    if not cfg.task_close_lesson_gate:
        return None
    task_id = extract_closed_task(last_commit_msg(cwd), cfg.task_close_pattern)
    if not task_id:
        return None
    if task_lesson_recorded(cfg, task_id):
        return None
    return msg(cfg, "stop_check.closure_reminder", task_id=task_id)
