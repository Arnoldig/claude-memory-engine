"""Тема НАПИСАНА, но её нет в конфиге — заявка #14.

Отказ, который тут закрывается. Урок с `topic: anubis` при конфиге без такого слага
попадал в раздел «⚠ No topic (add `topic:` to the frontmatter to file it here)», а само
значение не появлялось НИ В ОДНОМ канале — ни в указателе, ни в пульсе, ни в CLI. То есть
движок говорил человеку буквально обратное факту: «добавь тему» о файле, где тема есть.
Причина — `run_diagnostics` вычисляла `ls.topic not in titles` и тут же выбрасывала само
`ls.topic`, оставляя одно имя файла.

Почему это отдельный класс, а не подвид «без темы»: лечится он по-другому. «Нет поля»
чинится в УРОКЕ (дописать `topic:`), «неизвестное значение» — либо в уроке (опечатка в
слаге), либо в КОНФИГЕ (тема настоящая, но не заведена). Один заголовок на два случая
уводит ровно в ту половину, которая неверна.
"""
from __future__ import annotations

from dataclasses import replace

from claude_memory import catalog_generate as G
from conftest import write_lesson


def _lessons(cfg):
    return G.collect_lessons(cfg.memory_dir, cfg)


# ── Диагностика разводит два случая ────────────────────────────────────────────

def test_unknown_topic_is_reported_with_the_value_seen(cfg) -> None:
    """Падает на коде до #14: ключа `unknown_topic` в диагностике не было вовсе."""
    write_lesson(cfg.memory_dir, "feedback_bogus.md", name="b", description="d",
                 topic="totally-bogus-slug")
    diag = G.run_diagnostics(cfg.memory_dir, _lessons(cfg), cfg)
    assert diag["unknown_topic"] == [("feedback_bogus.md", "totally-bogus-slug")]


def test_missing_field_and_unknown_value_are_disjoint(cfg) -> None:
    """Два случая не пересекаются: иначе один урок считается дважды и цифры в пульсе врут."""
    write_lesson(cfg.memory_dir, "feedback_none.md", name="n", description="d")
    write_lesson(cfg.memory_dir, "feedback_bogus.md", name="b", description="d",
                 topic="totally-bogus-slug")
    diag = G.run_diagnostics(cfg.memory_dir, _lessons(cfg), cfg)
    assert diag["no_topic"] == ["feedback_none.md"]
    assert [f for f, _ in diag["unknown_topic"]] == ["feedback_bogus.md"]


def test_known_topic_is_silent_in_both_buckets(cfg) -> None:
    """Тест на ПРОПУСК: исправный урок не должен попадать ни в один из счётчиков."""
    write_lesson(cfg.memory_dir, "feedback_ok.md", name="o", description="d", topic="workflow")
    diag = G.run_diagnostics(cfg.memory_dir, _lessons(cfg), cfg)
    assert diag["no_topic"] == [] and diag["unknown_topic"] == []


def test_empty_topic_value_counts_as_missing_not_unknown(cfg) -> None:
    """`topic:` с пустым значением — это «не задано», а не «неизвестное значение».

    Пустую строку `parse_frontmatter` и так не кладёт в результат (`if v: out[key] = v`),
    поэтому различить «поля нет» и «поле пустое» ниже по течению нельзя. Тест закрепляет
    хотя бы то, что пустое НЕ уезжает в unknown_topic с пустым слагом в тексте жалобы.
    """
    write_lesson(cfg.memory_dir, "feedback_empty.md", name="e", description="d", topic="")
    diag = G.run_diagnostics(cfg.memory_dir, _lessons(cfg), cfg)
    assert diag["unknown_topic"] == []
    assert diag["no_topic"] == ["feedback_empty.md"]


# ── Значение доходит до человека: указатель ────────────────────────────────────

def test_index_names_the_unknown_value(cfg) -> None:
    """Указатель — тот артефакт, который человек реально читает.

    До #14 он показывал «⚠ нет темы» о файле, где тема написана, и сам слаг не печатал.
    """
    write_lesson(cfg.memory_dir, "feedback_bogus.md", name="b", description="d",
                 topic="totally-bogus-slug")
    idx = G.render_index(_lessons(cfg), cfg)
    assert "totally-bogus-slug" in idx


