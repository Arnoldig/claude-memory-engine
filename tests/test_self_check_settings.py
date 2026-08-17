"""Самодиагностика расхождений с настройками хозяина (Claude Code).

Класс дефекта, который тут ловится: движок читает не ту папку, куда пишет авто-память, —
и молчит. Выглядит как «всё хорошо, уроков просто нет», а на деле стражи слепы и Stop
блокирует после каждого коммита. Именно этот сценарий был ПОВЕДЕНИЕМ ПО УМОЛЧАНИЮ до
0.10.0 (установщик ставил `~/.claude/memory`, куда не пишет никто).

Домашний каталог подменяется для КАЖДОГО теста (`_isolate_home`): `_read_settings` всегда
читает `~/.claude/settings.json` слабейшей областью, и без подмены тесты «молчим при битой
настройке» зависели бы от того, что лежит в домашней папке запускающего. См. подробнее
докстринг `tests/test_claude_code_env.py`.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path


from claude_memory import self_check as SC
from conftest import write_lesson


def _settings(root: str, scope: str, data: dict) -> None:
    p = Path(root) / scope
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


# ── (1) авто-память выключена, а стражи включены — настоящий вечный тупик ────────

def test_auto_memory_off_with_gates_on_complains(cfg) -> None:
    _settings(cfg.project_root, ".claude/settings.json", {"autoMemoryEnabled": False})
    issues = SC.settings_issues(cfg)
    assert any("auto-memory is DISABLED" in i for i in issues)


def test_auto_memory_off_with_gates_off_is_silent(cfg) -> None:
    """Уроки писать некому — но и не требует никто. Это связная настройка, не дефект."""
    _settings(cfg.project_root, ".claude/settings.json", {"autoMemoryEnabled": False})
    cfg2 = replace(cfg, stop_lessons_enabled=False, task_close_lesson_gate=False)
    assert SC.settings_issues(cfg2) == []


def test_auto_memory_off_via_env(cfg, monkeypatch) -> None:
    from claude_memory import claude_code_env as E
    monkeypatch.setenv(E.DISABLE_ENV, "1")
    assert any("auto-memory is DISABLED" in i for i in SC.settings_issues(cfg))


# ── (2) явный autoMemoryDirectory ≠ memory_dir ──────────────────────────────────

def test_explicit_mismatch_complains(cfg, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    _settings(cfg.project_root, ".claude/settings.json",
              {"autoMemoryDirectory": str(other)})
    issues = SC.settings_issues(cfg)
    assert any("reading a directory nobody writes to" in i for i in issues)
    assert any(str(other) in i for i in issues)


def test_explicit_match_is_silent(cfg) -> None:
    _settings(cfg.project_root, ".claude/settings.json",
              {"autoMemoryDirectory": cfg.memory_dir})
    assert SC.settings_issues(cfg) == []


def test_explicit_match_via_symlink_is_silent(cfg, tmp_path: Path) -> None:
    """Сравнение через realpath: symlink на ту же папку — не расхождение."""
    link = tmp_path / "link-to-memory"
    link.symlink_to(cfg.memory_dir)
    _settings(cfg.project_root, ".claude/settings.json", {"autoMemoryDirectory": str(link)})
    assert SC.settings_issues(cfg) == []


# ── (3) memory_dir пуст, а рядом непустая папка авто-памяти ─────────────────────

def test_empty_memory_dir_with_lessons_elsewhere_complains(cfg, tmp_path: Path, monkeypatch) -> None:
    """Тот самый сценарий сломанного дефолта установщика: движок смотрит в пустоту,
    а уроки хозяина лежат в другой папке. Догадка подтверждена диском → жалуемся."""
    from claude_memory import claude_code_env as E

    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    auto = Path(E.default_auto_memory_dir(cfg.project_root))
    auto.mkdir(parents=True)
    write_lesson(str(auto), "kebab-lesson.md", name="k", description="d", type="project")

    issues = SC.settings_issues(cfg)   # memory_dir из фикстуры — пустой
    assert any("almost certainly pointed at the wrong directory" in i for i in issues)
    assert any(str(auto) in i for i in issues)


def test_not_complaining_when_neighbour_empty(cfg, tmp_path: Path, monkeypatch) -> None:
    """Соседняя папка пуста → догадка НЕ подтверждена → молчим. Иначе жаловались бы по
    недокументированному правилу слага, то есть гадали вслух."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert SC.settings_issues(cfg) == []


