"""Тесты второго источника сигнала о закрытии задачи (`gh issue close`).

Стража, который умеет говорить «нет», проверять на одном лишь «сказал ли нет» —
бесполезно: полностью мёртвый страж проходит такие тесты зелёными (прецедент 0.12.0 —
bash отдаёт код 2 и на синтаксической ошибке, и при отклонении вызова). Поэтому здесь
на каждый случай БЛОКИРОВКИ есть зеркальный случай ПРОПУСКА, и пропуск засчитывается
только при полном молчании — пустой вывод, пустой stderr, нулевой код возврата.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

from claude_memory import issue_close_watch as W
from claude_memory import hooks_cli
from conftest import ROOT, write_lesson

NOW = 1_800_000_000.0


def _event(command: str, **extra) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, **extra}


# ── Детектор: ЛОВИТ настоящее закрытие ───────────────────────────────────────

@pytest.mark.parametrize("command,expected", [
    ("gh issue close 5", "5"),
    ("gh issue close 5 --comment 'done'", "5"),
    ("gh issue close  123", "123"),
    ("cd /tmp && gh issue close 7", "7"),
    ("sudo gh issue close 8", "8"),
    ("/opt/homebrew/bin/gh issue close 9", "9"),
    ("GH_TOKEN=x gh issue close 10", "10"),
    ("gh issue close https://github.com/o/r/issues/42", "42"),
    ("(gh issue close 11)", "11"),
    ("true; gh issue close 12", "12"),
])
def test_detect_finds_real_closures(command: str, expected: str) -> None:
    assert W.detect_close(command) == expected


def test_detect_reports_closure_without_number() -> None:
    """`""` (закрытие есть, номер не разобран) обязано отличаться от None (закрытия нет).

    Слить их — значит вернуть ровно тот тихий пропуск, против которого заведён модуль:
    закрытие СОСТОЯЛОСЬ, и промолчать о нём нельзя только потому, что номер спрятан за
    переменной."""
    assert W.detect_close("gh issue close $N") == ""
    assert W.detect_close("gh issue close $N") is not None


# ── Детектор: МОЛЧИТ там, где закрытия нет (зеркальная половина) ─────────────

@pytest.mark.parametrize("command", [
    "",
    "ls -la",
    "gh issue list",
    "gh issue comment 5 --body 'x'",
    "gh issue view 5",
    "gh issue reopen 5",
    # ЛОЖНЫЙ ДЕТЕКТ, воспроизведённый на живой команде: разделитель ВНУТРИ строкового
    # аргумента принимался за границу команды, пока содержимое кавычек не маскировали.
    'grep "foo; gh issue close 5" file.txt',
    "echo 'gh issue close 5'",
    'git commit -m "gh issue close 5"',
    # поиск подстроки вместо командной позиции ловил и это
    "echo gh issue close 5",
    "# gh issue close 5",
])
def test_detect_stays_silent_on_non_closures(command: str) -> None:
    assert W.detect_close(command) is None


def test_detect_reads_number_through_quotes() -> None:
    """Маска кавычек сохраняет ДЛИНУ, поэтому номер в кавычках не теряется: смещения в
    маскированной и исходной строке совпадают, и хвост режется там же, где считается."""
    assert W.detect_close('gh issue close "5"') == "5"


def test_detect_does_not_borrow_number_from_next_command() -> None:
    """Номер берётся из хвоста аргументов САМОГО close, а не из соседней команды —
    зеркало грабли «ложное молчание», где чужой флаг засчитывался за свой."""
    assert W.detect_close("gh issue close $N && sleep 42") == ""


# ── Регрессии, найденные ревью перед выпуском ───────────────────────────────

def test_number_is_not_borrowed_from_a_flag_value() -> None:
    """Ложный номер ХУЖЕ неизвестного: он гасит долг по уроку про ДРУГУЮ задачу, то есть
    возвращает тихий пропуск. `--comment "попытка 3"` не имеет права стать задачей #3."""
    assert W.detect_close('gh issue close $N --comment "попытка 3"') == ""
    assert W.detect_close('gh issue close 7 --comment "попытка 3"') == "7"