def test_index_does_not_annotate_lessons_that_really_have_no_topic(cfg) -> None:
    """Тест на ПРОПУСК: урок без поля не получает приписки про неизвестное значение.

    Признак берём по маркеру приписки, а не по подстроке `topic:` — она стоит и в самом
    заголовке ⚠-раздела («add `topic:` to the frontmatter»), так что по ней два случая
    неразличимы (первая редакция теста именно на этом и падала).
    """
    write_lesson(cfg.memory_dir, "feedback_none.md", name="n", description="d")
    idx = G.render_index(_lessons(cfg), cfg)
    assert "⟵" not in idx


# ── Значение доходит до человека: пульс SessionStart ───────────────────────────

def test_pulse_says_unknown_topic_separately(cfg) -> None:
    """«5 без темы» и «2 с незаведённой темой» требуют РАЗНЫХ действий — значит и фразы разные."""
    write_lesson(cfg.memory_dir, "feedback_bogus.md", name="b", description="d",
                 topic="totally-bogus-slug")
    diag = G.run_diagnostics(cfg.memory_dir, _lessons(cfg), cfg)
    pulse = G.format_health_pulse(diag, cfg)
    assert pulse
    assert "totally-bogus-slug" in pulse


def test_pulse_silent_when_every_topic_is_known(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_ok.md", name="o", description="d", topic="workflow")
    diag = G.run_diagnostics(cfg.memory_dir, _lessons(cfg), cfg)
    assert G.format_health_pulse(diag, cfg) == ""


def test_pulse_throttle_signature_notices_a_fixed_typo(cfg) -> None:
    """Опечатку исправили — долг изменился, пульс обязан прозвучать заново.

    Подпись троттлинга собиралась из счётчиков; без отдельного счётчика неизвестных тем
    исправление слага её не меняло, и пульс молчал бы до истечения суток — новый
    молчаливый отказ, внесённый самой починкой.
    """
    write_lesson(cfg.memory_dir, "feedback_bogus.md", name="b", description="d",
                 topic="totally-bogus-slug")
    diag_a = G.run_diagnostics(cfg.memory_dir, _lessons(cfg), cfg)
    sig_a = G._pulse_signature(diag_a, cfg)
    write_lesson(cfg.memory_dir, "feedback_bogus.md", name="b", description="d",
                 topic="workflow")
    diag_b = G.run_diagnostics(cfg.memory_dir, _lessons(cfg), cfg)
    assert G._pulse_signature(diag_b, cfg) != sig_a


def test_pulse_signature_notices_a_swap_that_keeps_the_count(cfg) -> None:
    """СОСТАВ слагов, а не только их число: счётчик неизменен, беда — новая.

    Ровно тот случай, ради которого в подписи стоит `:{slugs}`, а не один `ut{n}`: одну
    опечатку исправили, рядом завелась другая, `ut` как был 2, так и остался. Без состава
    подпись совпадает, `throttle_pulse` возвращает пусто — и про НОВУЮ незаведённую тему
    человек не узнаёт до истечения суток.

    Мутационная проверка ревью: удаление одного лишь `:{_unknown}` из подписи проходило
    всю сюиту зелёной — то есть самая суть защиты не была закреплена ничем.
    """
    write_lesson(cfg.memory_dir, "feedback_a.md", name="a", description="d", topic="bogus-a")
    write_lesson(cfg.memory_dir, "feedback_b.md", name="b", description="d", topic="bogus-b")
    diag_a = G.run_diagnostics(cfg.memory_dir, _lessons(cfg), cfg)
    write_lesson(cfg.memory_dir, "feedback_a.md", name="a", description="d", topic="workflow")
    write_lesson(cfg.memory_dir, "feedback_c.md", name="c", description="d", topic="bogus-c")
    diag_b = G.run_diagnostics(cfg.memory_dir, _lessons(cfg), cfg)

    assert len(diag_a["unknown_topic"]) == len(diag_b["unknown_topic"]) == 2  # счётчик тот же
    assert G._pulse_signature(diag_a, cfg) != G._pulse_signature(diag_b, cfg)


def test_throttle_pulse_speaks_again_when_only_the_slugs_changed(cfg, tmp_path) -> None:
    """То же самое, но через БОЕВУЮ функцию троттлинга, а не через приватную подпись.

    Сюита трогала только `_pulse_signature`; `throttle_pulse` — то, что реально зовёт хук
    SessionStart (`hooks_cli`), — не вызывалась ни одним тестом. Верная подпись, не
    доехавшая до решения «показывать/молчать», человеку ничего не даёт.
    """
    import datetime
    marker = str(tmp_path / "_marker")
    today = datetime.date(2026, 7, 19)
    write_lesson(cfg.memory_dir, "feedback_a.md", name="a", description="d", topic="bogus-a")
    write_lesson(cfg.memory_dir, "feedback_b.md", name="b", description="d", topic="bogus-b")
    diag_a = G.run_diagnostics(cfg.memory_dir, _lessons(cfg), cfg)
    first = G.throttle_pulse(G.format_health_pulse(diag_a, cfg), diag_a, cfg,
                             today=today, marker=marker)
    assert first  # первый показ состоялся и записал маркер

    write_lesson(cfg.memory_dir, "feedback_a.md", name="a", description="d", topic="workflow")
    write_lesson(cfg.memory_dir, "feedback_c.md", name="c", description="d", topic="bogus-c")
    diag_b = G.run_diagnostics(cfg.memory_dir, _lessons(cfg), cfg)
    # СЛЕДУЮЩИЙ день: правило «не чаще раза в сутки» иначе съело бы показ само по себе,
    # и тест был бы зелёным по неверной причине.
    second = G.throttle_pulse(G.format_health_pulse(diag_b, cfg), diag_b, cfg,
                              today=today + datetime.timedelta(days=1), marker=marker)
    assert second and "bogus-c" in second


# ── Значение доходит до человека: CLI-диагностика ──────────────────────────────

def test_cli_diagnostics_print_the_value(cfg, capsys) -> None:
    """Функция может быть верной, а ключ сообщения — забытым; тогда жалоба не прозвучит."""
    import sys
    write_lesson(cfg.memory_dir, "feedback_bogus.md", name="b", description="d",
                 topic="totally-bogus-slug")
    diag = G.run_diagnostics(cfg.memory_dir, _lessons(cfg), cfg)
    G.print_diagnostics(diag, cfg, stream=sys.stderr)
    err = capsys.readouterr().err
    assert "feedback_bogus.md" in err and "totally-bogus-slug" in err


# ── Устойчивость ──────────────────────────────────────────────────────────────

def test_non_string_topic_does_not_kill_diagnostics(cfg) -> None:
    """Описка в типе не должна уносить ВСЮ диагностику разом (конвенция сюиты)."""
    lessons = _lessons(cfg)
    lessons.append(G.Lesson(filename="x.md", name="x", description="d", doc_type="",
                            topic=42, subtopic="", reverify_after="",  # type: ignore[arg-type]
                            size=10, has_frontmatter=True))
    diag = G.run_diagnostics(cfg.memory_dir, lessons, cfg)
    assert isinstance(diag["unknown_topic"], list)
    assert isinstance(G.format_health_pulse(diag, cfg), str)


def test_project_taxonomy_decides_what_is_unknown(cfg) -> None:
    """«Неизвестна» — относительно КОНФИГА проекта, а не заводского списка."""
    c = replace(cfg, topic_order=(("ops", "Operations"),))
    write_lesson(cfg.memory_dir, "feedback_a.md", name="a", description="d", topic="ops")
    write_lesson(cfg.memory_dir, "feedback_b.md", name="b", description="d", topic="workflow")
    diag = G.run_diagnostics(c.memory_dir, G.collect_lessons(c.memory_dir, c), c)
    assert [f for f, _ in diag["unknown_topic"]] == ["feedback_b.md"]
