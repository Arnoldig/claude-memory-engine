"""Тесты поиска уроков по пути файла (applies_to-глобы)."""
from __future__ import annotations

from claude_memory import applies_to as A
from conftest import write_lesson


def test_inline_glob_matches(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_chat.md",
                 description="правка чата", applies_to="[app/routers/chat.py, static/js/chat.js]")
    res = A.find_lessons_for_path("app/routers/chat.py", cfg)
    assert res == [("feedback_chat.md", "правка чата")]


def test_yaml_list_glob_matches(cfg) -> None:
    p = cfg.memory_dir + "/feedback_tpl.md"
    with open(p, "w", encoding="utf-8") as f:
        f.write("---\ndescription: шаблоны\nmetadata:\n  applies_to:\n"
                "    - templates/*.html\n    - app/core/pdf.py\n---\nтело\n")
    assert A.find_lessons_for_path("templates/claim.html", cfg) == [("feedback_tpl.md", "шаблоны")]
    assert A.find_lessons_for_path("app/core/pdf.py", cfg) == [("feedback_tpl.md", "шаблоны")]


def test_scalar_glob_matches(cfg) -> None:
    # applies_to одиночной СТРОКОЙ (не списком) — валидный YAML-скаляр, который движок
    # с рождения молча ронял в []: значение не с «[» → уходило в разбор YAML-списка, а
    # первая же строка ниже (`metadata:`) обрывала цикл. Результат неотличим от «уроков нет».
    for name, value in (("feedback_s1.md", "app/routers/chat.py"),      # без кавычек
                        ("feedback_s2.md", '"app/routers/chat.py"'),    # в двойных
                        ("feedback_s3.md", "'app/routers/chat.py'")):   # в одинарных
        p = cfg.memory_dir + "/" + name
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"---\ndescription: чат\napplies_to: {value}\nmetadata:\n  topic: infra\n---\nтело\n")
    assert A.find_lessons_for_path("app/routers/chat.py", cfg) == [
        ("feedback_s1.md", "чат"), ("feedback_s2.md", "чат"), ("feedback_s3.md", "чат"),
    ]


def test_scalar_glob_with_wildcard_matches(cfg) -> None:
    # скаляр — полноценный глоб, не только точный путь
    write_lesson(cfg.memory_dir, "feedback_w.md", description="стили", applies_to="site/src/styles/*.css")
    assert A.find_lessons_for_path("site/src/styles/admin.css", cfg) == [("feedback_w.md", "стили")]


def test_empty_value_still_reads_yaml_list_below(cfg) -> None:
    # ГРАНИЦА скалярной ветки: пустое значение + список ниже — это YAML-список, а НЕ скаляр.
    # Скаляром считаем только НЕПУСТОЕ значение, иначе сломали бы рабочий формат.
    p = cfg.memory_dir + "/feedback_y.md"
    with open(p, "w", encoding="utf-8") as f:
        f.write("---\ndescription: шаблоны\napplies_to:\n  - templates/*.html\n---\nтело\n")
    assert A.find_lessons_for_path("templates/claim.html", cfg) == [("feedback_y.md", "шаблоны")]


def test_unparsed_applies_to_detected(cfg) -> None:
    # ГЛАВНОЕ: поле есть, глобов нет → движок обязан это ЗАМЕТИТЬ (а не молча вернуть []).
    # Пустое значение без списка ниже: привязку объявили и не заполнили.
    assert A.unparsed_applies_to("---\napplies_to:\nmetadata:\n  topic: infra\n---") == ""
    # значение есть, но глоб из него не вышел (пустые кавычки / пустой инлайн-список)
    assert A.unparsed_applies_to('---\napplies_to: ""\n---') == '""'
    assert A.unparsed_applies_to("---\napplies_to: []\n---") == "[]"
    # YAML-отображение / brace-expansion — не глоб: жалоба, а НЕ «протухшая привязка»
    assert A.unparsed_applies_to("---\napplies_to: {путь: app/x.py}\n---") == "{путь: app/x.py}"
    assert A.unparsed_applies_to("---\napplies_to: {app,lib}/x.py\n---") == "{app,lib}/x.py"
    # разобранные формы жалобы НЕ вызывают
    assert A.unparsed_applies_to("---\napplies_to: app/x.py\n---") is None
    assert A.unparsed_applies_to("---\napplies_to: [app/x.py]\n---") is None
    assert A.unparsed_applies_to("---\napplies_to:\n  - app/x.py\n---") is None
    # поля нет вовсе — это НЕ дефект, это урок без привязки (None, а не "")
    assert A.unparsed_applies_to("---\ndescription: без привязки\n---") is None


def test_applies_to_value_distinguishes_absent_from_empty(cfg) -> None:
    # Контракт, на котором стоит жалоба: None (поля нет) ≠ "" (поле есть, значение пусто).
    assert A.applies_to_value("---\ndescription: x\n---") is None
    assert A.applies_to_value("---\napplies_to:\n---") == ""
    assert A.applies_to_value("---\napplies_to: app/x.py\n---") == "app/x.py"


def test_no_match_returns_empty(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_chat.md",
                 description="чат", applies_to="[app/routers/chat.py]")
    assert A.find_lessons_for_path("app/main.py", cfg) == []


def test_lesson_without_applies_to_ignored(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_x.md", description="без applies_to")
    assert A.find_lessons_for_path("app/x.py", cfg) == []


def test_format_lines(cfg) -> None:
    assert A.format_lines([("a.md", "desc"), ("b.md", "")]) == "- a.md: desc\n- b.md"


def test_quoted_description_stripped_in_path_lessons(cfg) -> None:
    # description в кавычках → в «уроках по пути» показывается БЕЗ кавычек, как в CATALOG
    # и поиске. Раньше applies_to снимал только пробелы → кавычки протекали в вывод
    # (рассинхрон половин системы, задача DRY-хелпера strip_scalar).
    write_lesson(cfg.memory_dir, "feedback_q.md",
                 description='"чат в кавычках"', applies_to="[app/routers/chat.py]")
    assert A.find_lessons_for_path("app/routers/chat.py", cfg) == [("feedback_q.md", "чат в кавычках")]


def test_strip_scalar_removes_one_quote_layer() -> None:
    # Контракт общего хелпера: trim + снятие ОДНОГО слоя обрамляющих кавычек любого вида.
    assert A.strip_scalar('  "x"  ') == "x"
    assert A.strip_scalar("'y'") == "y"
    assert A.strip_scalar("z") == "z"      # без кавычек → только trim
    assert A.strip_scalar("") == ""        # пусто → пусто (совместимо с «нет значения»)
