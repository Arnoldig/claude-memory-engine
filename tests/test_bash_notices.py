"""Мягкие подсказки перед командой оболочки (`claude_memory/bash_notices.py`).

ПОЧЕМУ ОНИ В ДВИЖКЕ. Обе жили отдельными скриптами на bash сразу в двух проектах и
успели разойтись редакциями — расползание копий одного механизма движок и существует
лечить. Всё проектное (пути, каталог контекста задач, набор расширений) вынесено в
конфиг, все тексты — в каталог сообщений: дефолт библиотеки языко-нейтрален.

КОНВЕНЦИЯ НАБОРА. У каждого срабатывания есть ПАРНЫЙ случай молчания. Подсказка,
которая приходит всегда, неотличима от подсказки, которая не работает вовсе: обе не
несут сведений, и обе одинаково быстро перестают читаться.
"""
from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from claude_memory import bash_notices as B


def _event(command: str, tool: str = "Bash") -> dict:
    return {"tool_name": tool, "tool_input": {"command": command}}


@pytest.fixture
def repo(cfg, tmp_path: Path):
    """Конфиг, чей project_root — настоящий git-репозиторий с изменённым файлом кода."""
    root = tmp_path / "repo"
    root.mkdir()
    def git(*args):
        subprocess.run(["git", "-C", str(root)] + list(args), capture_output=True, timeout=10)
    git("init", "-q")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "t")
    (root / "app.py").write_text("print(1)\n", encoding="utf-8")
    (root / "notes.txt").write_text("не код\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "start")
    (root / "app.py").write_text("print(2)\n", encoding="utf-8")
    (root / "notes.txt").write_text("тоже не код\n", encoding="utf-8")
    return replace(cfg, project_root=str(root))


# ── подсказка 1: ревью перед сохранением и отправкой ─────────────────────────

@pytest.mark.parametrize("command", ["git commit -m x", "git push origin main",
                                     "cd /tmp && git commit -am x"])
def test_review_note_appears_before_saving_and_sending(repo, command: str) -> None:
    out = B.commit_review_note(_event(command), repo)
    assert out and "app.py" in out


def test_only_code_files_are_listed(repo) -> None:
    """Не-код в перечень не идёт: перечислять нечитаемые ревьюером файлы — шум."""
    out = B.commit_review_note(_event("git commit -m x"), repo)
    assert "app.py" in out and "notes.txt" not in out


def test_long_file_list_is_cut_and_says_how_many_are_hidden(repo) -> None:
    """Обрезка без счётчика читалась бы как «это всё» — то есть врала бы.

    Новые файлы здесь ДОБАВЛЯЮТСЯ в индекс намеренно: перечень берётся из индекса и
    рабочего дерева, а неотслеживаемый файл не виден ни там, ни там. В жизни это не
    мешает — к моменту сохранения файлы уже добавлены, — но тест обязан воспроизводить
    именно ту форму, на которой подсказка работает.
    """
    for i in range(15):
        (Path(repo.project_root) / f"m{i}.py").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", repo.project_root, "add", "-A"], capture_output=True, timeout=10)
    out = B.commit_review_note(_event("git commit -m x"), replace(repo, commit_review_max_files=3))
    listed = out.count(".py")
    full = B.commit_review_note(_event("git commit -m x"), replace(repo, commit_review_max_files=0))
    hidden = full.count(".py") - 3
    assert listed == 3, f"показано имён: {listed}, ожидалось 3"
    assert str(hidden) in out, f"в подсказке нет числа скрытых ({hidden}): {out!r}"


@pytest.mark.parametrize("command", ["git status", "git log --oneline", "git diff",
                                     "ls -la", "python3 -m pytest"])
def test_silent_on_commands_that_neither_save_nor_send(repo, command: str) -> None:
    assert B.commit_review_note(_event(command), repo) == ""


def test_silent_when_no_code_file_changed(cfg, tmp_path: Path) -> None:
    """Ревью пустого набора — подсказка ни о чём."""
    root = tmp_path / "clean"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], capture_output=True, timeout=10)
    assert B.commit_review_note(_event("git commit -m x"), replace(cfg, project_root=str(root))) == ""


def test_silent_outside_a_git_repository(cfg, tmp_path: Path) -> None:
    """Ошибка git — молчание, а не падение: подсказка не имеет права мешать работе."""
    assert B.commit_review_note(_event("git commit -m x"), replace(cfg, project_root=str(tmp_path))) == ""


def test_switch_off_really_silences_the_review_note(repo) -> None:
    assert repo.commit_review_enabled is True
    assert B.commit_review_note(_event("git commit -m x"), replace(repo, commit_review_enabled=False)) == ""


# ── подсказка 2: формулировка задачи ─────────────────────────────────────────

@pytest.mark.parametrize("title,expected_key", [
    ("#42 и ещё кое-что",              "issue_title_leading_hash"),
    ("fix-empty-input-crash",          "issue_title_looks_like_slug"),
    ("Починить падение в app/main.py", "issue_title_has_path"),
    ("Починить обработчик API",        "issue_title_has_acronym"),
])
def test_mechanically_visible_wording_defects(cfg, title: str, expected_key: str) -> None:
    from claude_memory.messages import msg
    out = B.issue_formulation_notes(_event(f'gh issue create --title "{title}"'), cfg)
    assert msg(cfg, f"bash.{expected_key}") in out