# ── (4) memory_dir НЕПУСТ, а авто-память пишется в другую папку ─────────────────
#
# До 0.12.0 всё это было одним тестом «видит урок → молчим» с обоснованием «возможен
# намеренный корпус». Обоснование неверно при ВКЛЮЧЁННЫХ стражах: уроки создаёт только
# авто-память Claude Code, значит страж требует урок, который в memory_dir не появится
# никогда. Намеренный корпус остался законным — но лишь там, где его никто не требует
# пополнять, и именно эту границу закрепляют два первых теста ниже.


def _diverged(cfg, tmp_path: Path, monkeypatch) -> Path:
    """Разъезд, подтверждённый диском: уроки есть И у движка, И в папке авто-памяти."""
    from claude_memory import claude_code_env as E

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    auto = Path(E.default_auto_memory_dir(cfg.project_root))
    auto.mkdir(parents=True)
    write_lesson(str(auto), "kebab-lesson.md", name="k", description="d", type="project")
    write_lesson(cfg.memory_dir, "my-own.md", name="m", description="d", type="project")
    return auto


def test_divergent_with_gates_off_is_silent(cfg, tmp_path: Path, monkeypatch) -> None:
    """Стражи выключены → отдельный корпус связен: движок тут просто ретривер над
    курируемой папкой, пополнять её никто не обязывает. Жалоба была бы навязчивой И
    неустранимой — замолчать её было бы нечем."""
    _diverged(cfg, tmp_path, monkeypatch)
    cfg2 = replace(cfg, stop_lessons_enabled=False, task_close_lesson_gate=False)

    assert SC.settings_issues(cfg2) == []


def test_divergent_with_gates_on_complains(cfg, tmp_path: Path, monkeypatch) -> None:
    """Стражи включены → состояние неудовлетворимо: новые уроки уходят мимо движка, и
    Stop-страж не удовлетворить ничем. Молчать тут нельзя."""
    auto = _diverged(cfg, tmp_path, monkeypatch)

    issues = SC.settings_issues(cfg)
    assert any("dead tail" in i for i in issues)
    assert any(str(auto) in i and cfg.memory_dir in i for i in issues)


def test_report_says_no_implies_complaint(cfg, tmp_path: Path, monkeypatch) -> None:
    """ИНВАРИАНТ: при включённых стражах И разъезде, ПОДТВЕРЖДЁННОМ диском, невозможно
    состояние «отчёт печатает NO, а жалоб нет». Разные пороги у отчёта и у жалоб —
    намеренные (отчёт отвечает «что настроено»), но односторонне: подтверждённый приговор
    `NO` обязан быть слышен и без просьбы человека. Тест держит именно эту связь, а не текст
    конкретной жалобы, — он переживёт смену формулировок.

    Оговорка «подтверждённом диском» — не хедж, а граница: без неё утверждение ЛОЖНО.
    Когда каталога авто-памяти ещё нет, `report()` печатает `NO` с пометкой «выведено, не
    подтверждено», а жалоб нет намеренно — иначе движок гадал бы вслух по недокументированному
    правилу слага (см. `test_not_complaining_when_neighbour_empty`).
    """
    _diverged(cfg, tmp_path, monkeypatch)

    assert "same directory    : NO" in "\n".join(SC.report(cfg))
    assert SC.settings_issues(cfg) != []


def test_divergent_stays_quiet_when_auto_memory_off(cfg, tmp_path: Path, monkeypatch) -> None:
    """Авто-память выключена → «новые уроки пишутся в другую папку» было бы ВРАНЬЁМ: они не
    пишутся никуда. Про это уже сказала жалоба (1), второй голос только путал бы."""
    _diverged(cfg, tmp_path, monkeypatch)
    _settings(cfg.project_root, ".claude/settings.json", {"autoMemoryEnabled": False})

    issues = SC.settings_issues(cfg)
    assert any("auto-memory is DISABLED" in i for i in issues)
    assert not any("dead tail" in i for i in issues)


