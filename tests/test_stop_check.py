"""Тесты напоминания про уроки при завершении (Stop)."""
from __future__ import annotations

import os
import pytest
import subprocess
from dataclasses import replace
from pathlib import Path

from claude_memory import stop_check as SC
from conftest import write_lesson, RU_EN_CLOSE_PATTERN

AGE = 14400


def test_decide_table() -> None:
    # коммит свежий и новее урока → блок
    assert SC.decide(commit_ts=1000, feedback_ts=500, now_ts=1100, age_limit=AGE) is True
    # урок новее коммита → не блок
    assert SC.decide(commit_ts=1000, feedback_ts=2000, now_ts=1100, age_limit=AGE) is False
    # коммит слишком старый → не блок
    assert SC.decide(commit_ts=1000, feedback_ts=500, now_ts=1000 + AGE + 1, age_limit=AGE) is False
    # нет коммита → не блок
    assert SC.decide(commit_ts=0, feedback_ts=0, now_ts=100, age_limit=AGE) is False


def test_newest_lesson_mtime(cfg) -> None:
    assert SC.newest_lesson_mtime(cfg) == 0.0  # пусто
    f = write_lesson(cfg.memory_dir, "feedback_a.md", description="d")
    os.utime(f, (1000, 1000))
    assert SC.newest_lesson_mtime(cfg) == 1000.0


# ── АНТИ-ВОСКРЕШЕНИЕ бага «страж требует урок, который сам же не видит» ─────────
# Имена файлов уроков пишет авто-память Claude Code, а не движок. До 0.10.0 страж искал
# по маске `f"{prefix}_*.md"`, и уроки, названные иначе, для него не существовали:
# требование стража было НЕЧЕМ удовлетворить.

def test_newest_lesson_mtime_sees_lessons_without_prefix(cfg) -> None:
    """Каталог ТОЛЬКО из уроков без приставки. Раньше здесь был ровно 0.0 при непустой
    папке — «уроков нет» → блок на каждый свежий коммит, снять нечем."""
    f = write_lesson(cfg.memory_dir, "kebab-case-lesson.md", name="k", description="d",
                     type="project")
    os.utime(f, (2000, 2000))
    assert SC.newest_lesson_mtime(cfg) == 2000.0


def test_newest_lesson_mtime_sees_user_type(cfg) -> None:
    """Тип `user` есть в официальном словаре Claude Code, но отсутствовал в дефолтных
    lesson_prefixes → такой урок был невидим стражу в ЛЮБОМ проекте на дефолтах."""
    f = write_lesson(cfg.memory_dir, "user_profile.md", name="u", description="d", type="user")
    os.utime(f, (3000, 3000))
    assert SC.newest_lesson_mtime(cfg) == 3000.0


def test_newest_lesson_mtime_ignores_core_catalog_private(cfg) -> None:
    """Ядро/указатель/приватные — не уроки: они меняются сами (указатель пересобирается
    движком) и молча снимали бы блок за человека."""
    for base in (cfg.core_file, cfg.catalog_file, "_private.md"):
        f = write_lesson(cfg.memory_dir, base, name="x", description="d")
        os.utime(f, (9000, 9000))
    assert SC.newest_lesson_mtime(cfg) == 0.0


def test_kebab_lesson_after_commit_releases_the_block(cfg) -> None:
    """Сквозной сценарий бага: свежий коммит → блок; записали урок в стиле проекта →
    блок обязан сняться. Раньше не снимался никогда."""
    commit_ts, now = 1_000_000, 1_000_060
    assert SC.decide(commit_ts, SC.newest_lesson_mtime(cfg), now, AGE) is True

    f = write_lesson(cfg.memory_dir, "pravovaya-ramka-rkn.md", name="p", description="d",
                     type="project")
    os.utime(f, (commit_ts + 10, commit_ts + 10))
    assert SC.decide(commit_ts, SC.newest_lesson_mtime(cfg), now, AGE) is False


def test_task_lesson_recorded_in_kebab_lesson(cfg) -> None:
    """Привратник закрытия задачи страдал тем же: урок про задачу, названный не по
    приставке, он не находил — и требовал записать уже записанное."""
    write_lesson(cfg.memory_dir, "infrastruktura-vps.md", name="i", description="d",
                 type="project", body="Разобрано в задаче #42 — вывод такой.")
    assert SC.task_lesson_recorded(cfg, "42") is True
    assert SC.task_lesson_recorded(cfg, "43") is False