def test_detect_is_linear_on_a_long_slashy_argument() -> None:
    """Квадратичный разбор здесь не косметика: хук убивают по таймауту (10 с), а убитый
    хук теряет НАСТОЯЩЕЕ закрытие молча — рецидив чинимого дефекта. Замер до правки:
    40 КБ → 3.9 с."""
    import time
    command = "gh issue close $ISSUE --comment " + ("/a" * 40000)
    started = time.perf_counter()
    W.detect_close(command)
    assert time.perf_counter() - started < 0.5


def test_marker_write_is_atomic(cfg, tmp_path) -> None:
    """Метка появляется целиком или не появляется вовсе: параллельная сессия не должна
    застать её пустой, счесть битой и подмести свежее закрытие."""
    W.record_close(_event("gh issue close 5"), cfg, str(tmp_path), NOW, "s1")
    assert json.loads(W.marker_path(cfg, str(tmp_path), "5").read_text(encoding="utf-8"))
    assert list(W.marker_dir(cfg).glob("*.tmp")) == []   # временных огрызков не остаётся


def test_every_registered_event_has_a_dispatcher_branch() -> None:
    """Опечатка в имени события дала бы ПОЛНОСТЬЮ мёртвый хук: неизвестное имя диспетчер
    глотает молча (fail-open), а wrapper-тесты зовут обёртку литеральным именем и
    рассинхрона не заметят. Ровно тот «мёртвый страж, проходящий зелёным», ради которого
    написан этот файл."""
    from claude_memory import installer

    source = (ROOT / "claude_memory" / "hooks_cli.py").read_text(encoding="utf-8")
    for _event_name, _matcher, subcommand, _timeout in installer.HOOK_REGISTRATIONS:
        assert f'== "{subcommand}"' in source, (
            f"событие {subcommand!r} зарегистрировано в installer, "
            f"но ветки в hooks_cli.main нет — хук будет молчать всегда"
        )


# ── Разобранный номер попадает в ПУТЬ — значит его надо сузить ──────────────

@pytest.mark.parametrize("command", [
    "gh issue close ../../../../etc/passwd",
    "gh issue close /etc/passwd",
    "gh issue close ..%2f..%2fevil",
    "gh issue close $(echo 5)",
    "gh issue close ５",                       # юникод-цифра: `\\d` её принимает, `[0-9]` нет
    "gh issue close " + "9" * 500,             # имя длиннее лимита файловой системы
])
def test_number_never_escapes_the_marker_directory(cfg, tmp_path, command) -> None:
    """Единственное место, где текст ЧУЖОЙ команды попадает в путь файловой системы.
    Что бы ни пришло, метка обязана лечь внутрь своего каталога и никуда больше."""
    issue = W.detect_close(command)
    assert issue is not None                    # закрытие замечено в любом случае
    path = W.marker_path(cfg, str(tmp_path), issue or "")
    assert path.parent == W.marker_dir(cfg)
    assert "/" not in path.name and ".." not in path.name
    assert len(path.name) < 255                 # имя влезает в лимит файловой системы
    W.record_close(_event(command), cfg, str(tmp_path), NOW, "s1")
    written = list(W.marker_dir(cfg).glob("*")) if W.marker_dir(cfg).exists() else []
    assert all(p.parent == W.marker_dir(cfg) for p in written)


def test_command_text_never_lands_in_the_marker(cfg, tmp_path) -> None:
    """В команде бывают токены. В метку идут только номер, время, каталог и id сессии —
    текст команды не сохраняется нигде и не уходит в контекст модели."""
    secret = "GH_TOKEN=ghp_supersecret1234567890 gh issue close 5"
    out = W.record_close(_event(secret), cfg, str(tmp_path), NOW, "s1")
    assert "ghp_supersecret" not in (out or "")
    body = W.marker_path(cfg, str(tmp_path), "5").read_text(encoding="utf-8")
    assert "ghp_supersecret" not in body


# ── Постановка метки ────────────────────────────────────────────────────────

