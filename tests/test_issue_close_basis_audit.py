"""Проверка основания у заявок, закрытых АВТОМАТИЧЕСКИ (`.claude/hooks/issue_close_basis_audit.sh`).

Сеть здесь не нужна: `gh` подменяется скриптом-заглушкой в PATH, который отдаёт фикстуру
или падает заданным кодом. Так проверяется весь протокол целиком — вызов, разбор, вердикт
и канал вывода, — а не одна функция в вакууме.

ЗЕРКАЛЬНАЯ КОНВЕНЦИЯ (как в test_issue_close_basis_guard.py и test_issue_close_watch.py):
блок засчитывается ТОЛЬКО вместе с текстом в `stopReason`; молчание — ТОЛЬКО при пустых
stdout и stderr с кодом 0. Мёртвый хук проходит зелёными любые проверки «умеет ли он
говорить нет», поэтому тест на ПРОПУСК тут главный, а не парный.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "issue_close_basis_audit.sh"
NOW = datetime.now(timezone.utc)


def _iso(delta_seconds: int = 0) -> str:
    return (NOW + timedelta(seconds=delta_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _issue(number: int, closer: str | None, comment_offsets=(), closed_ago: int = 60) -> dict:
    """Заявка в форме ответа GraphQL. `closer=None` → закрыл человек."""
    closed_at = _iso(-closed_ago)
    return {
        "number": number,
        "title": f"заявка {number}",
        "closedAt": closed_at,
        "timelineItems": {"nodes": [
            {"createdAt": closed_at, "closer": ({"__typename": closer} if closer else None)}
        ]},
        "comments": {"nodes": [{"createdAt": _iso(-closed_ago + off)} for off in comment_offsets]},
    }


def _payload(*issues: dict) -> dict:
    return {"data": {"repository": {"issues": {"nodes": list(issues)}}}}


def _run(payload: dict | None, tmp_path: Path, gh_rc: int = 0) -> subprocess.CompletedProcess:
    """Запускает хук с подменённым `gh`. gh_rc != 0 → заглушка изображает сбой запроса."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(payload or {}), encoding="utf-8")
    (bindir / "gh").write_text(
        "#!/bin/bash\n"
        # `gh repo view` — только чтобы хук узнал слаг репозитория; он обязан отвечать
        # всегда, иначе тест на сбой ЗАПРОСА проверял бы не то, что заявлено.
        'if [ "$1" = "repo" ]; then echo "owner/repo"; exit 0; fi\n'
        f"if [ \"$1\" = \"api\" ]; then exit {gh_rc} ; fi\n" if gh_rc else
        "#!/bin/bash\n"
        'if [ "$1" = "repo" ]; then echo "owner/repo"; exit 0; fi\n'
        f'if [ "$1" = "api" ]; then cat "{fixture}"; exit 0; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (bindir / "gh").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env["TMPDIR"] = str(tmp_path / "t")
    (tmp_path / "t").mkdir(exist_ok=True)
    env["CME_AUDIT_THROTTLE_SECONDS"] = "0"      # троттлинг мешал бы кейсам друг другу
    return subprocess.run(
        ["bash", str(HOOK)], input="{}", capture_output=True, text=True, env=env, timeout=30,
    )


def _blocks(p: subprocess.CompletedProcess) -> bool:
    """True — заблокировано С ТЕКСТОМ; False — пропущено МОЛЧА. Иное → провал теста."""
    if p.returncode != 0:
        pytest.fail(f"хук обязан выходить с кодом 0 (протокол Stop), а вышел {p.returncode}")
    if not p.stdout.strip():
        if p.stderr.strip():
            pytest.fail(f"молчание должно быть полным, а на stderr: {p.stderr[:200]!r}")
        return False
    payload = json.loads(p.stdout)
    if payload.get("continue") is not False or not str(payload.get("stopReason") or "").strip():
        pytest.fail(f"блок без текста объяснения: {p.stdout[:200]!r}")
    return True


def test_hook_is_present_and_executable() -> None:
    assert HOOK.is_file(), f"{HOOK} отсутствует — проверка не сработает ни разу"
    assert HOOK.stat().st_mode & 0o111, f"{HOOK} не исполняем — молча не запустится"


def test_syntax_is_valid() -> None:
    """bash отдаёт код 2 и на синтаксической ошибке — проверяем отдельно и заранее."""
    p = subprocess.run(["bash", "-n", str(HOOK)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


# ── Главное: воспроизведение заявки #6 ──────────────────────────────────────

def test_blocks_auto_closed_without_basis(tmp_path: Path) -> None:
    """#6 в миниатюре: закрыто слиянием PR, комментария после закрытия нет."""
    p = _run(_payload(_issue(6, "PullRequest")), tmp_path)
    assert _blocks(p)
    assert "#6" in json.loads(p.stdout)["stopReason"]


def test_silent_when_basis_written_after_closing(tmp_path: Path) -> None:
    """Зеркало: то же авто-закрытие, но основание дописано — ровно как в реальной #6
    (там оно отстало на 294 секунды). Никаких временных окон: комментарий после закрытия
    засчитывается, когда бы он ни появился."""
    p = _run(_payload(_issue(6, "PullRequest", comment_offsets=(294,))), tmp_path)
    assert not _blocks(p)


@pytest.mark.parametrize("closer", ["Commit", "ProjectV2"])
def test_blocks_other_automatic_closers(tmp_path: Path, closer: str) -> None:
    """Не только слияние PR: ключевое слово в сообщении коммита закрывает заявку тоже,
    и именно этот канал сработал в живом случае #6."""
    assert _blocks(_run(_payload(_issue(11, closer)), tmp_path))


def test_silent_when_closed_by_human(tmp_path: Path) -> None:
    """`closer=null` — ручное закрытие: командой (её стережёт PreToolUse-страж, требующий
    основание) или кнопкой в вебе. Требовать комментарий здесь нельзя: у стража есть
    законный второй путь — основание отдельной командой ДО закрытия."""
    assert not _blocks(_run(_payload(_issue(5, None)), tmp_path))


def test_comment_before_closing_does_not_count(tmp_path: Path) -> None:
    """Обратная граница окна, похороненная на живых данных: в #6 за 69 минут ДО закрытия
    лежал содержательный комментарий на 3741 знак, не имевший отношения к основанию.
    Засчитать его — значит промолчать там, где нужно спросить."""
    assert _blocks(_run(_payload(_issue(6, "PullRequest", comment_offsets=(-4140,))), tmp_path))


def test_reopened_then_closed_again_needs_fresh_basis(tmp_path: Path) -> None:
    """Заявку переоткрыли и закрыли снова: комментарий есть, но он старше ПОСЛЕДНЕГО
    закрытия. Сверка идёт с последним событием, иначе один давний разбор навсегда
    закрывал бы вопрос по всем будущим закрытиям этой заявки."""
    issue = _issue(7, "PullRequest", closed_ago=60)
    issue["comments"]["nodes"] = [{"createdAt": _iso(-7200)}]
    assert _blocks(_run(_payload(issue), tmp_path))


def test_old_closures_are_not_dragged_up(tmp_path: Path) -> None:
    """Долг старше окна сессию не держит: страж, поднимающий всю историю, блокирует
    навсегда — и его выключают целиком вместе с пользой."""
    old = _issue(3, "PullRequest", closed_ago=90 * 24 * 3600)
    assert not _blocks(_run(_payload(old), tmp_path))


# ── Тест на ПРОПУСК: главный ────────────────────────────────────────────────

def test_silent_when_nothing_to_report(tmp_path: Path) -> None:
    """ПОЛНОЕ молчание при чистом репозитории: пустой stdout, пустой stderr, код 0.
    Без этого теста проверялось бы только умение говорить «нет», а мёртвый хук говорит
    «нет» на всё подряд."""
    p = _run(_payload(_issue(5, None), _issue(6, "PullRequest", comment_offsets=(10,))), tmp_path)
    assert p.returncode == 0 and p.stdout.strip() == "" and p.stderr.strip() == ""


def test_silent_on_empty_repository(tmp_path: Path) -> None:
    p = _run(_payload(), tmp_path)
    assert p.returncode == 0 and p.stdout.strip() == "" and p.stderr.strip() == ""


# ── «Не смог проверить» ≠ «нарушений нет» ──────────────────────────────────

@pytest.mark.parametrize("rc,marker", [(4, "авторизован"), (1, "сети")])
def test_failed_check_warns_but_never_blocks(tmp_path: Path, rc: int, marker: str) -> None:
    """Сетевая моргалка не имеет права запирать сессию — но и молчать нельзя: пустой
    ответ при ненулевом коде неотличим от чистого репозитория, это ровно тот класс
    дефекта, что описан в памяти проекта («разбор молча вернул пустоту»)."""
    p = _run(None, tmp_path, gh_rc=rc)
    assert p.returncode == 0, "сбой проверки не блокирует"
    assert p.stdout.strip() == "", "сбой проверки не выдаёт себя за нарушение"
    assert "НЕ ВЫПОЛНЕНА" in p.stderr and marker in p.stderr, p.stderr


def test_failed_check_does_not_arm_the_throttle(tmp_path: Path) -> None:
    """Метка троттлинга ставится только после УДАЧНОГО запроса. Иначе одна моргалка
    усыпляла бы проверку на весь период, а выглядела бы она работающей."""
    _run(None, tmp_path, gh_rc=1)
    stamps = list((tmp_path / "t").glob("claude-issue-basis-audit-*"))
    assert stamps == [], "после неудачи метка не ставится"
    assert _blocks(_run(_payload(_issue(6, "PullRequest")), tmp_path)), "следующая попытка живая"


def test_broken_payload_is_not_a_violation(tmp_path: Path) -> None:
    """Неожиданная форма ответа = «нарушений не вижу»: страж, падающий на чужом JSON,
    мешает работе вместо того, чтобы помогать."""
    p = _run({"data": {"repository": None}}, tmp_path)
    assert p.returncode == 0 and p.stdout.strip() == ""


# ── Троттлинг — ещё один способ тихо умереть ───────────────────────────────

def test_throttle_suppresses_repeat_and_expires(tmp_path: Path) -> None:
    """С включённым троттлингом повтор молчит, а по истечении срока проверка снова живая."""
    payload = _payload(_issue(6, "PullRequest"))
    env_tmp = tmp_path / "t"
    env_tmp.mkdir(exist_ok=True)

    def run(throttle: str) -> subprocess.CompletedProcess:
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        fixture = tmp_path / "fixture.json"
        fixture.write_text(json.dumps(payload), encoding="utf-8")
        (bindir / "gh").write_text(
            "#!/bin/bash\n"
            'if [ "$1" = "repo" ]; then echo "owner/repo"; exit 0; fi\n'
            f'if [ "$1" = "api" ]; then cat "{fixture}"; exit 0; fi\nexit 0\n',
            encoding="utf-8",
        )
        (bindir / "gh").chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
        env["TMPDIR"] = str(env_tmp)
        env["CME_AUDIT_THROTTLE_SECONDS"] = throttle
        return subprocess.run(["bash", str(HOOK)], input="{}", capture_output=True,
                              text=True, env=env, timeout=30)

    assert _blocks(run("900")), "первый прогон сообщает о долге"
    assert not _blocks(run("900")), "повтор в пределах троттла молчит"
    assert _blocks(run("0")), "по истечении троттла проверка снова живая"
