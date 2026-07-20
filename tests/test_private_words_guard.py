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
import time
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


def _run(command: str, project_dir: Path,
         extra_env: dict = None) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir), **(extra_env or {}))
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
    """Публикующий канал со словом из списка обязан быть остановлен.

    Проверяется ФАКТ остановки, а не её причина: с переходом на белый список (#18)
    команда с цепочкой или подстановкой блокируется раньше — по непроверяемости, —
    и текст объяснения у неё другой. Требовать здесь конкретную формулировку значило
    бы сделать тест придирчивее контракта: наружу в обоих случаях не ушло ничего.
    """
    p = _run(template.format(w=SECRET), sandbox)
    assert p.returncode == 2 and p.stderr.strip(), (
        f"публикация не остановлена: код={p.returncode}")


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

    def run_hook(stdin_text=""):
        return subprocess.run(["bash", str(repo / ".githooks" / "pre-push"), "origin", "url"],
                              input=stdin_text, cwd=str(repo), capture_output=True, text=True)

    def sha(ref="HEAD"):
        return subprocess.run(["git", "-C", str(repo), "rev-parse", ref],
                              capture_output=True, text=True).stdout.strip()

    def push_input(base="0" * 40):
        """Хуку положено получать список ссылок на стандартный ввод — так его зовёт git.

        Прежняя редакция звала хук БЕЗ ввода, и он проверял рабочее дерево вместо
        уходящих коммитов. На такой настройке зелёными оставались оба дефекта:
        и посторонний выход по отсутствию файла пакета, и слепота к истории.
        """
        return f"refs/heads/main {sha()} refs/heads/main {base}\n"

    base_sha = sha()
    clean = run_hook(push_input(base_sha))
    assert clean.returncode == 0, f"чистый репозиторий обязан проходить: {clean.stderr[:200]}"

    (repo / "readme.md").write_text(f"внутри {SECRET}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "правка"], check=True)
    dirty = run_hook(push_input(base_sha))
    assert dirty.returncode == 1, "приватное слово в уходящем коммите обязано останавливать push"
    assert "private-words" in dirty.stderr


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


# ── Дефекты, найденные замером ВРЕМЕНИ, а не чтением кода (заявка #15) ──────────
# ОБЩИЙ КОНТЕКСТ, без которого эти тесты выглядят придиркой к скорости. ЗАМЕРЕНО:
# превышение таймаута хука PreToolUse ПРОПУСКАЕТ вызов, а не блокирует его (контрольный
# опыт: хук с кодом 2 вызов заблокировал; хук, спящий дольше таймаута, — вызов
# выполнился, побочный эффект состоялся). Документация Claude Code об этом исходе не
# говорит вовсе, поэтому здесь замер, а не ссылка.
#
# Следствие: любой способ ЗАМЕДЛИТЬ стража есть способ его ОБОЙТИ, причём молча — ни
# блокировки, ни ошибки в журнале не остаётся. Отсюда правило набора: проверки стража
# МЕРЯЮТ ВРЕМЯ, а не только исход, и на входах в десятки килобайт. Прежние тесты все
# четыре дефекта пропускали, потому что на коротком входе поведение было правильным.
#
# Превентивные блоки проверяются НАПРЯМУЮ (код 2 + свой текст), а не через `_blocks`:
# у того в тексте зашита формулировка про приватный список, а здесь исход другой —
# «проверить не удалось», и смешивать их значило бы сказать человеку неправду.

def _timed(command: str, project_dir: Path, extra_env: dict = None) -> tuple:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir), **(extra_env or {}))
    started = time.monotonic()
    p = subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True,
                       text=True, env=env, timeout=60)
    return p, time.monotonic() - started