def test_record_close_writes_marker_and_acks(cfg, tmp_path) -> None:
    out = W.record_close(_event("gh issue close 5"), cfg, str(tmp_path), NOW, "s1")
    assert out and "#5" in out
    marker = W.marker_path(cfg, str(tmp_path), "5")
    assert marker.is_file()
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["issue"] == "5" and data["ts"] == int(NOW)


def test_marker_dir_is_private(cfg) -> None:
    """Метки лежат под приватным префиксом → невидимы указателю и ретриверу."""
    assert W.marker_dir(cfg).name.startswith(cfg.private_file_prefix)


def test_record_close_silent_when_lesson_already_recorded(cfg, tmp_path) -> None:
    """Штатный порядок «записал урок → выложил → закрыл задачу» не создаёт долга."""
    write_lesson(cfg.memory_dir, "feedback_task.md", description="d", body="про #5")
    assert W.record_close(_event("gh issue close 5"), cfg, str(tmp_path), NOW, "s1") is None
    assert not W.marker_path(cfg, str(tmp_path), "5").exists()


@pytest.mark.parametrize("event", [
    {"tool_name": "Read", "tool_input": {"command": "gh issue close 5"}},
    {"tool_name": "Bash", "tool_input": {}},
    {"tool_name": "Bash", "tool_input": {"command": "ls"}},
    {},
])
def test_record_close_silent_on_irrelevant_events(cfg, tmp_path, event) -> None:
    assert W.record_close(event, cfg, str(tmp_path), NOW, "s1") is None
    assert not W.marker_dir(cfg).exists()


def test_record_close_skips_failed_command(cfg, tmp_path) -> None:
    """Команда не выполнилась (прервана/ошибка) — закрытия не было, долга нет."""
    ev = _event("gh issue close 5", tool_response={"interrupted": True})
    assert W.record_close(ev, cfg, str(tmp_path), NOW, "s1") is None
    assert not W.marker_path(cfg, str(tmp_path), "5").exists()


def test_record_close_assumes_success_on_unknown_response_shape(cfg, tmp_path) -> None:
    """Неизвестный формат ответа = считаем удачей. Ошибиться в сторону лишнего вопроса
    дешевле, чем в сторону молчания: молчание — это и есть чинимый дефект."""
    ev = _event("gh issue close 5", tool_response={"stdout": "", "stderr": "warning: x"})
    assert W.record_close(ev, cfg, str(tmp_path), NOW, "s1") is not None


# ── Stop: блокировка и её снятие ────────────────────────────────────────────

def test_pending_reminder_blocks_after_close(cfg, tmp_path) -> None:
    W.record_close(_event("gh issue close 5"), cfg, str(tmp_path), NOW, "s1")
    reason = W.pending_reminder(cfg, str(tmp_path), NOW + 60)
    assert reason and "#5" in reason


def test_pending_reminder_silent_without_markers(cfg, tmp_path) -> None:
    assert W.pending_reminder(cfg, str(tmp_path), NOW) is None


def test_lesson_with_issue_number_quenches(cfg, tmp_path) -> None:
    W.record_close(_event("gh issue close 5"), cfg, str(tmp_path), NOW, "s1")
    write_lesson(cfg.memory_dir, "feedback_task.md", description="d", body="разбор #5")
    assert W.pending_reminder(cfg, str(tmp_path), NOW + 60) is None
    assert not W.marker_path(cfg, str(tmp_path), "5").exists()


def test_newer_lesson_quenches_even_without_number(cfg, tmp_path) -> None:
    """Оба живых проекта именуют задачи слагом, а не номером. Урок, записанный ПОСЛЕ
    закрытия, снимает долг — проверка намеренно слабее точной: цена ошибки здесь «не
    спросил лишний раз», а не «запер сессию навсегда»."""
    W.record_close(_event("gh issue close 5"), cfg, str(tmp_path), NOW, "s1")
    f = write_lesson(cfg.memory_dir, "feedback_slug.md", description="d", body="про #moya-zadacha")
    os.utime(f, (NOW + 30, NOW + 30))
    assert W.pending_reminder(cfg, str(tmp_path), NOW + 60) is None


