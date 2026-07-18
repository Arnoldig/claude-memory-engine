"""Тесты самодиагностики (self_check) + новых сигналов пульса (wiki-ссылки, счётчик уроков)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from claude_memory import self_check as SC
from conftest import RU_EN_CLOSE_PATTERN, write_lesson


# ── self_check: сверка плейсхолдеров override ⊆ дефолта ─────────────────────────

def test_self_check_flags_bad_placeholder(cfg) -> None:
    from claude_memory import self_check
    cfg2 = replace(cfg, messages={"precedent.index_preamble": "битый {len(cards)} тут"})
    issues = self_check.message_placeholder_issues(cfg2)
    assert any(k == "precedent.index_preamble" and "len(cards)" in extra for k, extra in issues)
    assert self_check.run(cfg2)  # непустое предупреждение


def test_self_check_clean_config_silent(cfg) -> None:
    from claude_memory import self_check
    assert self_check.message_placeholder_issues(cfg) == []
    assert self_check.run(cfg) == ""


# ── self_check: опечатка в имени ключа конфига (difflib) ───────────────────────

def test_self_check_flags_typo_key_but_not_new_key(cfg) -> None:
    from claude_memory import self_check
    # Опечатка близка к известному ключу → лаем: настройка молча не в силе, остаётся
    # англ. дефолт (ровно так уже умирал страж закрытия задачи).
    cfg2 = replace(cfg, unknown_config_keys=("session_close_patterns", "stop_words"))
    assert self_check.typo_key_issues(cfg2) == [
        ("session_close_patterns", "session_close_pattern"), ("stop_words", "stopwords")]
    # Честно новый/чужой ключ НЕ похож ни на что известное → молчим: отбрасывание
    # неизвестных ключей задумано ради forward-compat, ломать его жалобой нельзя.
    cfg3 = replace(cfg, unknown_config_keys=("quantum_flux_capacitor", "vendor_specific_thing"))
    assert self_check.typo_key_issues(cfg3) == []
    assert self_check.run(cfg3) == ""


def test_self_check_ignores_underscore_comment_keys(cfg) -> None:
    # Живой конфиг рабочего проекта держит заметку-комментарий `_task_close_pattern_note`.
    # Она БЛИЗКА к `task_close_pattern` → без правила про ведущий `_` жалоба была бы
    # ложной на КАЖДОМ старте. Ложное срабатывание — то, от чего жалобы начинают
    # игнорировать, то есть лекарство хуже болезни.
    from claude_memory import self_check
    cfg2 = replace(cfg, unknown_config_keys=("_task_close_pattern_note", "_note"))
    assert self_check.typo_key_issues(cfg2) == []
    assert self_check.run(cfg2) == ""


def test_unknown_keys_recorded_by_loader_not_taken_from_json(cfg, tmp_path) -> None:
    """self_check физически не увидит опечатку, если загрузчик не запомнит выброшенное:
    к нему конфиг приходит уже очищенным. И само поле нельзя принимать из JSON — иначе
    конфиг подделает отчёт о собственных опечатках."""
    import json
    from claude_memory import config as C
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"memory_dir": str(tmp_path), "session_close_patterns": r"\bx\b",
                             "unknown_config_keys": ["подделка"]}), encoding="utf-8")
    c = C.load(str(p))
    assert "session_close_patterns" in c.unknown_config_keys
    assert "подделка" not in c.unknown_config_keys       # из JSON не принимаем
    assert c.session_close_pattern == r"\bclose session\b"  # опечатка → остался дефолт


def test_self_check_cli_verbose_lists_far_unknown_keys(cfg) -> None:
    """Слепая зона difflib — опечатка, далёкая от всех ключей. Закрывается CLI-режимом:
    человек сам попросил проверку, справочный список уместен. На SessionStart его нет."""
    from claude_memory import self_check
    cfg2 = replace(cfg, unknown_config_keys=("zzz_totally_off",))
    assert self_check.warnings(cfg2) == []                      # старт молчит
    assert any("zzz_totally_off" in w for w in self_check.warnings(cfg2, verbose=True))


# ── self_check: молча выключенные стражи (битый regex / дата не в ISO) ──────────

def test_self_check_flags_broken_regex(cfg) -> None:
    from claude_memory import self_check
    cfg2 = replace(cfg, session_close_pattern=r"(закрыт[ьи")
    assert [f for f, _ in self_check.bad_regex_issues(cfg2)] == ["session_close_pattern"]
    assert "silently OFF" in self_check.run(cfg2)
    # пустой шаблон = страж намеренно выключен, это не дефект
    assert self_check.bad_regex_issues(replace(cfg, session_close_pattern="")) == []


def test_self_check_flags_non_iso_config_date(cfg) -> None:
    from claude_memory import self_check
    cfg2 = replace(cfg, model_registry_verified_on="01.01.2026")
    assert self_check.bad_date_issues(cfg2) == [("model_registry_verified_on", "01.01.2026")]
    # None = страж намеренно выключен; корректный ISO = молчим
    assert self_check.bad_date_issues(replace(cfg, model_registry_verified_on=None)) == []
    assert self_check.bad_date_issues(replace(cfg, model_registry_verified_on="2026-01-01")) == []


def test_self_check_valid_subset_ok(cfg) -> None:
    from claude_memory import self_check
    # override без плейсхолдеров (⊆ любого дефолта) — не нарушение
    cfg2 = replace(cfg, messages={"health.no_topic": "тем нет"})
    assert self_check.message_placeholder_issues(cfg2) == []


def test_self_check_ignores_orphan_key(cfg) -> None:
    from claude_memory import self_check
    # ключа нет в дефолтах → не предмет этой проверки (формат не ломается)
    cfg2 = replace(cfg, messages={"my.custom.key": "raw {whatever}"})
    assert self_check.message_placeholder_issues(cfg2) == []


def test_self_check_both_real_bugs_caught(cfg) -> None:
    from claude_memory import self_check
    cfg2 = replace(cfg, messages={
        "precedent.index_preamble": "{len(cards)}",
        "marker.violation_reason": "{lines_part} {limit}",
    })
    keys = {k for k, _ in self_check.message_placeholder_issues(cfg2)}
    assert keys == {"precedent.index_preamble", "marker.violation_reason"}


# ── битые [[wiki]]-ссылки между уроками ────────────────────────────────────────

def test_find_broken_wikilinks(cfg) -> None:
    from claude_memory import catalog_generate as cg
    write_lesson(cfg.memory_dir, "feedback_a.md", description="a", topic="workflow",
                 body="см. [[feedback_b]] и [[feedback_gone]]")
    write_lesson(cfg.memory_dir, "feedback_b.md", description="b", topic="workflow")
    broken = cg.find_broken_wikilinks(cfg.memory_dir, cfg)
    assert ("feedback_a.md", "feedback_gone") in broken
    assert not any(t == "feedback_b" for _, t in broken)   # файл есть → не битая


def test_wikilink_to_name_slug_ok(cfg) -> None:
    from claude_memory import catalog_generate as cg
    write_lesson(cfg.memory_dir, "feedback_x.md", name="feedback_special-slug",
                 description="x", topic="workflow", body="ссылка [[feedback_special-slug]]")
    assert cg.find_broken_wikilinks(cfg.memory_dir, cfg) == []   # цель = name-слаг, существует


def test_wikilink_with_md_extension_ok(cfg) -> None:
    from claude_memory import catalog_generate as cg
    # обе конвенции: `[[feedback_b]]` и `[[feedback_b.md]]` — на существующий файл не битые
    write_lesson(cfg.memory_dir, "feedback_a.md", description="a", topic="workflow",
                 body="с расширением [[feedback_b.md]] и без [[feedback_b]]")
    write_lesson(cfg.memory_dir, "feedback_b.md", description="b", topic="workflow")
    assert cg.find_broken_wikilinks(cfg.memory_dir, cfg) == []


def test_wikilink_ignores_non_lesson_refs(cfg) -> None:
    from claude_memory import catalog_generate as cg
    write_lesson(cfg.memory_dir, "feedback_p.md", description="p", topic="workflow",
                 body="произвольная [[заметка в скобках]] — не ссылка-урок")
    assert cg.find_broken_wikilinks(cfg.memory_dir, cfg) == []   # не начинается с префикса урока


def test_broken_wikilinks_in_pulse(cfg) -> None:
    from claude_memory import catalog_generate as cg
    write_lesson(cfg.memory_dir, "feedback_a.md", description="a", topic="workflow",
                 body="[[feedback_missing]]")
    _, diag = cg.build_catalog(cfg.memory_dir, cfg)
    assert len(diag["broken_wikilinks"]) == 1
    assert cg.format_health_pulse(diag, cfg)   # пульс не молчит при битой wiki-ссылке


# ── нудж «много уроков → проверь дубли» (только дедуп, без обобщения) ───────────

def test_pulse_many_lessons_nudges(cfg) -> None:
    from claude_memory import catalog_generate as cg
    cfg2 = replace(cfg, lesson_count_warn=2)
    for i in range(3):
        write_lesson(cfg.memory_dir, f"feedback_{i}.md", name=f"урок {i}",
                     description=f"d{i}", topic="workflow")
    _, diag = cg.build_catalog(cfg2.memory_dir, cfg2)
    pulse = cg.format_health_pulse(diag, cfg2)
    assert "3" in pulse and "duplicat" in pulse.lower()


def test_pulse_silent_under_threshold(cfg) -> None:
    from claude_memory import catalog_generate as cg
    cfg2 = replace(cfg, lesson_count_warn=100)
    write_lesson(cfg.memory_dir, "feedback_a.md", name="урок a",
                 description="a", topic="workflow")
    _, diag = cg.build_catalog(cfg2.memory_dir, cfg2)
    assert cg.format_health_pulse(diag, cfg2) == ""


def test_pulse_count_check_off_when_zero(cfg) -> None:
    from claude_memory import catalog_generate as cg
    cfg0 = replace(cfg, lesson_count_warn=0)   # дефолт теперь 500 — выключаем явно
    for i in range(5):
        write_lesson(cfg.memory_dir, f"feedback_{i}.md", name=f"урок {i}",
                     description=f"d{i}", topic="workflow")
    _, diag = cg.build_catalog(cfg.memory_dir, cfg0)
    assert cg.format_health_pulse(diag, cfg0) == ""


# ── Отставший шаблон закрытия (заявка #8, 0.14.0) ───────────────────────────
# Класс: шаблон КОМПИЛИРУЕТСЯ и работает, но был скопирован из старого дефолта и
# заморозился, а дефолт с тех пор вырос. Соседний класс (СЛОМАННЫЙ шаблон) ловит
# bad_regex_issues; этот молчал полтора релиза в обоих боевых проектах.

# Дефолт ДО 0.10.0 — намеренно замороженная история: шесть слов из девяти, нет семьи
# `resolve`. Ровно это значение и разъехалось у потребителей. Трекать текущий дефолт
# эта строка не обязана и не должна.
_PRE_RESOLVE_DEFAULT = r"(?i)(?<![\w-])(?:clos(?:e|es|ed)|fix(?:es|ed)?)\s+#([\w-]+)"


def test_lag_caught_on_frozen_old_default(cfg) -> None:
    """Исторический снимок обязан ловиться: это воспроизведение реального дефекта."""
    c = replace(cfg, task_close_pattern=_PRE_RESOLVE_DEFAULT)
    issues = SC.close_pattern_lag_issues(c)
    assert len(issues) == 1
    field, missing = issues[0]
    assert field == "task_close_pattern"
    # три слова семьи resolve в обоих написаниях + все девять слов с двоеточием
    assert set(missing) >= {"Resolve", "Resolves", "Resolved"}
    assert "Close:" in missing and "Fix:" in missing


def test_lag_caught_on_frozen_default_with_project_branch(cfg) -> None:
    """Форма, в которой дефект и жил у потребителей: старый дефолт + своя русская ветка.
    Добавленная ветка не должна прятать отставание английской части."""
    c = replace(cfg, task_close_pattern=_PRE_RESOLVE_DEFAULT + r"|#([\w-]+)\s+закрыт[аоы]?\b")
    missing = SC.close_pattern_lag_issues(c)[0][1]
    assert set(missing) >= {"Resolve", "Resolves", "Resolved", "Close:"}


def test_lag_silent_on_current_project_pattern(cfg) -> None:
    """МОЛЧАНИЕ на текущей боевой форме (дефолт + русские ветки) — то, что стоит в обоих
    живых конфигах сегодня. Тест берёт conftest-копию, а не файл с чужой машины: тест,
    зависящий от чужого ноутбука, закрепляет ноутбук, а не поведение."""
    assert SC.close_pattern_lag_issues(replace(cfg, task_close_pattern=RU_EN_CLOSE_PATTERN)) == []


def test_lag_silent_on_library_default(cfg) -> None:
    assert SC.close_pattern_lag_issues(cfg) == []


def test_default_pattern_covers_every_keyword(cfg) -> None:
    """Замок на сам дефолт: он обязан узнавать КАЖДУЮ форму эталона. Без него дефолт и
    константа могли бы разъехаться — та же болезнь, что чинит заявка #8."""
    from claude_memory.stop_check import GITHUB_CLOSE_KEYWORDS, extract_closed_task

    for word in GITHUB_CLOSE_KEYWORDS:
        assert extract_closed_task(f"feat: {word} #42", cfg.task_close_pattern) == "42", word