def test_disabled_returns_none(cfg) -> None:
    cfg2 = replace(cfg, stop_lessons_enabled=False)
    assert SC.should_remind(cfg2, cfg.project_root, now_ts=10_000_000_000) is None


def test_non_git_dir_returns_none(cfg, tmp_path) -> None:
    assert SC.last_commit_ts(str(tmp_path / "nope")) == 0
    assert SC.should_remind(cfg, str(tmp_path), now_ts=10_000_000_000) is None  # нет git → нет блока


def test_real_git_commit_triggers(cfg, tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=repo, check=True, env=env)
    # урок старее коммита (mtime в прошлом)
    f = write_lesson(cfg.memory_dir, "feedback_a.md", description="d")
    os.utime(f, (1000, 1000))
    commit_ts = SC.last_commit_ts(str(repo))
    assert commit_ts > 0
    msg = SC.should_remind(cfg, str(repo), now_ts=commit_ts + 10)
    assert msg and "stop-lessons" in msg


# ── привратник закрытия задачи ───────────────────────────────────────────────

def test_extract_closed_task_numeric_and_slug(cfg) -> None:
    p = cfg.task_close_pattern
    assert SC.extract_closed_task("fix: Closes #58", p) == "58"
    assert SC.extract_closed_task("docs: Fixes #memory-lib-cutover", p) == "memory-lib-cutover"
    assert SC.extract_closed_task("just a normal commit", p) is None


@pytest.mark.parametrize("word", [
    "close", "closes", "closed", "Close", "CLOSES",
    "fix", "fixes", "fixed", "Fixes",
    "resolve", "resolves", "resolved", "Resolves",
])
def test_extract_closed_task_all_github_keywords(cfg, word: str) -> None:
    """Дефолт обязан зеркалить ВСЕ девять слов-закрытий GitHub, а не подмножество.

    Семья `resolve` отсутствовала до 0.10.0: треть законных форм привратник молча не
    узнавал, и «не узнал» было неотличимо от «задачу не закрывали». Список форм здесь
    сверяется с ИСТОЧНИКОМ (докой GitHub), а не с интуицией — тот же класс дефекта уже
    ловили на русской фразе закрытия."""
    assert SC.extract_closed_task(f"feat: {word} #42", cfg.task_close_pattern) == "42"


def test_extract_closed_task_body_based_closure(cfg) -> None:
    """GitHub распознаёт закрытие и в ТЕЛЕ коммита — движок читает `%B`, не `%s`."""
    assert SC.extract_closed_task("fix(config): описка\n\nResolves #7", cfg.task_close_pattern) == "7"


def test_extract_closed_task_bare_mention_is_not_closure(cfg) -> None:
    """Голое упоминание задачи — не закрытие: рядовой стиль `тема (#5)` не должен
    блокировать Stop зря."""
    p = cfg.task_close_pattern
    assert SC.extract_closed_task("рефакторинг (#5)", p) is None
    assert SC.extract_closed_task("правки по #5 и #6", p) is None


def test_extract_closed_task_hyphen_prefix_not_closure(cfg) -> None:
    # RED-TEAM (#stale-reconcile-ru-closure): дефис перед словом закрытия — НЕ закрытие.
    # Прежняя граница `\b` срабатывала и после дефиса → `prefixed-closes #10` ложно читался
    # как закрытие #10. Негативный lookbehind `(?<![\w-])` в дефолте это убирает.
    p = cfg.task_close_pattern
    assert SC.extract_closed_task("prefixed-closes #10", p) is None
    assert SC.extract_closed_task("auto-closes #10", p) is None
    assert SC.extract_closed_task("v0.7-fixes #42", p) is None
    # контроль: легитимные закрытия по-прежнему распознаются (в начале строки и после слова)
    assert SC.extract_closed_task("fix: Closes #58", p) == "58"
    assert SC.extract_closed_task("Fixes #memory-lib-cutover", p) == "memory-lib-cutover"


