"""Мягкие подсказки перед выполнением команды оболочки (событие PreToolUse, Bash).

Две подсказки, и обе НИЧЕГО НЕ БЛОКИРУЮТ — они кладут текст в контекст модели и
возвращают управление. Направление выбрано по цене ошибки: и пропущенное ревью, и
неудачно названная задача обратимы (ревью можно провести после, заголовок — переписать),
а блокирующий страж на разборе произвольной команды оболочки ошибался бы часто и был бы
снят вместе с защитой. Поэтому здесь перечень ЗАПРЕЩЁННОГО и мягкий тон.

Обе подсказки пришли из проектов-потребителей, где жили отдельными скриптами на bash и
успели разойтись между собой копиями. Расползание копий одного механизма — ровно то, что
движок существует лечить, поэтому механизм переехал сюда, а всё проектное (пути, имена
серверов, каталог контекста задач) вынесено в конфиг и в каталог сообщений.

Обе включены по умолчанию: настройка, которую надо сперва найти и включить, до того, кто
за этим не следит, не доходит. Выключаются полями `commit_review_enabled` и
`issue_formulation_enabled`.
"""
from __future__ import annotations

import re
import subprocess
from typing import List, Optional

from .config import MemoryConfig, get_config
from .messages import msg

# `gh issue create` с любым числом пробелов между словами; команда часто идёт после
# `cd … &&`, поэтому якоря на начало строки нет.
_ISSUE_CREATE_RE = re.compile(r"\bgh\s+issue\s+create\b")


def _command(event: dict) -> str:
    """Текст команды из события. Пусто — если формы не знаем (fail-open)."""
    if not isinstance(event, dict):
        return ""
    if str(event.get("tool_name") or "") != "Bash":
        return ""
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    return str(tool_input.get("command") or "")


# ── подсказка 1: ревью перед сохранением и отправкой ─────────────────────────

def _changed_code_files(cfg: MemoryConfig) -> List[str]:
    """Изменённые файлы кода: индекс + рабочее дерево, отсортированы, без повторов.

    Любая ошибка git (нет репозитория, таймаут, чужая версия) — пустой список: подсказка
    молчит. Это НЕ страж, терять тут нечего.
    """
    exts = "|".join(re.escape(e) for e in cfg.commit_review_extensions)
    if not exts:
        return []
    code_re = re.compile(rf"\.(?:{exts})$", re.IGNORECASE)
    out = ""
    for args in (["diff", "--cached", "--name-only"], ["diff", "--name-only"]):
        try:
            out += subprocess.run(
                ["git", "-C", cfg.project_root] + args,
                capture_output=True, text=True, timeout=5,
            ).stdout + "\n"
        except (OSError, subprocess.SubprocessError):
            return []
    files = sorted({line.strip() for line in out.splitlines() if line.strip()})
    return [f for f in files if code_re.search(f)]


def commit_review_note(event: dict, cfg: Optional[MemoryConfig] = None) -> str:
    """Подсказка провести ревью перед `git commit` / `git push`. Пусто — если не про то.

    Молчим, когда изменённых файлов кода нет: подсказка про ревью пустого набора — шум,
    а шумную подсказку выключают вместе с полезной.
    """
    cfg = cfg or get_config()
    if not cfg.commit_review_enabled or not cfg.commit_review_verbs:
        return ""
    command = _command(event)
    if not command:
        return ""
    verbs = "|".join(re.escape(v) for v in cfg.commit_review_verbs)
    found = re.search(rf"\bgit\s+({verbs})\b", command)
    if not found:
        return ""
    files = _changed_code_files(cfg)
    if not files:
        return ""
    limit = cfg.commit_review_max_files
    shown = ", ".join(files[:limit]) if limit else ", ".join(files)
    if limit and len(files) > limit:
        shown += msg(cfg, "bash.commit_review_more", rest=len(files) - limit)
    return msg(cfg, "bash.commit_review", action=found.group(1), files=shown)


# ── подсказка 2: формулировка задачи ─────────────────────────────────────────

def _flag_value(command: str, *flags: str) -> str:
    """Значение флага командной строки: `--flag "v"`, `--flag 'v'`, `--flag=v`, `-f v`.

    Пусто — если флага нет. Разбор нарочно грубый: это подсказка, а не страж, и
    моделировать лексер оболочки тут не нужно — цена промаха равна молчанию.
    """
    for flag in flags:
        quoted = re.search(rf"{re.escape(flag)}[= ]+(['\"])(.*?)\1", command, re.DOTALL)
        if quoted:
            return quoted.group(2)
        bare = re.search(rf"{re.escape(flag)}=([^\s]+)", command)
        if bare:
            return bare.group(1)
    return ""


def issue_formulation_notes(event: dict, cfg: Optional[MemoryConfig] = None) -> str:
    """Механически обнаружимые огрехи формулировки задачи. Пусто — если их нет.

    Проверяется ТОЛЬКО то, что видно машине без понимания смысла. Судить о том, «понятна
    ли формулировка», код не может, и попытка это делать давала бы шум на каждой задаче.
    """
    cfg = cfg or get_config()
    if not cfg.issue_formulation_enabled:
        return ""
    command = _command(event)
    if not command or not _ISSUE_CREATE_RE.search(command):
        return ""

    title = _flag_value(command, "--title", "-t")
    body = _flag_value(command, "--body", "-b")
    has_body_file = "--body-file" in command or re.search(r"(?<!\w)-F\s", command) is not None

    notes: List[str] = []
    if title:
        stripped = title.strip()
        if stripped.startswith("#"):
            notes.append(msg(cfg, "bash.issue_title_leading_hash"))
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", stripped):
            notes.append(msg(cfg, "bash.issue_title_looks_like_slug"))
        # Расширение — минимум ДВЕ буквы после точки и слово перед ней: иначе «версия 1.2»
        # и сокращение «e.g.» считались бы именем файла, а ложная подсказка на каждой
        # второй задаче гасит вместе с собой и верные.
        if re.search(r"(?<=\w)\.[A-Za-z]{2,5}\b|/[\w.-]+/", stripped):
            notes.append(msg(cfg, "bash.issue_title_has_path"))
        if re.search(r"[A-ZА-ЯЁ]{3,}", stripped):
            notes.append(msg(cfg, "bash.issue_title_has_acronym"))
    limit = cfg.issue_body_warn_chars
    if (
        body and limit and len(body) > limit
        and not has_body_file
        and cfg.issue_context_dir
        and cfg.issue_context_dir not in body
    ):
        notes.append(msg(cfg, "bash.issue_body_long_without_context",
                         limit=limit, context_dir=cfg.issue_context_dir))
    if not notes:
        return ""
    return msg(cfg, "bash.issue_formulation_header") + "\n" + "\n".join(f"  - {n}" for n in notes)


def notes(event: dict, cfg: Optional[MemoryConfig] = None) -> str:
    """Обе подсказки одной строкой. Пусто — если сказать нечего.

    Одно событие на обе: второй процесс на каждую команду оболочки — заметная цена, а
    обе подсказки читают один и тот же вход.
    """
    cfg = cfg or get_config()
    parts = [commit_review_note(event, cfg), issue_formulation_notes(event, cfg)]
    return "\n".join(p for p in parts if p)