def test_lag_silent_on_full_replacement(cfg) -> None:
    """0 из 9 — это законная ПОЛНАЯ замена под чужой трекер, а не отставание.
    Жалоба здесь была бы навязчивой и неустранимой — такую отключают первой."""
    assert SC.close_pattern_lag_issues(replace(cfg, task_close_pattern=r"DONE-(\d+)")) == []


def test_lag_silent_when_gate_disabled(cfg) -> None:
    """Страж выключен целиком — шаблон не используется, жалоба была бы шумом."""
    c = replace(cfg, task_close_pattern=_PRE_RESOLVE_DEFAULT, task_close_lesson_gate=False)
    assert SC.close_pattern_lag_issues(c) == []


def test_lag_silent_on_empty_pattern(cfg) -> None:
    assert SC.close_pattern_lag_issues(replace(cfg, task_close_pattern="")) == []


def test_broken_regex_yields_exactly_one_complaint(cfg) -> None:
    """У битого шаблона lag-проверка обязана молчать: иначе один дефект даёт две жалобы,
    и человек чинит не то. Порядок в warnings() тоже проверяем — грубая идёт первой."""
    c = replace(cfg, task_close_pattern=r"(?i)(?<![\w-])clos(e|es|ed)\s+#([\w-]+")  # скобка не закрыта
    assert SC.close_pattern_lag_issues(c) == []
    assert SC.bad_regex_issues(c)
    texts = SC.warnings(c)
    # Признак берём из текста ИМЕННО lag-жалобы: имя поля `task_close_pattern` стоит и в
    # жалобе про битый regex, поэтому по нему две жалобы неразличимы.
    assert sum("closing keyword" in t for t in texts) == 0
    assert sum("not a valid regular expression" in t for t in texts) == 1