def test_older_lesson_does_not_quench(cfg, tmp_path) -> None:
    """Урок, написанный ДО закрытия и без номера, долг не снимает — иначе страж гасился
    бы любой прошлой записью и не срабатывал никогда."""
    f = write_lesson(cfg.memory_dir, "feedback_old.md", description="d", body="ни при чём")
    os.utime(f, (NOW - 3600, NOW - 3600))
    W.record_close(_event("gh issue close 5"), cfg, str(tmp_path), NOW, "s1")
    assert W.pending_reminder(cfg, str(tmp_path), NOW + 60) is not None


def test_ttl_sweeps_stale_marker(cfg, tmp_path) -> None:
    """Срок годности — предохранитель от вечной блокировки. Страж, способный запереть
    сессию насмерть, выключают целиком вместе с пользой."""
    W.record_close(_event("gh issue close 5"), cfg, str(tmp_path), NOW, "s1")
    later = NOW + cfg.task_close_marker_ttl_seconds + 1
    assert W.pending_reminder(cfg, str(tmp_path), later) is None
    assert not W.marker_path(cfg, str(tmp_path), "5").exists()


def test_ttl_zero_disables_expiry(cfg, tmp_path) -> None:
    c = replace(cfg, task_close_marker_ttl_seconds=0)
    W.record_close(_event("gh issue close 5"), c, str(tmp_path), NOW, "s1")
    assert W.pending_reminder(c, str(tmp_path), NOW + 10 ** 7) is not None


def test_broken_marker_is_swept_not_blocking(cfg, tmp_path) -> None:
    """Битая метка не имеет права держать сессию: «не смог прочесть» — не «есть долг»."""
    d = W.marker_dir(cfg)
    d.mkdir(parents=True)
    bad = d / f"{W._cwd_digest(str(tmp_path))}_5.json"
    bad.write_text("{не json", encoding="utf-8")
    assert W.pending_reminder(cfg, str(tmp_path), NOW) is None
    assert not bad.exists()


# ── Параллельные сессии: чужое рабочее дерево не блокирует моё ───────────────

def test_marker_from_another_worktree_does_not_block(cfg, tmp_path) -> None:
    """memory_dir общий для всех рабочих деревьев проекта, а закрытие сделано в другом:
    у этой сессии нет ни контекста задачи, ни повода писать по ней урок. Ложная
    блокировка чужой сессии хуже пропуска — она бьёт по тому, кто ни при чём."""
    other = tmp_path / "other-worktree"
    other.mkdir()
    W.record_close(_event("gh issue close 5"), cfg, str(other), NOW, "s1")
    assert W.pending_reminder(cfg, str(tmp_path), NOW + 60) is None
    assert W.pending_reminder(cfg, str(other), NOW + 60) is not None


def test_expired_foreign_marker_is_swept(cfg, tmp_path) -> None:
    """Протухшие чужие метки подметает любая сессия: своего Stop у мёртвого дерева уже
    не будет, а движок нигде не убирает за собой по расписанию."""
    other = tmp_path / "other-worktree"
    other.mkdir()
    W.record_close(_event("gh issue close 5"), cfg, str(other), NOW, "s1")
    W.pending_reminder(cfg, str(tmp_path), NOW + cfg.task_close_marker_ttl_seconds + 1)
    assert not W.marker_path(cfg, str(other), "5").exists()


# ── Выключатели ─────────────────────────────────────────────────────────────

def test_command_watch_off_disables_both_halves(cfg, tmp_path) -> None:
    c = replace(cfg, task_close_command_watch=False)
    assert W.record_close(_event("gh issue close 5"), c, str(tmp_path), NOW, "s1") is None
    # метка, поставленная до выключения, тоже перестаёт блокировать
    W.record_close(_event("gh issue close 5"), cfg, str(tmp_path), NOW, "s1")
    assert W.pending_reminder(c, str(tmp_path), NOW + 60) is None


def test_master_gate_off_disables_watch(cfg, tmp_path) -> None:
    """`task_close_lesson_gate` остаётся мастер-выключателем: выключен — не работает ничто."""
    c = replace(cfg, task_close_lesson_gate=False)
    assert W.record_close(_event("gh issue close 5"), c, str(tmp_path), NOW, "s1") is None
    W.record_close(_event("gh issue close 5"), cfg, str(tmp_path), NOW, "s1")
    assert W.pending_reminder(c, str(tmp_path), NOW + 60) is None


