"""Страж приватных слов (`.claude/hooks/private_words_guard.sh` + блок в `.githooks/pre-push`).

Репозиторий публичный, и текст комментария к заявке уезжает одним вызовом — отозвать его
нельзя, правка остаётся видимой в истории правок GitHub. Так названия рабочих проектов
попали в комментарии заявок #6 и #13 (вычищено вручную 18.07.2026); git-хук их не поймал
бы никогда, потому что комментарии заявок в git не попадают вовсе.

СПИСОК СЛОВ ЖИВЁТ ВНЕ GIT, поэтому тесты подкладывают СВОЙ временный список и работают
на выдуманных словах. Иначе сами тесты стали бы четвёртой копией того, что скрывается, —
ровно та ошибка, которую в этом проекте уже разбирали на эталоне форм закрытия.

Конвенция та же, что у соседних стражей: блок засчитывается ТОЛЬКО вместе с текстом на
stderr, пропуск — ТОЛЬКО при пустом stderr.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "private_words_guard.sh"
PREPUSH = ROOT / ".githooks" / "pre-push"
SECRET = "вымышленноеслово"          # намеренно не настоящее: см. докстринг


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """Копия стража с приватным списком из выдуманных слов."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "private-words.txt").write_text(
        f"# комментарий игнорируется\n\n{SECRET}\nSecondWord\n", encoding="utf-8")
    return tmp_path


def _run(command: str, project_dir: Path) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir))
    return subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True,
                          text=True, env=env, timeout=30)


def _blocks(p: subprocess.CompletedProcess) -> bool:
    """True — отклонено С ОБЪЯСНЕНИЕМ; False — пропущено МОЛЧА. Иное → провал теста."""
    if p.returncode == 2 and "приватного списка" in p.stderr:
        return True
    if p.returncode == 0 and not p.stderr.strip():
        return False
    pytest.fail(f"страж сломан: код={p.returncode}, stderr={p.stderr[:200]!r}")


def test_hooks_are_present_and_executable() -> None:
    assert HOOK.is_file() and HOOK.stat().st_mode & 0o111, f"{HOOK} не исполняем"
    assert PREPUSH.is_file(), f"{PREPUSH} отсутствует"


@pytest.mark.parametrize("template", [
    'gh issue comment 8 --body "замер на {w}"',
    'gh issue create --title x --body "{w}"',
    'gh pr create --title x --body "про {w}"',
    'gh pr edit 9 --body "{w}"',
    'gh release create v1 --notes "история {w}"',
    'gh gist create f.md --desc "{w}"',
    'gh api -X PATCH repos/o/r/issues/comments/1 -f body="{w}"',
    'cd /tmp && gh issue comment 8 --body "{w}"',
])
def test_blocks_publishing_channels(sandbox: Path, template: str) -> None:
    assert _blocks(_run(template.format(w=SECRET), sandbox))


@pytest.mark.parametrize("command", [
    # Читающие команды обязаны проходить: страж, ломающий поиск по этим же словам,
    # делает невозможным аудит и будет снят в первый же день.
    "grep -rn вымышленноеслово .",
    "git log --all | grep -i вымышленноеслово",
    "gh issue list --state closed",
    "gh pr view 9",
    "gh api repos/o/r/issues/6/timeline",
    "gh release view v1",
    # публикация БЕЗ приватных слов
    'gh issue comment 8 --body "замер на двух рабочих проектах"',
    'gh pr create --title x --body "обычный текст"',
    "ls -la",
    "",
])
def test_allows(sandbox: Path, command: str) -> None:
    assert not _blocks(_run(command, sandbox))


def test_reads_text_from_body_file(sandbox: Path, tmp_path: Path) -> None:
    """Длинный текст удобнее отдать файлом — значит страж обязан читать и файл.
    Иначе он проверял бы ИМЯ файла и пропускал ровно тот путь, которым публикуют разборы."""
    notes = tmp_path / "notes.md"
    notes.write_text(f"основание: проверено на {SECRET}\n", encoding="utf-8")
    assert _blocks(_run(f'gh issue comment 8 --body-file {notes}', sandbox))
    clean = tmp_path / "clean.md"
    clean.write_text("основание: проверено на рабочем проекте\n", encoding="utf-8")
    assert not _blocks(_run(f'gh release create v1 --notes-file {clean}', sandbox))