def test_lag_complaint_reaches_warnings(cfg) -> None:
    """Данные обязаны дойти до человека текстом: функция может быть верной, а ключ
    сообщения — забытым, и тогда жалоба не прозвучит."""
    c = replace(cfg, task_close_pattern=_PRE_RESOLVE_DEFAULT)
    texts = [t for t in SC.warnings(c) if "Resolve" in t]
    assert len(texts) == 1, SC.warnings(c)
    assert "Resolve" in texts[0] and "Close:" in texts[0]
    assert "Close: #42" in texts[0]         # образец коммита готов к вставке, без вырождения


def test_lag_requires_the_id_itself_not_just_a_match(cfg) -> None:
    """Зонд требует, чтобы шаблон вернул САМ id (`42`), а не просто «что-то совпало».

    Без этой строгости мимо проверки прошёл бы шаблон, где дописанная ветка захватывает
    `#42` вместе с решёткой: слово узнано, а боевой страж пойдёт искать урок про `##42` и
    не найдёт никогда — гейт неисправен ровно тем же молчаливым способом. Пробел нашло
    ревью: мутация `!= "42"` → `is None` проходила всю сюиту зелёной."""
    mixed = (r"(?i)(?<![\w-])(?:clos(?:e|es|ed)|fix(?:es|ed)?)\s+#([\w-]+)"
             r"|(?<![\w-])resolv(?:e|es|ed)\s+(#[\w-]+)")   # у resolve-ветки решётка ВНУТРИ группы
    from claude_memory.stop_check import extract_closed_task
    assert extract_closed_task("feat: Resolves #42", mixed) == "#42"   # совпало, но id битый
    assert set(SC.close_pattern_lag_issues(replace(cfg, task_close_pattern=mixed))[0][1]) >= {
        "Resolve", "Resolves", "Resolved"
    }