def test_empty_memory_dir_keeps_its_own_wording(cfg, tmp_path: Path, monkeypatch) -> None:
    """(3) и (4) — один разъезд, но разный текст. Пустой memory_dir обязан по-прежнему
    получать формулировку про сломанный дефолт установщика, а не про мёртвый хвост."""
    from claude_memory import claude_code_env as E

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    auto = Path(E.default_auto_memory_dir(cfg.project_root))
    auto.mkdir(parents=True)
    write_lesson(str(auto), "kebab-lesson.md", name="k", description="d", type="project")

    issues = SC.settings_issues(cfg)   # memory_dir из фикстуры — пустой
    assert any("almost certainly pointed at the wrong directory" in i for i in issues)
    assert not any("dead tail" in i for i in issues)


# ── цена: здоровый основной чекаут не платит за git ─────────────────────────────

def test_healthy_main_checkout_never_shells_out_to_git(cfg, tmp_path: Path, monkeypatch) -> None:
    """КОНТРАКТ ЦЕНЫ, а не поведения. Проверка (4) бежит на каждом SessionStart, и наивная
    реализация звала бы git (~14 мс, в патологии до 5 с таймаута) у ВСЕХ, у кого не задан
    `autoMemoryDirectory`, — то есть почти у всех. Отсекатель `..._without_git` обязан
    закрывать вопрос у основного чекаута одним stat. Тест падает ровно тогда, когда кто-то
    уберёт отсекатель: поведение при этом останется верным, и заметить регресс будет нечем.
    """
    from claude_memory import claude_code_env as E

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    git = Path(cfg.project_root) / ".git"              # форма основного чекаута
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    auto = Path(E.default_auto_memory_dir_without_git(cfg.project_root))
    auto.mkdir(parents=True)
    write_lesson(str(auto), "kebab-lesson.md", name="k", description="d", type="project")

    calls = []
    monkeypatch.setattr(E, "main_checkout", lambda cwd: calls.append(cwd))

    assert SC.settings_issues(replace(cfg, memory_dir=str(auto))) == []
    assert calls == []


def test_worktree_shape_falls_back_to_git(cfg, tmp_path: Path, monkeypatch) -> None:
    """У worktree `.git` — ФАЙЛ, дешёвый отсекатель обязан честно сказать «не знаю» (None) и
    пропустить вопрос к git, а не выдать слаг от пути worktree."""
    from claude_memory import claude_code_env as E

    (Path(cfg.project_root) / ".git").write_text("gitdir: /elsewhere/.git/worktrees/w\n",
                                                 encoding="utf-8")
    assert E.main_checkout_without_git(cfg.project_root) is None
    assert E.default_auto_memory_dir_without_git(cfg.project_root) is None


def test_bare_git_directory_is_not_proof_of_checkout(cfg, tmp_path: Path) -> None:
    """Пустой каталог с именем `.git` — НЕ доказательство чекаута: внутри чужого репозитория
    git нашёл бы РОДИТЕЛЬСКИЙ `.git`, а мы бы уверенно ответили «это корень» и замолчали по
    ложному основанию. Отсекатель обязан требовать `HEAD` и в сомнении говорить «не знаю».
    """
    from claude_memory import claude_code_env as E

    (Path(cfg.project_root) / ".git").mkdir()          # форма есть, содержимого нет
    assert E.main_checkout_without_git(cfg.project_root) is None


# ── fail-open ───────────────────────────────────────────────────────────────────

def test_broken_settings_json_is_silent(cfg) -> None:
    p = Path(cfg.project_root) / ".claude" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ битый", encoding="utf-8")
    assert SC.settings_issues(cfg) == []


def test_no_settings_at_all_is_silent(cfg, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "nohome"))
    assert SC.settings_issues(cfg) == []


# ── отчёт для человека ──────────────────────────────────────────────────────────

def test_report_shows_paths_match_and_types(cfg) -> None:
    """Отчёт обязан отвечать на «что настроено», а не только на «что сломано»: при чистом
    конфиге жалоб нет, и проверить настройку человеку иначе нечем."""
    _settings(cfg.project_root, ".claude/settings.json",
              {"autoMemoryDirectory": cfg.memory_dir})
    write_lesson(cfg.memory_dir, "a-lesson.md", name="a", description="d", type="feedback")
    write_lesson(cfg.memory_dir, "b-lesson.md", name="b", description="d", type="user")

    text = "\n".join(SC.report(cfg))
    assert cfg.memory_dir in text
    assert "same directory    : yes" in text
    assert "lessons visible   : 2" in text
    assert "feedback: 1" in text and "user: 1" in text