def test_long_single_line_body_does_not_stall_the_guard(sandbox: Path) -> None:
    """Квадратичный откат в образце адреса почты = обход стража.

    Замер до починки: 5 КБ — 0,15 с, 20 КБ — 2,31 с, 40 КБ — 9,25 с (вчетверо на каждое
    удвоение). Подушка набирается БЕЗ злого умысла: список идентификаторов через запятую
    или JSON в одну строку. Перенос строки откат рвёт, поэтому страж отказывал выборочно
    и незаметно. Порог 3 с различает старое поведение и новое с большим запасом."""
    pad = "a-" * 20480                        # 40 КБ одной строкой, ни одного `@`
    p, secs = _timed(f'gh issue create --title x --body "{SECRET} {pad}"', sandbox)
    assert p.returncode == 2 and p.stderr.strip(), "приватное слово обязано ловиться"
    assert secs < 3, f"разбор занял {secs:.1f} с — на живом таймауте это молчаливый обход"


def test_clean_long_body_is_fast_too(sandbox: Path) -> None:
    """Тот же замер на ЧИСТОМ тексте: страж обязан не только блокировать быстро, но и
    ПРОПУСКАТЬ быстро — иначе он мешает работе на каждом длинном сообщении."""
    p, secs = _timed(f'gh issue create --title x --body "{"a-" * 20480}"', sandbox)
    assert p.returncode == 0 and not p.stderr.strip()
    assert secs < 3, f"пропуск занял {secs:.1f} с"


def test_body_file_that_is_not_a_regular_file_blocks(sandbox: Path, tmp_path: Path) -> None:
    """Именованный канал как файл тела: `open()` висел бы вечно, и хук умирал бы по
    таймауту — то есть команда уходила бы непроверенной (замер: не завершился за 12 с).
    Тип проверяется ДО открытия, поэтому исход мгновенный и БЛОКИРУЮЩИЙ: содержимое не
    прочли → не знаем, есть ли там запретное."""
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    p, secs = _timed(f"gh issue create --title x --body-file {fifo}", sandbox)
    assert p.returncode == 2, "непрочитанный файл тела обязан блокировать, а не пропускаться"
    assert "проверить не удалось" in p.stderr
    assert secs < 5, f"страж завис на {secs:.1f} с — это и есть обход по таймауту"


def test_oversized_body_file_blocks(sandbox: Path, tmp_path: Path) -> None:
    big = tmp_path / "big.md"
    big.write_text("a" * (1024 * 1024 + 10), encoding="utf-8")
    p, _ = _timed(f"gh issue create --title x --body-file {big}", sandbox)
    assert p.returncode == 2 and "проверить не удалось" in p.stderr


def test_missing_body_file_is_blocked(sandbox: Path, tmp_path: Path) -> None:
    """ГРАНИЦА ПЕРЕПИСАНА ОСОЗНАННО (#18, 2026-07-20).

    Прежняя редакция закрепляла пропуск с доводом «файла нет — пусть решает `gh`,
    он сам упадёт». Довод неполон: мы не знаем, чего нет — файла или прав на него, —
    а между проверкой и запуском команды файл может появиться. Через эту же ветку
    проходила подстановка процесса: пути `<(…)` не существует, `os.stat` падал, страж
    пропускал, а команда прекрасно работала и публиковала.

    Цена новой строгости почти нулевая: команда с несуществующим файлом всё равно не
    сработает, поэтому блокировка не отнимает у человека ничего, кроме одного
    внятного сообщения вместо невнятной ошибки `gh`.
    """
    p = _run(f"gh issue create --title x --body-file {tmp_path / 'нет-такого.md'}", sandbox)
    assert p.returncode == 2 and "проверить не удалось" in p.stderr