def test_non_string_pattern_does_not_kill_all_diagnostics(cfg) -> None:
    """Описка в типе (`"task_close_pattern": 42`) не должна уносить ВСЕ жалобы разом.

    `_coerce` типы строковых полей не приводит, `re.compile(42)` даёт TypeError, а он не
    `re.error` — без перехвата падал бы весь `warnings()`, и человек терял бы заодно
    диагностику расхождения каталогов, опечаток в ключах и всего прочего."""
    c = replace(cfg, task_close_pattern=42)          # type: ignore[arg-type]
    assert SC.close_pattern_lag_issues(c) == []
    assert SC.bad_regex_issues(c)                    # о самой описке всё же сказано
    assert isinstance(SC.warnings(c), list)          # и остальные проверки живы


# ── Вторая координата эталона: НАПИСАНИЕ формы (заявка #13, 0.15.0) ─────────
# GitHub принимает и `Closes #42`, и `Closes: #42`. До 0.15.0 вторую форму движок молча
# не узнавал: задача закрывалась, привратник урока молчал.

def test_lag_caught_when_only_spelling_is_behind(cfg) -> None:
    """ГЛАВНЫЙ ТЕСТ ЭТОГО ВЫПУСКА — анти-инверсия предохранителя.

    Копия дефолта 0.14.0 знает все девять СЛОВ, но ни одного написания с двоеточием: это
    отставание, и о нём надо сказать. Прежний порог сверялся с числом СЛОВ, и при двух
    написаниях 9 промахов из 18 совпадали с этим числом — страж принимал отставшую копию
    за «законную полную замену» и МОЛЧАЛ, а настоящую замену наоборот обвинял. Проверка
    начинала работать наоборот, ровно тем молчаливым способом, против которого заведена."""
    old_default = (r"(?i)(?<![\w-])(?:clos(?:e|es|ed)|fix(?:es|ed)?|resolv(?:e|es|ed))"
                   r"\s+#([\w-]+)")
    missing = SC.close_pattern_lag_issues(replace(cfg, task_close_pattern=old_default))[0][1]
    assert missing == ["Close:", "Closes:", "Closed:", "Fix:", "Fixes:", "Fixed:",
                       "Resolve:", "Resolves:", "Resolved:"]


