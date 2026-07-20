"""Настройка задана ОСМЫСЛЕННО, а движок молча делает не то — заявка #14.

Два замеренных случая вокруг таксономии тем:
  • `"topic_order": null` — доезжал до конструктора как None и ронял `topic_titles()`
    TypeError'ом далеко от причины: `is not None` в `_coerce` пропускал приведение к
    кортежам, но сам ключ из данных не убирал, поэтому дефолт датакласса не срабатывал.
    Тот же зазор был у ВСЕХ списочных полей (`_TUPLE_FIELDS`), не только у тем.
  • `"topic_order": []` — принимался молча и давал указатель без единого раздела: все
    уроки уезжали в «⚠ без темы». Отличить это от «просто ещё нет уроков с темой» было
    нечем.
"""
from __future__ import annotations

import json

import pytest

from claude_memory import config as C
from claude_memory import self_check as SC


def _load(tmp_path, **keys):
    mem = tmp_path / "memory"
    mem.mkdir(exist_ok=True)
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"memory_dir": str(mem), "project_root": str(tmp_path), **keys}),
                 encoding="utf-8")
    return C.load(str(p))


# ── null в списочном поле = «не задано», а не None ────────────────────────────

def test_null_topic_order_falls_back_to_default(tmp_path) -> None:
    """Падает на коде до #14: `topic_titles()` бросал TypeError на None."""
    cfg = _load(tmp_path, topic_order=None)
    assert cfg.topic_order == C._DEFAULT_TOPIC_ORDER
    assert cfg.topic_titles()["workflow"]


@pytest.mark.parametrize("field", sorted(C._TUPLE_FIELDS))
def test_null_in_any_list_field_falls_back_to_default(tmp_path, field) -> None:
    """Зазор был общий для всех списочных полей — закрываем его целиком, а не точечно.

    Иначе следующее поле повторит ту же историю: `null` доедет до кода, который его
    перебирает, и уронит движок в месте, не связанном с настройкой.
    """
    cfg = _load(tmp_path, **{field: None})
    default = C.MemoryConfig.__dataclass_fields__[field].default
    assert getattr(cfg, field) == default


def test_explicit_value_still_wins(tmp_path) -> None:
    """Тест на ПРОПУСК: починка не должна затирать осмысленно заданное значение."""
    cfg = _load(tmp_path, topic_order=[["ops", "Operations"]])
    assert cfg.topic_order == (("ops", "Operations"),)


# ── Пустая таксономия — жалоба, а не тишина ───────────────────────────────────

def test_empty_topic_order_is_flagged(tmp_path) -> None:
    """Падает на коде до #14: пустой список принимался молча."""
    cfg = _load(tmp_path, topic_order=[])
    assert SC.topic_order_issues(cfg)


def test_empty_topic_order_complaint_reaches_warnings(tmp_path) -> None:
    """Данные обязаны дойти до человека текстом — функция может быть верной, а ключ забытым."""
    cfg = _load(tmp_path, topic_order=[])
    assert any("topic_order" in t for t in SC.warnings(cfg))


def test_normal_taxonomy_is_silent(tmp_path) -> None:
    """Тест на ПРОПУСК: и дефолт, и свой непустой список (с `core`) — молчат."""
    assert SC.topic_order_issues(_load(tmp_path)) == []
    assert SC.topic_order_issues(
        _load(tmp_path, topic_order=[["ops", "Ops"], ["core", "Ядро"]])) == []


def test_taxonomy_without_core_is_silent(tmp_path) -> None:
    """Отсутствие `core` НЕ жалоба — и это осознанно.

    Слаг `core` читает единственное место — `bootstrap_topics_from_catalog`
    (`catalog_generate.py`), разовый ПРЕВЬЮ-путь миграции. Жаловаться каждую сессию про
    путь, который выполняется один раз за жизнь проекта, — это ложное срабатывание, а с
    него начинается привычка не читать жалобы вовсе.

    Список ЗДЕСЬ обязан быть непустым и без `core`: иначе тест вырождается в копию
    соседнего (ревью поймало ровно это — две проверки были побайтово одинаковы, и вторая
    не убивала ни одной мутации).
    """
    cfg = _load(tmp_path, topic_order=[["ops", "Ops"], ["legal", "Право"]])
    assert "core" not in dict(cfg.topic_order)
    assert SC.topic_order_issues(cfg) == []
    assert SC.warnings(cfg) == []


@pytest.mark.parametrize("junk", ["workflow", {"a": "b"}])
def test_topic_order_check_survives_junk(tmp_path, junk) -> None:
    """Мусор в поле не должен уносить ВСЕ жалобы самодиагностики разом.

    Прежняя редакция подавала сюда `[]` — корректный пустой список, идущий обычной веткой,
    а вовсе не мусор: ветка `except TypeError` не исполнялась ни разу (находка ревью).
    Здесь мусор настоящий: строка и словарь переживают `_coerce` (оба итерируемы), доезжают
    до самодиагностики и не должны её ронять.
    """
    cfg = _load(tmp_path, topic_order=junk)
    assert isinstance(SC.topic_order_issues(cfg), list)
    assert isinstance(SC.warnings(cfg), list)


def test_non_iterable_topic_order_is_named_not_fatal(tmp_path) -> None:
    """ГРАНИЦА ПЕРЕПИСАНА ОСОЗНАННО (заявка #21, 2026-07-20).

    Прежняя редакция закрепляла обратное: `"topic_order": 42` роняло саму загрузку.
    Тест был заведён как закладка со словами «когда его починят, он упадёт и заставит
    осознанно переписать эту границу» — что и произошло, он покраснел на починке.

    Новое поведение: значение чужого рода отбрасывается, действует дефолт, а имя поля
    уходит в жалобу. Причина смены направления — цена ошибки. Падение загрузки хуки
    fail-open превращали в выход нулём: движок выключался ЦЕЛИКОМ и молча, и «выключен
    опиской» выглядело как «подсказывать нечего». Потерять одну настройку и сказать об
    этом дешевле, чем потерять всю память сессии и промолчать.
    """
    cfg = _load(tmp_path, topic_order=42)
    assert "topic_order" in cfg.mistyped_config_keys
    assert cfg.topic_order == C.MemoryConfig.topic_order, "должен действовать дефолт"
    assert any("topic_order" in ж for ж in SC.warnings(cfg)), "поле обязано быть названо"