@pytest.mark.parametrize("title", [
    "Починить падение при пустом вводе",
    "Fix the crash on empty input",
    "Поднять версию до 1.2 и обновить оба списка",   # «1.2» — не имя файла
])
def test_silent_on_a_normally_worded_title(cfg, title: str) -> None:
    assert B.issue_formulation_notes(_event(f'gh issue create --title "{title}"'), cfg) == ""


@pytest.mark.parametrize("command", [
    'gh issue comment 8 --body "#42 текст"',
    'gh issue close 8',
    'gh pr create --title "fix-slug-like-title"',
])
def test_silent_when_the_command_does_not_create_an_issue(cfg, command: str) -> None:
    """Парный случай к каждому срабатыванию: тот же текст, но другая команда."""
    assert B.issue_formulation_notes(_event(command), cfg) == ""


def test_long_body_hint_only_when_the_project_has_a_context_dir(cfg) -> None:
    """Советовать положить файл в каталог, которого в проекте нет, — заведомо ложный совет,
    поэтому пустой `issue_context_dir` гасит проверку целиком."""
    long_body = "я" * 400
    command = f'gh issue create --title "Обычный заголовок" --body "{long_body}"'
    assert B.issue_formulation_notes(_event(command), cfg) == ""
    with_dir = replace(cfg, issue_context_dir="docs/context/")
    assert "docs/context/" in B.issue_formulation_notes(_event(command), with_dir)


def test_long_body_hint_is_silent_when_the_context_file_is_already_referenced(cfg) -> None:
    with_dir = replace(cfg, issue_context_dir="docs/context/")
    body = "я" * 400 + " docs/context/task.md"
    command = f'gh issue create --title "Обычный заголовок" --body "{body}"'
    assert B.issue_formulation_notes(_event(command), with_dir) == ""


def test_switch_off_really_silences_the_wording_notes(cfg) -> None:
    assert cfg.issue_formulation_enabled is True
    command = 'gh issue create --title "fix-slug-like-title"'
    assert B.issue_formulation_notes(_event(command), replace(cfg, issue_formulation_enabled=False)) == ""


# ── общее: чужая форма события не роняет хук ────────────────────────────────

@pytest.mark.parametrize("event", [
    {}, {"tool_name": "Write", "tool_input": {"command": "git commit -m x"}},
    {"tool_name": "Bash", "tool_input": None}, {"tool_name": "Bash"}, None,
])
def test_fail_open_on_a_foreign_event(cfg, event) -> None:
    assert B.notes(event, cfg) == ""


def test_a_mere_mention_of_the_command_also_produces_a_hint_and_that_is_accepted(cfg) -> None:
    """Упоминание команды в тексте (`echo "gh issue create …"`) подсказку ВЫЗОВЕТ.

    Так и задумано, и вот почему. Разобрать оболочку регэкспом нельзя — в этом проекте
    попытка отличить цитату от выполнения на БЛОКИРУЮЩЕМ страже дала семь обходов и была
    откачена. Здесь цена ошибки другая и она несимметрична в обратную сторону: лишняя
    подсказка стоит одной строки текста, а пропущенная — плохо сформулированной задачи,
    которую потом читают люди. Поэтому берём заведомо широкое распознавание и
    ЗАКРЕПЛЯЕМ это тестом, чтобы никто не «починил» его, приняв за недосмотр.
    """
    out = B.issue_formulation_notes(_event('echo "gh issue create --title \'#1\'"'), cfg)
    assert out != ""


def test_the_hint_is_emitted_under_the_right_event_name(tmp_path: Path) -> None:
    """Канал доставки: `hookSpecificOutput.hookEventName` обязан быть `PreToolUse`.

    Клиент разбирает этот блок ПО ИМЕНИ СОБЫТИЯ и пару с чужим именем игнорирует целиком.
    Значит описка здесь не ломает ничего видимого — подсказка просто не доходит, и это
    неотличимо от «сказать было нечего». Проверяем ЗАПУСКОМ самого хука, а не вызовом
    функции: сама подсказка может считаться верно и не дойти, а до человека доходит
    только то, что напечатано.
    """
    import json
    import os
    import sys

    root = tmp_path / "repo"
    root.mkdir()
    def git(*args):
        subprocess.run(["git", "-C", str(root)] + list(args), capture_output=True, timeout=10)
    git("init", "-q")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "t")
    (root / "app.py").write_text("print(1)\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "start")
    (root / "app.py").write_text("print(2)\n", encoding="utf-8")

    mem = tmp_path / "mem"
    mem.mkdir()
    conf = tmp_path / "claude-memory.config.json"
    conf.write_text(json.dumps({"memory_dir": str(mem), "project_root": str(root)}), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "claude_memory.hooks_cli", "pre-bash-notice"],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}),
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "CLAUDE_MEMORY_CONFIG": str(conf)},
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert proc.returncode == 0, f"хук обязан быть fail-open, код {proc.returncode}"
    payload = json.loads(proc.stdout)
    block = payload["hookSpecificOutput"]
    assert block["hookEventName"] == "PreToolUse", (
        f"подсказка уйдёт в пустоту: имя события {block['hookEventName']!r}"
    )
    assert "app.py" in block["additionalContext"]