def test_extract_closed_task_hyphen_prefix_combined_pattern() -> None:
    # Тот же фикс в комбинированном проектном шаблоне (англ. + рус. ветки): дефис перед
    # англ. словом закрытия НЕ закрытие; рус. ветка и легитимные англ. формы не затронуты.
    p = RU_EN_CLOSE_PATTERN
    assert SC.extract_closed_task("prefixed-closes #10", p) is None
    assert SC.extract_closed_task("auto-closes #10", p) is None
    assert SC.extract_closed_task("fix: Closes #task-9", p) == "task-9"
    assert SC.extract_closed_task("#task-7 закрыта", p) == "task-7"


def test_extract_closed_task_first_nonempty_group() -> None:
    # Многогрупповой проектный шаблон: id в РАЗНЫХ группах по ветке (англ. — группа 1,
    # рус. — группа 2). extract_closed_task берёт первую НЕПУСТУЮ группу, не жёстко группу 1.
    p = RU_EN_CLOSE_PATTERN
    assert SC.extract_closed_task("fix: Closes #task-9", p) == "task-9"            # англ. → группа 1
    assert SC.extract_closed_task(
        "docs(tracker): #audit-2026-06-28-G2 закрыт — A28 DONE", p
    ) == "audit-2026-06-28-G2"                                                     # рус. → группа 2
    assert SC.extract_closed_task("#task-7 закрыта", p) == "task-7"
    assert SC.extract_closed_task("#task-8 закрыто", p) == "task-8"
    assert SC.extract_closed_task("#task-10 закрыты", p) == "task-10"
    assert SC.extract_closed_task("feat: ordinary work, no closure", p) is None
    # фикс-коммит «#id — …» без слова закрытия НЕ распознаётся как закрытие
    assert SC.extract_closed_task(
        "fix(payment): #audit-2026-06-28-G2 — превью без кеша", p
    ) is None


def test_extract_closed_task_russian_branch_is_narrow() -> None:
    # Рус. ветка узкая (`закрыт[аоы]?\b`): отглагольные формы НЕ считаются закрытием.
    # Регресс-замок к red-team: `закры\w*` ловил бы их и давал ложные блок-напоминания.
    p = RU_EN_CLOSE_PATTERN
    assert SC.extract_closed_task("#A28-1 закрытие обсудим позже", p) is None
    assert SC.extract_closed_task("#A28-2 закрытость интерфейса важна", p) is None
    assert SC.extract_closed_task("обсудили, #A28-3 закрывать пока НЕ будем", p) is None
    assert SC.extract_closed_task("#A28-4 закрытый вопрос", p) is None
    assert SC.extract_closed_task("#A28-5 закрывается автоматически при деплое", p) is None


def test_extract_closed_task_no_group_pattern_is_safe() -> None:
    # Шаблон без capture-групп не должен падать (раньше m.group(1) → IndexError) — None.
    assert SC.extract_closed_task("Closes #task-9", r"closes #[\w-]+") is None