def test_silent_without_word_list(tmp_path: Path) -> None:
    """Нет списка — нет и стража: у внешнего участника проекта этих слов не существует,
    и мешать ему нечем. Fail-open здесь обязателен, иначе чужой клон не сможет
    опубликовать ни одного комментария."""
    (tmp_path / ".claude").mkdir()
    assert not _blocks(_run(f'gh issue comment 8 --body "{SECRET}"', tmp_path))


def test_silent_on_empty_word_list(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "private-words.txt").write_text("# только комментарий\n\n",
                                                            encoding="utf-8")
    assert not _blocks(_run(f'gh issue comment 8 --body "{SECRET}"', tmp_path))


def test_matching_is_case_insensitive_and_substring(sandbox: Path) -> None:
    """Корень обязан ловить производные (`имя` → `ИМЯ_001`): утекает не точное слово,
    а узнаваемый корень."""
    assert _blocks(_run(f'gh issue comment 8 --body "{SECRET.upper()}_001"', sandbox))
    assert _blocks(_run('gh issue comment 8 --body "secondword-2026"', sandbox))


def test_fail_open_on_broken_input(sandbox: Path) -> None:
    p = subprocess.run(["bash", str(HOOK)], input="не json вовсе", capture_output=True,
                       text=True, env=dict(os.environ, CLAUDE_PROJECT_DIR=str(sandbox)))
    assert p.returncode == 0 and not p.stderr.strip()


def test_word_list_is_not_versioned() -> None:
    """Файл со списком не имеет права попасть в git: перечисляя то, что нужно скрыть,
    он раскрывает это сам. Проверяем не наличие строки в .gitignore, а РЕЗУЛЬТАТ —
    правило могло быть перекрыто другим шаблоном."""
    p = subprocess.run(["git", "check-ignore", "-q", ".claude/private-words.txt"],
                       cwd=str(ROOT))
    assert p.returncode == 0, "список приватных слов не игнорируется git"