def test_full_replacement_still_silent_with_two_syntaxes(cfg) -> None:
    """Зеркало предыдущего: настоящая полная замена обязана молчать и при 18 зондах."""
    assert SC.close_pattern_lag_issues(replace(cfg, task_close_pattern=r"DONE-(\d+)")) == []


def test_threshold_is_derived_from_probes_not_frozen(cfg) -> None:
    """Порог «узнано ноль» обязан считаться от длины списка зондов. Замороженное число
    или множитель «×2» вернут ту же инверсию при третьем написании."""
    from claude_memory.stop_check import GITHUB_CLOSE_KEYWORDS, GITHUB_CLOSE_SYNTAXES
    assert len(GITHUB_CLOSE_KEYWORDS) * len(GITHUB_CLOSE_SYNTAXES) == 18


@pytest.mark.parametrize("text,expected", [
    ("Closes: #42", "42"),                 # документирована
    ("CLOSES: #42", "42"),                 # документирована (регистр)
    ("feat: Closes: #42", "42"),           # закрытие внутри конвенциональной темы
    ("Closes:#42", "42"),                  # НЕ документирована, принята намеренно шире
    ("Closes #moya-zadacha", "moya-zadacha"),   # слаг — только в пробельной ветке
])
def test_default_reads_documented_spellings(cfg, text: str, expected: str) -> None:
    from claude_memory.stop_check import extract_closed_task
    assert extract_closed_task(text, cfg.task_close_pattern) == expected


@pytest.mark.parametrize("text", [
    # Conventional Commits: `fix:` — ТИП коммита, `#слаг` — тема, а не закрываемая задача.
    # Боевые примеры из истории проекта-потребителя (619 тем с таким префиксом); ветка,
    # принимающая после двоеточия любой id, дала бы здесь 8 ложных блокировок Stop.
    "fix: #incident-2026-05-10 — gitignore infra/prometheus/scrape_token*",
    "fix: #egress-pin + #payment-test-credentials (#39)",
    "fix: #cleanup-2026-05-04 — browser console hygiene",
    "auto-closes: #10",                    # дефис слева — не закрытие
    "prefixed-closes: #10",
])
def test_default_ignores_conventional_commit_prefix(cfg, text: str) -> None:
    from claude_memory.stop_check import extract_closed_task
    assert extract_closed_task(text, cfg.task_close_pattern) is None


def test_colon_branch_takes_numbers_only(cfg) -> None:
    """Слаг после двоеточия — это Conventional Commits, а не закрытие. GitHub на
    нечисловой id не реагирует вовсе, поэтому двоеточная ветка наследует его семантику."""
    from claude_memory.stop_check import extract_closed_task
    assert extract_closed_task("Closes: #memory-lib", cfg.task_close_pattern) is None
    assert extract_closed_task("Closes #memory-lib", cfg.task_close_pattern) == "memory-lib"


def test_reference_holds_only_documented_spellings() -> None:
    """Эталон — строго документация; дефолт вправе быть шире, обратное — нет.
    Слитной формы `Closes:#42` в эталоне быть не должно: требовать от чужого шаблона
    больше, чем документирует GitHub, библиотека не вправе."""
    from claude_memory.stop_check import GITHUB_CLOSE_SYNTAXES
    templates = [t for _, t in GITHUB_CLOSE_SYNTAXES]
    assert templates == ["{word} #42", "{word}: #42"]
    assert all(":#" not in t for t in templates)