def test_task_lesson_recorded_in_lesson_file(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_x.md", description="про #widget-42 и решение")
    assert SC.task_lesson_recorded(cfg, "widget-42") is True
    assert SC.task_lesson_recorded(cfg, "nope-99") is False


def test_task_lesson_recorded_in_archive(cfg) -> None:
    arc = Path(cfg.memory_dir) / "archive"
    arc.mkdir()
    (arc / "precedents-2026-Q2.md").write_text("## 2026-06-17 закрыта #task-7\n", encoding="utf-8")
    assert SC.task_lesson_recorded(cfg, "task-7") is True


def _git_commit(repo: Path, msg: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", msg], cwd=repo, check=True, env=env)


# ── extract_closed_tasks: ВСЕ закрытия коммита, а не первое (заявка #15) ────────
# Прежний `extract_closed_task` на `re.search` брал первое совпадение и остальные терял
# МОЛЧА. Замер по живой истории потребителя: 3 коммита из 144 с >1 совпадением, в двух
# терялась настоящая задача. Молчание было неотличимо от «закрытий не было».

def test_extract_closed_tasks_multiple_on_one_line(cfg) -> None:
    p = cfg.task_close_pattern
    assert SC.extract_closed_tasks("Closes #1, also Closes #2", p) == ["1", "2"]


def test_extract_closed_tasks_multiple_on_separate_lines(cfg) -> None:
    p = cfg.task_close_pattern
    assert SC.extract_closed_tasks("Closes #alpha\nCloses #beta", p) == ["alpha", "beta"]


def test_extract_closed_tasks_dedupes_preserving_order(cfg) -> None:
    # Один id, записанный дважды в разных формах, — это ОДНО закрытие, не два.
    # Замер по живой истории: таких коммитов 8 из 10 многосовпадальных, и требовать
    # по ним урок дважды значило бы шуметь на ровном месте.
    p = RU_EN_CLOSE_PATTERN
    assert SC.extract_closed_tasks("#task-7 закрыт\n\nCloses #task-7", p) == ["task-7"]


def test_extract_closed_tasks_shadowed_real_closure_survives(cfg) -> None:
    """ГЛАВНЫЙ сценарий заявки #15: проза о ФОРМЕ закрытия выше НАСТОЯЩЕГО закрытия.

    Порядок самый естественный — конвенция обоих потребителей кладёт закрывающую фразу
    в КОНЕЦ сообщения, а рассказ о правке в тело выше. Раньше извлекался фантом `123`,
    а настоящая задача терялась: ложное срабатывание порождало ложный ПРОПУСК."""
    p = RU_EN_CLOSE_PATTERN
    text = ("Сверка показала, что шаблон расширен формами Closes: #123 и resolved.\n\n"
            "#tema-zaslona закрыт (issue #77)")
    got = SC.extract_closed_tasks(text, p)
    assert "tema-zaslona" in got, "настоящее закрытие потеряно — тот самый баг"
    assert got[0] == "123", "контракт надмножества: первый элемент = прежнее поведение"


def test_extract_closed_tasks_is_superset_of_singular(cfg) -> None:
    """КОНТРАКТ НАДМНОЖЕСТВА: ничего ловящегося сегодня не потеряется.

    Свойство проверяется на батарее входов из соседних тестов, а не на одном: именно
    оно разрешает переключить `closure_reminder` без риска регресса."""
    for p in (cfg.task_close_pattern, RU_EN_CLOSE_PATTERN):
        for text in ("fix: Closes #58", "docs: Fixes #memory-lib-cutover",
                     "just a normal commit", "рефакторинг (#5)", "prefixed-closes #10",
                     "#task-7 закрыта", "fix(config): описка\n\nResolves #7",
                     "Closes #1, also Closes #2", ""):
            one = SC.extract_closed_task(text, p)
            many = SC.extract_closed_tasks(text, p)
            if one is None:
                assert many == [], f"одиночная молчит, множественная нет: {text!r}"
            else:
                assert many and many[0] == one, f"расхождение на {text!r}"


def test_extract_closed_tasks_degenerate_inputs(cfg) -> None:
    p = cfg.task_close_pattern
    assert SC.extract_closed_tasks("", p) == []
    assert SC.extract_closed_tasks("Closes #9", "((((") == []          # битый шаблон
    assert SC.extract_closed_tasks("Closes #9", r"closes #[\w-]+") == []  # шаблон без групп


def test_extract_closed_tasks_capped(cfg) -> None:
    # Потолок про читаемость блок-текста, не про безопасность: полотно из тридцати
    # номеров человек не прочтёт, а прочесть он обязан — иначе блок бесполезен.
    p = cfg.task_close_pattern
    text = " ".join(f"Closes #{i}" for i in range(50))
    assert len(SC.extract_closed_tasks(text, p)) == SC.MAX_CLOSED_TASKS


def test_closure_reminder_blocks_on_second_closure(cfg, tmp_path) -> None:
    """Урок есть про ПЕРВОЕ закрытие, но не про второе → всё равно блок, и в тексте второе.

    Раньше здесь был молчаливый None: гейт видел только первый id, находил про него урок
    и успокаивался. Именно так терялись `ttl-session-key-deny` и `#90` в живой истории."""
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "docs: Closes #task-9 и Closes #task-10")
    write_lesson(cfg.memory_dir, "feedback_done.md", description="урок про #task-9")
    msg = SC.closure_reminder(cfg, str(repo))
    assert msg, "второе закрытие потеряно — гейт промолчал"
    assert "task-10" in msg
    assert "task-9" not in msg, "про #task-9 урок есть — требовать его повторно незачем"


def test_closure_reminder_lists_all_missing(cfg, tmp_path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "docs: Closes #task-9 и Closes #task-10")
    msg = SC.closure_reminder(cfg, str(repo))
    assert msg and "task-9" in msg and "task-10" in msg


def test_closure_reminder_passes_when_all_lessons_exist(cfg, tmp_path) -> None:
    """ТЕСТ НА ПРОПУСК — обязательный. Набор из одних блокирующих случаев проходит
    зелёным даже на полностью сломанном страже, поэтому «не блокирует, когда не должен»
    проверяется отдельно (урок «мёртвый страж выглядит как блокирующий»)."""
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "docs: Closes #task-9 и Closes #task-10")
    write_lesson(cfg.memory_dir, "feedback_a.md", description="урок про #task-9")
    write_lesson(cfg.memory_dir, "feedback_b.md", description="урок про #task-10")
    assert SC.closure_reminder(cfg, str(repo)) is None