def test_non_utf8_word_list_does_not_disable_the_guard(tmp_path: Path) -> None:
    """Один байт не в UTF-8 в списке слов отключал стража ЦЕЛИКОМ.

    Список правят руками, и одна вставка из cp1251 роняла процесс UnicodeDecodeError ещё
    ДО образцов ключей — переставало работать и то, что от списка вообще не зависит.
    Замер до починки: код 1 и traceback вместо кода 2, токен НЕ заблокирован. Код 1
    блокировкой не является, поэтому со стороны это выглядело как обычная работа:
    защиты нет, но и внятной жалобы тоже."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "private-words.txt").write_bytes(
        "вымышленноеслово\n".encode("cp1251"))
    p = _run(f'gh issue create --title x --body "ghp_{"A" * 40}"', tmp_path)
    assert p.returncode == 2, f"страж умер вместо блокировки: код={p.returncode}"
    assert "приватного списка" in p.stderr


def test_non_utf8_allow_list_does_not_disable_the_guard(sandbox: Path) -> None:
    """Тот же дефект во ВТОРОМ чтении файла — асимметрию легко пропустить глазами."""
    (sandbox / ".claude" / "private-words-allow.txt").write_bytes(
        "почта@пример.рф\n".encode("cp1251"))
    assert _blocks(_run(f'gh issue comment 8 --body "{SECRET}"', sandbox))


@pytest.mark.parametrize("flag", ["-F", "--input", "--file", "--body-file", "--notes-file",
                                  "--field"])
def test_file_flag_forms_are_all_inspected(sandbox: Path, tmp_path: Path, flag: str) -> None:
    """Короткие формы флага не досматривались — обход одной буквой.

    Шапка файла заявляет, что закрыт «тот путь, которым удобнее всего отправить длинный
    текст». Короткая форма ровно так же удобна, а `--input` при этом УЖЕ числился каналом
    публикации в самом же PUBLISH: канал признан публикующим, но содержимое не читалось.
    Замер до починки: `-F`, `--input`, `--file` давали код 0."""
    dirty = tmp_path / "dirty.md"
    dirty.write_text(f"текст с {SECRET} внутри\n", encoding="utf-8")
    assert _blocks(_run(f"gh issue create --title x {flag} {dirty}", sandbox))


def test_key_at_file_form_is_inspected(sandbox: Path, tmp_path: Path) -> None:
    """Форма `-F key=@file`: путь лежит после `=@`, а не сразу за флагом."""
    dirty = tmp_path / "dirty.md"
    dirty.write_text(f"текст с {SECRET} внутри\n", encoding="utf-8")
    assert _blocks(_run(f"gh api repos/o/r/issues -X POST -F body=@{dirty}", sandbox))


def test_attached_shorthand_value_is_inspected(sandbox: Path, tmp_path: Path) -> None:
    """ПРИЛИПШЕЕ значение краткого флага: `-Fфайл` и `-Fключ=@файл` без пробела.

    Стандартная краткая запись, `gh` принимает её везде. Найдено ревью diff и замерено:
    первая правка требовала разделитель и пропускала обе формы с кодом 0 — то есть
    закрывала обход одной буквой и тут же оставляла обход одним пробелом."""
    dirty = tmp_path / "dirty.md"
    dirty.write_text(f"текст с {SECRET} внутри\n", encoding="utf-8")
    assert _blocks(_run(f"gh issue create --title x -F{dirty}", sandbox))
    assert _blocks(_run(f"gh api repos/o/r/issues -X POST -Fbody=@{dirty}", sandbox))
    # Форма `-F=файл`: pflag съедает один знак равенства после краткого флага, и `gh` её
    # принимает. Проверяется вместе с прилипшей, потому что дыра между ними ПЕРЕЕЗЖАЛА:
    # первая правка ловила `-F=`, но не `-Fфайл`, вторая — ровно наоборот.
    assert _blocks(_run(f"gh issue create --title x -F={dirty}", sandbox))
    assert _blocks(_run(f"gh api repos/o/r/issues -X POST -F=body=@{dirty}", sandbox))


def test_path_containing_equals_sign_is_still_read(sandbox: Path, tmp_path: Path) -> None:
    """РЕГРЕСС-ЗАМОК: обычный путь со знаком равенства обязан читаться.

    Разбор формы `ключ=@файл` сперва применялся ко ВСЕМ флагам, и путь вида `dir=ty.md`
    переставал читаться — страж становился СЛАБЕЕ, чем был ДО починки. Пойман ревью и
    подтверждён замером против прежней версии. Разбор теперь только у `-F`/`--field`."""
    dirty = tmp_path / "dir=ty.md"
    dirty.write_text(f"текст с {SECRET} внутри\n", encoding="utf-8")
    assert _blocks(_run(f"gh issue create --title x --body-file {dirty}", sandbox))


@pytest.mark.parametrize("env", [
    {"PRIVATE_WORDS_GUARD_DEADLINE": "abc"},                   # не число
    {"PRIVATE_WORDS_GUARD_DEADLINE": "99999999999999999999"},  # signal.alarm: OverflowError
    {"PRIVATE_WORDS_GUARD_DEADLINE": "-5"},                    # отрицательное
    {"PRIVATE_WORDS_GUARD_TEST_SLOW": "abc"},                  # не число
])
def test_garbage_in_env_knobs_does_not_kill_the_guard(sandbox: Path, env: dict) -> None:
    """Мусор в переменной окружения не имеет права уронить процесс.

    Непойманное исключение — это код 1, а код 1 НЕ БЛОКИРУЕТ. То есть опечатка в ручке
    молча снимала бы защиту целиком: ровно тот дефект, что чинится выше про не-UTF8
    в списке слов, воспроизведённый внутри его же починки. Замерено: все четыре входа
    давали код 1 и пропускали приватное слово."""
    p = _run(f'gh issue create --title x --body "{SECRET}"', sandbox, env)
    assert p.returncode == 2, f"страж умер вместо блокировки: код={p.returncode}"
    assert "приватного списка" in p.stderr


def test_negative_deadline_does_not_disable_the_watchdog(sandbox: Path) -> None:
    """ОТДЕЛЬНО от теста выше, и это не дублирование.

    Тот проверяет, что процесс не падает, но приватное слово в нём ловится СПИСКОМ СЛОВ —
    значит ветка таймера там не исполняется вовсе, и он остался бы зелёным при полностью
    выключенном таймере. Ревю поймало ровно это: тест давал ложную уверенность.

    Здесь текст ЧИСТЫЙ, поэтому сработать может только таймер. Замер до правки: «abc»
    давало откат к умолчанию и блокировку, а «-5» молча снимало таймер — две одинаково
    вероятные опечатки с ПРОТИВОПОЛОЖНЫМ исходом, защита и её отсутствие."""
    p, secs = _timed('gh issue create --title x --body "безобидный текст"', sandbox,
                     {"PRIVATE_WORDS_GUARD_DEADLINE": "-5",
                      "PRIVATE_WORDS_GUARD_TEST_SLOW": "8"})
    assert p.returncode == 2, "опечатка в дедлайне выключила таймер — молчаливый обход"
    assert "за отведённое время" in p.stderr
    assert secs < 8, f"таймер ответил к {secs:.1f} с — позже хук убьёт среда"


def test_explicit_zero_disables_the_watchdog(sandbox: Path) -> None:
    """ЛАЗЕЙКА, названная вслух: ТОЧНОЕ «0» выключает таймер осознанно.

    Отличается от мусора именно точностью написания — иначе опечатка и намерение
    неразличимы. Тест на ПРОПУСК: без него «0» мог бы незаметно перестать работать.

    СОН ВЫШЕ УМОЛЧАНИЯ (6 > 5) — не придирка, а условие осмысленности. При сне 1 с тест
    зеленел бы и при выключенной проверке: 1 с меньше любого возможного дедлайна, поэтому
    код 0 получался бы и когда «0» ничего не выключает. Замерено: `00` при сне 1 с
    неотличим от `0`, а при сне 6 с расходится (код 2 против 0). Ровно та ложная
    уверенность, которую этот же выпуск чинит в соседнем тесте."""
    p, secs = _timed('gh issue create --title x --body "безобидный текст"', sandbox,
                     {"PRIVATE_WORDS_GUARD_DEADLINE": "0",
                      "PRIVATE_WORDS_GUARD_TEST_SLOW": "6"})
    assert p.returncode == 0 and not p.stderr.strip()
    assert secs > 5, "сон не дошёл до умолчания — тест зеленел бы и без выключения таймера"


def test_huge_deadline_is_capped_below_consumer_hook_timeout(sandbox: Path) -> None:
    """Ручка не должна позволять задрать дедлайн ВЫШЕ таймаута хука: тогда исход снова
    определяет среда, то есть пропуск. Закрывает сразу два замера: дедлайн 30 при сне 9
    давал код 0, а огромное число роняло `signal.alarm` с OverflowError — то есть код 1,
    тоже пропуск. Верхняя граница чинит оба разом."""
    p, secs = _timed('gh issue create --title x --body "безобидный текст"', sandbox,
                     {"PRIVATE_WORDS_GUARD_DEADLINE": "99999999999999999999",
                      "PRIVATE_WORDS_GUARD_TEST_SLOW": "12"})
    assert p.returncode == 2, "дедлайн выше таймаута хука = пропуск, а не блокировка"
    assert "за отведённое время" in p.stderr
    assert secs < 11, f"таймер ответил к {secs:.1f} с — на таймауте 10 с это пропуск"


def test_inline_key_value_form_is_not_treated_as_a_path(sandbox: Path) -> None:
    """ТЕСТ НА ПРОПУСК. `-F key=value` без `@` — значение инлайновое, файла нет.
    Оно уже внутри команды, значит уже проверено; выдумывать здесь файл незачем."""
    p = _run("gh api repos/o/r/issues -X POST -F title=обычныйтекст", sandbox)
    assert p.returncode == 0 and not p.stderr.strip()


def test_watchdog_blocks_when_scan_exceeds_deadline(sandbox: Path) -> None:
    """Сторожевой таймер: не уложились в срок → БЛОКИРОВКА, а не пропуск.

    Единственное отступление от fail-open, и оно осознанное: исход обязан быть выбором
    СТРАЖА, а не среды. Проверяется через тестовую закладку — после ограничения образца
    почты сверху реалистичным входом стража уже не замедлить (40 КБ — 0,08 с), а
    непроверенный таймер выглядит точно так же, как работающий, пока не случится
    настоящий откат."""
    p, secs = _timed('gh issue create --title x --body "безобидный текст"', sandbox,
                     {"PRIVATE_WORDS_GUARD_TEST_SLOW": "4",
                      "PRIVATE_WORDS_GUARD_DEADLINE": "1"})
    assert p.returncode == 2, "таймер не сработал — на живом таймауте это молчаливый обход"
    assert "за отведённое время" in p.stderr
    assert secs < 3, f"таймер сработал только к {secs:.1f} с — позже хук убьёт среда"


def test_watchdog_does_not_fire_on_normal_input(sandbox: Path) -> None:
    """ТЕСТ НА ПРОПУСК для таймера: ложно блокирующего стража снимают целиком вместе
    с пользой, поэтому «не срабатывает, когда не должен» проверяется отдельно."""
    p, _ = _timed('gh issue create --title x --body "безобидный текст"', sandbox,
                  {"PRIVATE_WORDS_GUARD_TEST_SLOW": "0.2"})
    assert p.returncode == 0 and not p.stderr.strip()


def test_default_deadline_is_below_consumer_hook_timeout(sandbox: Path) -> None:
    """ИНВАРИАНТ, без которого таймер бесполезен: дедлайн ПО УМОЛЧАНИЮ обязан быть строго
    меньше таймаута хука. У обоих потребителей он задан явно и равен 10 с — берём меньшее
    из известных. Не уложись страж в него, исход снова определяла бы среда, то есть
    пропуск: ровно то, что здесь чинится.

    Судим по ПОВЕДЕНИЮ, а не по тексту файла: прежняя версия этого теста искала число
    регулярным выражением и умерла молча при первом же переносе дедлайна в функцию —
    то есть проверка исчезла бы, а набор остался бы зелёным."""
    p, secs = _timed('gh issue create --title x --body "безобидный текст"', sandbox,
                     {"PRIVATE_WORDS_GUARD_TEST_SLOW": "9"})   # дольше умолчания, короче 10 с
    assert p.returncode == 2, "таймер по умолчанию не сработал"
    assert "за отведённое время" in p.stderr
    assert secs < 9, f"страж ответил только к {secs:.1f} с — на таймауте 10 с это пропуск"