def test_report_flags_mismatch_and_no_type(cfg, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    _settings(cfg.project_root, ".claude/settings.json", {"autoMemoryDirectory": str(other)})
    write_lesson(cfg.memory_dir, "no-type.md", name="n", description="d")

    text = "\n".join(SC.report(cfg))
    assert "same directory    : NO" in text
    assert "(no type): 1" in text


def test_report_marks_derived_path(cfg, tmp_path: Path, monkeypatch) -> None:
    """Невыведенный путь обязан быть помечен как догадка — молча выдавать её за истину
    нельзя: правило слага не задокументировано."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "nohome"))
    text = "\n".join(SC.report(cfg))
    assert "derived, not confirmed" in text


# ── (6) мёртвые команды хуков и битый интерпретатор обёртки (класс «мёртвая установка») ──

def _hooks_settings(*cmds: str) -> dict:
    return {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": c} for c in cmds]}]}}


def test_hook_cmd_missing_flagged(cfg) -> None:
    """Команда хука указывает на несуществующий файл → жалоба. Мотив: в двух живых
    проектах 28 команд около месяца указывали на каталог переименованной учётной
    записи, и ни один хук об этом не сказал — не запустившийся хук неотличим от
    хука, которому нечего сказать."""
    _settings(cfg.project_root, ".claude/settings.json",
              _hooks_settings("bash /no/such/dir/hook.sh pre"))
    assert any("/no/such/dir/hook.sh" in w for w in SC.warnings(cfg))


def test_hook_cmd_portable_var_expands_and_checks(cfg) -> None:
    ok = Path(cfg.project_root) / ".claude" / "hooks" / "ok.sh"
    ok.parent.mkdir(parents=True, exist_ok=True)
    ok.write_text("#!/bin/bash\n", encoding="utf-8")
    _settings(cfg.project_root, ".claude/settings.json", _hooks_settings(
        'bash "$CLAUDE_PROJECT_DIR"/.claude/hooks/ok.sh go',
        'bash "$CLAUDE_PROJECT_DIR"/.claude/hooks/gone.sh go',
    ))
    joined = "\n".join(SC.warnings(cfg))
    assert "gone.sh" in joined and "ok.sh" not in joined


def test_hook_cmd_unverifiable_forms_stay_silent(cfg) -> None:
    """Бинарь из PATH, чужая переменная, конвейер: надёжно разобрать такой shell
    нельзя, и «не смогли проверить» здесь законно молчит — проверка чисто
    диагностическая, потеря обратима."""
    _settings(cfg.project_root, ".claude/settings.json", _hooks_settings(
        "npx some-tool", "bash $OTHER_VAR/x.sh", "cat x | grep y"))
    assert SC.warnings(cfg) == []


def _write_wrapper(root: str, interp: str) -> Path:
    w = Path(root) / ".claude" / "hooks" / "cme_hook.sh"
    w.parent.mkdir(parents=True, exist_ok=True)
    w.write_text(f'#!/bin/bash\nexec {interp} -m claude_memory.hooks_cli "$@"\n',
                 encoding="utf-8")
    return w


def test_wrapper_interp_missing_flagged_only_in_verbose(cfg) -> None:
    """Интерпретатор, закреплённый в cme_hook.sh, отсутствует → каждый хук движка
    мёртв. Жалоба только в verbose (doctor): дешёвый SessionStart-путь субпроцессов
    не запускает — это его лицензия."""
    _write_wrapper(cfg.project_root, "/no/such/python3")
    assert not any("/no/such/python3" in w for w in SC.warnings(cfg))
    assert any("/no/such/python3" in w for w in SC.warnings(cfg, verbose=True))


def test_wrapper_interp_import_failure_flagged(cfg) -> None:
    # /usr/bin/false существует, но `import claude_memory` под ним «падает» (код 1)
    _write_wrapper(cfg.project_root, "/usr/bin/false")
    assert any("/usr/bin/false" in w for w in SC.warnings(cfg, verbose=True))


def test_wrapper_interp_healthy_silent(cfg) -> None:
    import sys as _sys
    _write_wrapper(cfg.project_root, _sys.executable)
    assert not any("cme_hook.sh" in w for w in SC.warnings(cfg, verbose=True))