# ── Совместимость: коммит-путь не сдвинут ───────────────────────────────────

def test_commit_path_wins_over_command_path(cfg, tmp_path, monkeypatch) -> None:
    """Новый источник только ДОБАВЛЯЕТ срабатывания. Когда сработали оба, отвечает
    старый — его текст точнее (номер взят из шаблона проекта)."""
    monkeypatch.setattr("claude_memory.stop_check.last_commit_msg", lambda cwd: "Closes #77")
    W.record_close(_event("gh issue close 5"), cfg, str(tmp_path), NOW, "s1")
    reason = hooks_cli.ev_stop(cfg, str(tmp_path), NOW + 60, "s1", str(tmp_path))
    assert reason and "#77" in reason


def test_ev_stop_uses_command_path_when_commit_is_silent(cfg, tmp_path, monkeypatch) -> None:
    """Ровно чинимый случай: коммита-закрытия нет вообще, задачу закрыли командой."""
    monkeypatch.setattr("claude_memory.stop_check.last_commit_msg", lambda cwd: "обычный коммит")
    monkeypatch.setattr("claude_memory.stop_check.last_commit_ts", lambda cwd: 0)
    assert hooks_cli.ev_stop(cfg, str(tmp_path), NOW, "s1", str(tmp_path)) is None
    W.record_close(_event("gh issue close 5"), cfg, str(tmp_path), NOW, "s1")
    reason = hooks_cli.ev_stop(cfg, str(tmp_path), NOW + 60, "s1", str(tmp_path))
    assert reason and "#5" in reason


# ── Сквозной протокол через обёртку: блок с текстом, пропуск — в полной тишине ──

def _run(event: str, payload: dict, cfg_path: Path, cwd: Path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["CLAUDE_MEMORY_CONFIG"] = str(cfg_path)
    return subprocess.run(
        ["bash", str(ROOT / "hooks" / "cme_hook.sh"), event],
        input=json.dumps(payload), capture_output=True, text=True,
        env=env, cwd=str(cwd), timeout=30,
    )


@pytest.fixture
def sandbox(tmp_path):
    mem = tmp_path / "memory"; mem.mkdir()
    proj = tmp_path / "proj"; proj.mkdir()
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"memory_dir": str(mem), "project_root": str(proj)}),
                    encoding="utf-8")
    return cfgp, mem, proj


def test_wrapper_silent_on_ordinary_command(sandbox) -> None:
    """ТЕСТ НА ПРОПУСК. Он важнее теста на блокировку: bash отдаёт код 2 и при отклонении
    вызова, и на собственной синтаксической ошибке, поэтому мёртвый страж проходит любые
    проверки «умеет ли говорить нет». Пропуск засчитывается только при ПОЛНОМ молчании."""
    cfgp, _, proj = sandbox
    p = _run("issue-close-watch", _event("ls -la"), cfgp, proj)
    assert p.returncode == 0
    assert p.stdout.strip() == ""
    assert p.stderr.strip() == ""


def test_wrapper_records_and_then_blocks_stop(sandbox) -> None:
    cfgp, _, proj = sandbox
    rec = _run("issue-close-watch", _event("gh issue close 5"), cfgp, proj)
    assert rec.returncode == 0 and "Traceback" not in rec.stderr
    assert json.loads(rec.stdout)["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

    stop = _run("stop-check", {"hook_event_name": "Stop"}, cfgp, proj)
    assert stop.returncode == 0 and "Traceback" not in stop.stderr
    payload = json.loads(stop.stdout)
    assert payload["continue"] is False and "#5" in payload["stopReason"]


def test_wrapper_stop_silent_without_closure(sandbox) -> None:
    """Зеркало предыдущего: без закрытия Stop обязан молчать полностью."""
    cfgp, _, proj = sandbox
    p = _run("stop-check", {"hook_event_name": "Stop"}, cfgp, proj)
    assert p.returncode == 0 and p.stdout.strip() == "" and p.stderr.strip() == ""
