#!/usr/bin/env bash
# PreToolUse (Bash): снимок несохранённой работы перед командой, которая может её унести.
#
# Зачем отдельно от стража git-команд. Тот перечисляет опасные команды, а потеря
# бывает не через git: `rm -rf`, `sed -i`, перенаправление в файл, скрипт на python,
# команда, собранная в переменную. Перечнем такое не закрыть — обходы находятся
# быстрее, чем пишутся шаблоны (замерено 2026-07-20: 11 обходов через оболочку из 24).
#
# Снимок меняет саму постановку: вместо «предотвратить потерю» — «сделать потерю
# восстановимой». Это последний рубеж, когда все стражи пропустили.
#
# Как устроено: `git stash create` собирает коммит из текущих правок и НЕ трогает
# рабочее дерево (в отличие от обычного `git stash`), затем коммит закрепляется
# служебной ссылкой, чтобы его не собрал сборщик мусора.
#
# Восстановить:  git show <ссылка>:<путь>     — содержимое одного файла
#                git stash apply <ссылка>      — вернуть правки целиком
#                git for-each-ref refs/claude-snapshots — список снимков
#
# ГРАНИЦА, названная вслух: `git stash create` берёт только ОТСЛЕЖИВАЕМЫЕ изменения.
# Неотслеживаемые и игнорируемые файлы в снимок не попадают — это свойство git.
# Их держит страж git-команд (для него добавлена проверка игнорируемых).
#
# Fail-open: любая неожиданность → выход 0 без вывода. Снимок не имеет права мешать
# работе: он страховка, а не гейт.
set -u

input=$(cat 2>/dev/null || true)
HOOK_INPUT="$input" python3 - <<'PY' 2>/dev/null
import json, os, re, subprocess, sys

try:
    data = json.loads(os.environ.get("HOOK_INPUT", "{}"))
except Exception:
    raise SystemExit(0)
if not isinstance(data, dict) or (data.get("tool_name") or "") != "Bash":
    raise SystemExit(0)

cmd = (data.get("tool_input") or {}).get("command") or ""
каталог = (data.get("cwd") or "").strip()
if not cmd or not каталог or not os.path.isdir(каталог):
    raise SystemExit(0)

# Снимок только перед тем, что МОЖЕТ менять файлы. Делать его на каждую читающую
# команду — значит платить временем на каждом вызове и плодить служебные ссылки;
# страж, который дорого стоит, отключают целиком.
ЧИТАЮЩИЕ = re.compile(
    r"^\s*(?:git\s+(?:status|log|diff|show|branch|remote|rev-parse|ls-files|"
    r"for-each-ref|describe|blame|config\s+--(?:get|list))"
    r"|ls|cat|head|tail|grep|rg|find|wc|echo|pwd|which|file|stat|du|df)\b", re.I)
if ЧИТАЮЩИЕ.match(cmd):
    raise SystemExit(0)


def git(*args, cwd=каталог):
    return subprocess.run(["git", "-C", cwd] + list(args),
                          capture_output=True, text=True, timeout=10)


try:
    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        raise SystemExit(0)                       # не репозиторий — нечего снимать

    # Есть ли что терять. Неотслеживаемые здесь не считаем: `stash create` их всё
    # равно не возьмёт, а лишний снимок на каждый новый файл — только шум.
    статус = git("status", "--porcelain", "--untracked-files=no")
    if статус.returncode != 0 or not статус.stdout.strip():
        raise SystemExit(0)

    снимок = git("stash", "create")
    sha = снимок.stdout.strip()
    if снимок.returncode != 0 or not sha:
        raise SystemExit(0)                       # нечего снимать либо git отказал

    # Закрепляем ссылкой, иначе коммит останется висячим и его соберёт сборщик мусора.
    # Имя — по самому снимку: одинаковое состояние даёт одну ссылку, а не гору копий.
    git("update-ref", f"refs/claude-snapshots/{sha[:12]}", sha)

    # Держим последние ПРЕДЕЛ снимков. Копятся они на каждую правку — за одну сессию
    # набралось 28. Перечень без предела становится нечитаемым, и найти в нём нужный
    # снимок невозможно, то есть страховка перестаёт работать ровно тогда, когда
    # понадобится. Свежие ценнее: потеря обнаруживается сразу, а не через месяц.
    ПРЕДЕЛ = 10
    ссылки = git("for-each-ref", "--sort=-creatordate",
                 "--format=%(refname)", "refs/claude-snapshots")
    все = [s for s in ссылки.stdout.splitlines() if s.strip()]
    for лишняя in все[ПРЕДЕЛ:]:
        git("update-ref", "-d", лишняя)
except Exception:
    raise SystemExit(0)                           # fail-open по построению
PY
exit 0