def test_closure_reminder_shadow_no_longer_silences_real_task(cfg, tmp_path) -> None:
    """Сквозной сценарий тени на уровне гейта, а не экстрактора.

    Про фантом `#123` урок «есть» (он упомянут в чужом уроке), про настоящую задачу —
    нет. Раньше гейт видел только фантом, находил упоминание и МОЛЧАЛ."""
    cfg2 = replace(cfg, task_close_pattern=RU_EN_CLOSE_PATTERN)
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "Сверка: шаблон расширен формами Closes: #123 и resolved.\n\n"
                      "#tema-zaslona закрыт")
    write_lesson(cfg.memory_dir, "feedback_x.md", description="упоминание #123 в другом уроке")
    msg = SC.closure_reminder(cfg2, str(repo))
    assert msg and "tema-zaslona" in msg, "настоящая задача снова потеряна за фантомом"


def test_closure_reminder_blocks_when_no_lesson(cfg, tmp_path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "docs: Closes #task-9")
    msg = SC.closure_reminder(cfg, str(repo))
    assert msg and "task-close-gate" in msg and "task-9" in msg


def test_closure_reminder_passes_when_lesson_exists(cfg, tmp_path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "docs: Closes #task-9")
    write_lesson(cfg.memory_dir, "feedback_done.md", description="урок про #task-9")
    assert SC.closure_reminder(cfg, str(repo)) is None


def test_closure_gate_disabled(cfg, tmp_path) -> None:
    from dataclasses import replace
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "docs: Closes #task-9")
    assert SC.closure_reminder(replace(cfg, task_close_lesson_gate=False), str(repo)) is None


def test_closure_reminder_non_closing_commit_is_none(cfg, tmp_path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "feat: ordinary work, no closure")
    assert SC.closure_reminder(cfg, str(repo)) is None


def test_closure_reminder_detects_closes_in_body(cfg, tmp_path) -> None:
    # `Closes #N` в ТЕЛЕ коммита (не в теме) — тоже закрытие: last_commit_msg использует %B,
    # не %s (регресс-замок к dogfood-багу #memory-stale-lesson-guard).
    repo = tmp_path / "repo"; repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
    # тема БЕЗ Closes, тело — С Closes (второй -m)
    subprocess.run(["git", "commit", "-q", "-m", "feat: фича", "-m", "Closes #task-9"],
                   cwd=repo, check=True, env=env)
    assert "Closes #task-9" in SC.last_commit_msg(str(repo))  # %B вернул тело
    msg = SC.closure_reminder(cfg, str(repo))
    assert msg and "task-9" in msg


def test_closure_reminder_detects_russian_form(cfg, tmp_path) -> None:
    # Рус. «#id закрыт» БЕЗ «Closes» — тоже закрытие (проектный шаблон с двумя ветками).
    # Регресс-замок к реальному пропуску закрытия #audit-2026-06-28-G2 (коммит b2f91b1).
    cfg2 = replace(cfg, task_close_pattern=RU_EN_CLOSE_PATTERN)
    repo = tmp_path / "repo"; repo.mkdir()
    _git_commit(repo, "docs(tracker): #audit-2026-06-28-G2 закрыт — A28 DONE")
    msg = SC.closure_reminder(cfg2, str(repo))
    assert msg and "task-close-gate" in msg and "audit-2026-06-28-G2" in msg
