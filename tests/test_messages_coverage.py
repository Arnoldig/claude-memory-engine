"""Перевод отстал от библиотеки, а `doctor` отвечает «✓ config OK» — заявка #14.

Замер, с которого всё началось: у живого проекта переопределено 8 ключей `messages` из
142. Остальные 134 штатно деградируют на английский дефолт (`msg()` так и задумана —
никогда не падать), поэтому посреди русского вывода появляются английские строки, а
соседние пункты ОДНОГО чеклиста рендерятся на разных языках. Ошибки нет, кода возврата
нет, `doctor` печатал `✓ config OK` и выходил с нулём.

Почему в отчёт, а не в жалобы SessionStart. Дописать сто с лишним ключей — работа не на
пять минут, и жалоба каждую сессию превратилась бы в постоянный фон, который перестают
читать (ровно то, чего избегает `self_check`: там жалобы чинятся один раз навсегда).
Отчёт же человек запрашивает сам — при настройке и после обновления движка.

Только ЧТЕНИЕ: движок не правит чужой конфиг. Он к нему сегодня либо не прикасается, либо
удаляет при `uninstall`; третий режим «правлю на месте» вносил бы новый класс молчаливых
отказов (частично применённое слияние, потерянные `_*`-заметки) ради задачи, которую
человек решает руками по CHANGELOG.
"""
from __future__ import annotations

from dataclasses import replace

from claude_memory import self_check as SC
from claude_memory.messages import DEFAULT_MESSAGES


def test_partial_override_reports_missing_keys(cfg) -> None:
    """Падает на коде до #14: функции не было, покрытие не считал никто."""
    some = sorted(DEFAULT_MESSAGES)[0]
    c = replace(cfg, messages={some: "перевод"})
    missing = SC.missing_message_keys(c)
    assert some not in missing
    assert len(missing) == len(DEFAULT_MESSAGES) - 1


def test_full_override_is_silent(cfg) -> None:
    """Тест на ПРОПУСК: полный перевод — нечего сообщать."""
    c = replace(cfg, messages={k: "перевод" for k in DEFAULT_MESSAGES})
    assert SC.missing_message_keys(c) == []


def test_no_override_is_silent(cfg) -> None:
    """Пустой `messages` — проект СОЗНАТЕЛЬНО на дефолтах, это не недоделанный перевод.

    Без этого различения жалоба звучала бы у каждого, кто вообще не локализуется, —
    то есть у большинства, — и обесценила бы сигнал.
    """
    assert SC.missing_message_keys(cfg) == []
    assert SC.missing_message_keys(replace(cfg, messages={})) == []


def test_orphan_keys_do_not_hide_the_gap(cfg) -> None:
    """Ключ-сирота не должен зачитываться за покрытие: считаем ПЕРЕСЕЧЕНИЕ с дефолтом."""
    c = replace(cfg, messages={"no.such.key": "х", **{k: "п" for k in DEFAULT_MESSAGES}})
    assert SC.missing_message_keys(c) == []
    c2 = replace(cfg, messages={"no.such.key": "х"})
    assert len(SC.missing_message_keys(c2)) == len(DEFAULT_MESSAGES)


def test_report_names_the_coverage(cfg) -> None:
    """Данные обязаны дойти до человека текстом, а не остаться в возвращаемом списке."""
    some = sorted(DEFAULT_MESSAGES)[0]
    c = replace(cfg, messages={some: "перевод"})
    text = "\n".join(SC.report(c))
    assert "1" in text and str(len(DEFAULT_MESSAGES)) in text


def test_report_stays_quiet_without_override(cfg) -> None:
    """Тест на ПРОПУСК: у проекта без локализации в отчёте нет строки про покрытие."""
    before = "\n".join(SC.report(cfg))
    assert str(len(DEFAULT_MESSAGES)) not in before


def test_coverage_is_not_a_session_start_complaint(cfg) -> None:
    """Граница канала: отчёт — да, жалоба каждую сессию — нет.

    Тест закрепляет намерение. Если завтра кто-то перенесёт проверку в `warnings()`, он
    сделает это осознанно, сломав тест, а не походя.
    """
    some = sorted(DEFAULT_MESSAGES)[0]
    c = replace(cfg, messages={some: "перевод"})
    assert SC.warnings(c) == SC.warnings(replace(cfg, messages={}))


def test_non_dict_messages_does_not_kill_the_report(cfg) -> None:
    """Описка в типе не должна уносить весь отчёт (конвенция сюиты)."""
    c = replace(cfg, messages=["not", "a", "dict"])  # type: ignore[arg-type]
    assert isinstance(SC.missing_message_keys(c), list)
    assert isinstance(SC.report(c), list)