def test_prepush_blocks_and_passes(tmp_path: Path) -> None:
    """Второй слой: то, чего Bash-страж видеть не может — содержимое файлов и сообщения
    коммитов. Проверяем обе половины: блок с текстом и молчаливый пропуск."""
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for args in (["config", "user.email", "t@t"], ["config", "user.name", "t"],
                 ["config", "core.hooksPath", ".githooks"]):
        subprocess.run(["git", "-C", str(repo)] + args, check=True)
    (repo / ".githooks").mkdir()
    (repo / ".githooks" / "pre-push").write_text(PREPUSH.read_text(encoding="utf-8"),
                                                 encoding="utf-8")
    (repo / ".githooks" / "pre-push").chmod(0o755)
    (repo / "claude_memory").mkdir()
    (repo / "claude_memory" / "__init__.py").write_text('__version__ = "9.9.9"\n',
                                                        encoding="utf-8")
    (repo / ".claude" / "private-words.txt").write_text(f"{SECRET}\n", encoding="utf-8")
    (repo / "readme.md").write_text("чисто\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "первый"], check=True)

    def run_hook():
        return subprocess.run(["bash", str(repo / ".githooks" / "pre-push"), "origin", "url"],
                              cwd=str(repo), capture_output=True, text=True)

    clean = run_hook()
    assert clean.returncode == 0, f"чистый репозиторий обязан проходить: {clean.stderr[:200]}"

    (repo / "readme.md").write_text(f"внутри {SECRET}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "правка"], check=True)
    dirty = run_hook()
    assert dirty.returncode == 1, "приватное слово в файле обязано останавливать push"
    assert "private-words" in dirty.stderr and "readme.md" in dirty.stderr


# ── Шаблоны: ключи, почта, телефон (то, что списком слов не выражается) ─────
# Ключ каждый раз разный — перечислить нельзя, узнаётся формат. Шаблоны живут в коде:
# формат токена публичен, скрывать в нём нечего, а версионировать полезно.

@pytest.mark.parametrize("secret", [
    "ghp_" + "a" * 36,                                  # токен GitHub
    "github_pat_" + "b" * 45,                           # fine-grained
    "sk-ant-" + "c" * 30,                               # ключ Anthropic
    "sk-" + "d" * 32,                                   # ключ OpenAI
    "AKIA" + "E" * 16,                                  # ключ AWS
    "xoxb-1234567890-abcdefghij",                       # токен Slack
    "-----BEGIN RSA PRIVATE KEY-----",                  # приватный ключ
    "человек@почта.рф".replace("человек", "user1"),     # адрес почты
    "+7 916 123 45 67",                                 # телефон
])
def test_blocks_secrets_by_pattern(sandbox: Path, secret: str) -> None:
    p = _run(f'gh issue comment 8 --body "контекст {secret} конец"', sandbox)
    assert _blocks(p)


def test_secret_is_truncated_in_the_message(sandbox: Path) -> None:
    """Полный секрет в тексте ошибки — это ещё одна его копия, теперь в журнале сессии."""
    token = "ghp_" + "z" * 36
    p = _run(f'gh issue comment 8 --body "{token}"', sandbox)
    assert p.returncode == 2
    assert token not in p.stderr, "страж не имеет права печатать секрет целиком"
    assert "ghp_zzzzzz" in p.stderr and "…" in p.stderr


@pytest.mark.parametrize("text", [
    "noreply@github.com",                     # намеренные адреса не прячут
    "user@example.com",
    "someone@anthropic.com",
    "версия 0.15.0 собрана 2026-07-18",       # телефонный шаблон не ловит версии и даты
    "коммит aab86dd80f1c2b3a4d5e6f70",        # и хеши
    "порт 8080, таймаут 14400",
    "sk-",                                    # обрывки без тела не секрет
    "ghp_short",
])
def test_patterns_do_not_fire_on_safe_text(sandbox: Path, text: str) -> None:
    assert not _blocks(_run(f'gh issue comment 8 --body "{text}"', sandbox))


def test_patterns_work_without_word_list(tmp_path: Path) -> None:
    """Шаблоны в КОДЕ, поэтому обязаны работать и там, где приватного списка нет вовсе —
    у внешнего участника проекта ключ не должен уехать в публичный комментарий."""
    (tmp_path / ".claude").mkdir()
    assert _blocks(_run(f'gh issue comment 8 --body "{"ghp_" + "q" * 36}"', tmp_path))


@pytest.mark.parametrize("text", [
    # Найдено замером на двух живых проектах: 57 и 129 ложных срабатываний соответственно.
    # Страж, ругающийся на код и на файл блокировок, снимается за неделю вместе с пользой.
    "@pytest.mark.skip(reason='x')",
    "@router.patch('/api/v1/claims')",
    "@app.get('/health')",
    "https://registry.npmjs.org/@astrojs/compiler/-/compiler-0.3.1.tgz",
    "https://unpkg.com/react@18.3.1/umd/react.production.min.js",
])
def test_mail_pattern_ignores_decorators_and_package_urls(sandbox: Path, text: str) -> None:
    assert not _blocks(_run(f'gh issue comment 8 --body "{text}"', sandbox))


@pytest.mark.parametrize("text", [
    "пиши на ivan.petrov@mail.ru",
    "адрес a1@b2.рф в тексте",
])
def test_mail_pattern_still_catches_real_addresses(sandbox: Path, text: str) -> None:
    """Отсев декораторов не имеет права ослабить главное — настоящий адрес."""
    assert _blocks(_run(f'gh issue comment 8 --body "{text}"', sandbox))


def test_project_allow_list_suppresses_known_safe_addresses(tmp_path: Path) -> None:
    """У проекта с публичным контактным адресом страж без исключений ругался бы на каждый
    документ, где этот адрес упомянут, — и был бы снят. Исключения проектные, поэтому
    лежат рядом со списком слов и так же вне git."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "private-words.txt").write_text("несуществующееслово\n", encoding="utf-8")
    cmd = 'gh issue comment 8 --body "пиши на hello@ourcompany.ru"'
    assert _blocks(_run(cmd, tmp_path)), "без исключений адрес обязан ловиться"
    (tmp_path / ".claude" / "private-words-allow.txt").write_text(
        "# наши публичные адреса\nhello@ourcompany.ru\n", encoding="utf-8")
    assert not _blocks(_run(cmd, tmp_path)), "с исключением — молчит"
    # исключение не ослабляет остального
    assert _blocks(_run('gh issue comment 8 --body "ivan@other.ru"', tmp_path))
