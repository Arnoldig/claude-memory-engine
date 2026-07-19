"""Страж размера файла инструкций проекта (CLAUDE.md) — заявка #16.

Что здесь проверяется и почему именно так.

Файл инструкций разрастается молча. По числу СТРОК он выглядит нормальным, потому что в
стиле «абзац — одна строка» одна строка бывает длиной в тысячи знаков (замер на живом
проекте: 172 строки при 59 000 знаков — вчетверо больше всего бюджета горячего ядра, и ни
одного предупреждения за всё время роста). Поэтому страж меряет ЗНАКИ, и тесты ниже
закрепляют именно это, а не «строки» и не «байты».

Три свойства, которые тесты обязаны держать порознь, потому что каждое ломается отдельно:
  • ПОЛОЖИТЕЛЬНОЕ — предупреждение приходит и несёт ТЕКСТ (проверка на непустоту прошла бы
    и при полностью сломанном стражe, см. урок «мёртвый страж выглядит как блокирующий»);
  • ОТРИЦАТЕЛЬНОЕ — на чужих файлах и на файле в пределах ориентира страж МОЛЧИТ. Набор из
    одних положительных случаев зеленеет и у стража, который кричит на всё подряд;
  • ФОРМУЛИРОВКА — текст не подталкивает резать файл до числа. Это не косметика: ровно
    против такого прочтения заведена вся заявка, а цену платит следующая сессия, которая
    вырежет дорогую строку ради цифры.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from claude_memory import hooks_cli as H


def _write(root: str, name: str, text: str) -> Path:
    p = Path(root) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _edit(path: Path) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": str(path)}}


# ── Замер: знаки, и ровно те, что доходят до модели ──────────────────────────

def test_measures_chars_not_bytes(cfg) -> None:
    """Кириллица в UTF-8 — два байта на знак. Меряй страж байты, русский файл срабатывал бы
    вдвое раньше английского той же длины: одно и то же правило значило бы разное в
    зависимости от языка проекта."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    p = _write(cfg.project_root, "CLAUDE.md", "я" * 80)     # 80 знаков, 160 байт
    assert H.ev_instructions_check(_edit(p), cfg2) == ""
    p.write_text("я" * 120, encoding="utf-8")               # 120 знаков — за ориентиром
    assert "CLAUDE.md" in H.ev_instructions_check(_edit(p), cfg2)


def test_block_html_comments_do_not_count(cfg) -> None:
    """Claude Code вырезает блочные HTML-комментарии ДО подачи файла в контекст, значит в
    контекст они не попадают никогда. Считай мы их — страж наказывал бы за ровно то
    поведение, которого мы хотим: пояснения для сопровождающих вынесены в комментарий.
    Живой повод: у шаблона инструкций такая шапка тянет на пару тысяч знаков."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    body = "x" * 50
    comment = "<!--\n" + "z" * 500 + "\n-->\n"
    p = _write(cfg.project_root, "CLAUDE.md", comment + body)
    assert H.ev_instructions_check(_edit(p), cfg2) == "", "шапка-комментарий попала в замер"


def test_comment_inside_code_fence_still_counts(cfg) -> None:
    """Обратная сторона того же правила, и её легко потерять: внутри блока кода комментарий
    сохраняется (так делает и Claude Code), то есть контекст он тратит и в замер входит.
    Без этого теста «вырезать комментарии» тихо превратилось бы в «вырезать везде»."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    p = _write(cfg.project_root, "CLAUDE.md",
               "```html\n<!-- " + "z" * 200 + " -->\n```\n")
    assert "CLAUDE.md" in H.ev_instructions_check(_edit(p), cfg2)


def test_text_after_a_closing_comment_still_counts(cfg) -> None:
    """Край, на котором «вырезать блочный комментарий» легко превращается в «вырезать
    строку». Если за закрывающим `-->` на той же строке остался текст — он в контекст
    попадёт, и в замер обязан войти. Страж, который ЗАНИЖАЕТ размер, молчит там, где
    должен говорить, и это опаснее ложного крика: молчание неотличимо от «всё в порядке»."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    p = _write(cfg.project_root, "CLAUDE.md", "<!-- коротко --> " + "x" * 200 + "\n")
    assert "CLAUDE.md" in H.ev_instructions_check(_edit(p), cfg2), "хвост строки потерян"
    p.write_text("<!--\nмного\n--> " + "x" * 200 + "\n", encoding="utf-8")
    assert "CLAUDE.md" in H.ev_instructions_check(_edit(p), cfg2), "хвост после многострочного потерян"


def test_inline_comment_is_not_block_level(cfg) -> None:
    """Блочный — тот, что занимает строку целиком. Комментарий посреди прозы Claude Code не
    трогает, и мы тоже: иначе замер разошёлся бы с тем, что реально уходит в контекст."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    p = _write(cfg.project_root, "CLAUDE.md", "текст <!-- " + "z" * 200 + " --> хвост\n")
    assert "CLAUDE.md" in H.ev_instructions_check(_edit(p), cfg2)


# ── Положительное и отрицательное ────────────────────────────────────────────

def test_warns_with_text_when_over_guideline(cfg) -> None:
    """Положительный случай: предупреждение приходит И несёт имя файла, размер и ориентир.
    Проверка на непустоту прошла бы и у стража, отдающего пустую строку-заглушку."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    p = _write(cfg.project_root, "CLAUDE.md", "x" * 250)
    out = H.ev_instructions_check(_edit(p), cfg2)
    assert "CLAUDE.md" in out and "250" in out and "100" in out


def test_silent_on_other_files(cfg) -> None:
    """Отрицательный случай — обязателен. Хук висит на КАЖДОЙ правке любого файла: страж,
    который срабатывает не только на своей цели, шумит на всей работе и его выключают
    целиком, вместе с пользой.

    Стерегомый файл здесь СУЩЕСТВУЕТ и сам за ориентиром — иначе тест доказывал бы не то.
    Без него молчание объяснялось бы тем, что читать нечего, и сверка пути приставкой
    (`CLAUDE.md` ⊂ `CLAUDE.md.bak`) прошла бы незамеченной: правка соседнего файла
    выдавала бы предупреждение про чужой. Проверено мутацией — на приставочной сверке
    этот тест краснеет."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    _write(cfg.project_root, "CLAUDE.md", "x" * 500)
    for name in ("README.md", "CLAUDE.md.bak", "docs/CLAUDE.md", "app/x.py"):
        p = _write(cfg.project_root, name, "x" * 500)
        assert H.ev_instructions_check(_edit(p), cfg2) == "", name


def test_silent_within_guideline(cfg) -> None:
    """Ровно на ориентире — молчим: сравнение строгое, и граница не должна «дребезжать»."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    p = _write(cfg.project_root, "CLAUDE.md", "x" * 100)
    assert H.ev_instructions_check(_edit(p), cfg2) == ""


def test_missing_file_is_silent(cfg) -> None:
    """Файла инструкций нет вовсе — это не дефект, а обычная жизнь нового проекта."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    p = Path(cfg.project_root) / "CLAUDE.md"
    assert H.ev_instructions_check(_edit(p), cfg2) == ""


# ── Настраиваемость: список путей и выключатель ──────────────────────────────

def test_zero_budget_disables_the_guard(cfg) -> None:
    """Включённость по умолчанию обязана иметь названный способ выключения — иначе страж
    выключают тем, что перестают читать его вывод."""
    p = _write(cfg.project_root, "CLAUDE.md", "x" * 99999)
    assert H.ev_instructions_check(_edit(p), replace(cfg, instructions_budget_chars=0)) == ""
    # и обратная половина: при ненулевом бюджете тот же файл предупреждение даёт —
    # иначе тест зеленел бы и на страже, сломанном насмерть
    assert H.ev_instructions_check(_edit(p), replace(cfg, instructions_budget_chars=100)) != ""


def test_paths_are_configurable(cfg) -> None:
    """Дефолт покрывает раскладку «CLAUDE.md в корне», но список задан явно ровно затем,
    чтобы проект с иной раскладкой не остался без стража. Страж, узнающий объект по
    единственной форме записи, молча слепнет на всех прочих — домашний класс движка."""
    cfg2 = replace(cfg, instructions_budget_chars=100,
                   instructions_files=("CLAUDE.md", ".claude/CLAUDE.md"))
    nested = _write(cfg.project_root, ".claude/CLAUDE.md", "x" * 300)
    assert ".claude/CLAUDE.md" in H.ev_instructions_check(_edit(nested), cfg2)


def test_default_watches_claude_md(cfg) -> None:
    """Замок на самом умолчании: молча уехавший дефолт снял бы стража у ВСЕХ сразу."""
    assert cfg.instructions_files == ("CLAUDE.md",)
    assert cfg.instructions_budget_chars > 0


# ── Worktree: файл сессии, а не файл главной рабочей копии ───────────────────

def test_worktree_file_is_watched_not_only_the_config_root(cfg, tmp_path) -> None:
    """В worktree-сессии Claude Code читает файл инструкций РАБОЧЕЙ КОПИИ, а `project_root`
    в конфиге указывает на главный checkout. Страж, считающий только от конфига, мерил бы
    чужой файл и молчал о том, который сессия видит, — обе ошибки бесшумны. Ровно эту
    граблю движок уже обходит в привязке уроков к путям."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    wt = tmp_path / "worktrees" / "wt1"
    wt.mkdir(parents=True)
    (wt / "CLAUDE.md").write_text("x" * 300, encoding="utf-8")
    # в главной копии файла нет вовсе — значит сработать может только корень сессии
    event = {"tool_name": "Edit", "tool_input": {"file_path": str(wt / "CLAUDE.md")},
             "cwd": str(wt)}
    assert "CLAUDE.md" in H.ev_instructions_check(event, cfg2)
    assert "CLAUDE.md" in H.instructions_session_start(cfg2, cwd=str(wt))


def test_two_roots_do_not_collapse_into_one_entry(cfg, tmp_path) -> None:
    """У двух корней относительные пути СОВПАДАЮТ (`CLAUDE.md` и там, и там). Опознавай
    маркер троттлинга файл по имени — вторая запись затёрла бы первую, и один из двух
    файлов навсегда потерял бы право говорить. Поэтому опознание по абсолютному пути."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    wt = tmp_path / "worktrees" / "wt1"
    wt.mkdir(parents=True)
    _write(cfg.project_root, "CLAUDE.md", "x" * 300)
    (wt / "CLAUDE.md").write_text("x" * 400, encoding="utf-8")
    found = H.instructions_oversize(cfg2, cwd=str(wt))
    assert sorted(size for _rel, _abs, size in found) == [300, 400]
    H.instructions_session_start(cfg2, cwd=str(wt))
    stored = json.loads(
        (Path(cfg.memory_dir) / H.INSTRUCTIONS_MARKER_NAME).read_text(encoding="utf-8")
    )
    assert len(stored) == 2, f"две записи схлопнулись в одну: {stored}"


# ── Формулировка: ориентир, а не лимит ───────────────────────────────────────

def test_message_does_not_call_the_guideline_a_limit(cfg) -> None:
    """Суть требования заявки. Формулировка «превышен лимит/бюджет» подталкивает следующую
    сессию резать файл ДО числа, а резать надо по цене молчаливого пропуска правила.
    Поэтому текст обязан: сказать, что технического лимита НЕТ; назвать настоящую цену
    (правила молча не исполняются); и отделить себя от ЖЁСТКОГО порога авто-памяти, где
    содержимое действительно теряется."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    p = _write(cfg.project_root, "CLAUDE.md", "x" * 250)
    out = H.ev_instructions_check(_edit(p), cfg2).lower()
    assert "not a technical limit" in out
    assert "silently skipped" in out
    assert "25kb" in out, "нет контраста с жёстким порогом авто-памяти"
    assert "do not trim down to the number" in out


# ── Бэкстоп на старте сессии: сторожим РЕЗУЛЬТАТ, а не канал ─────────────────

def test_session_start_catches_edit_made_outside_the_editor(cfg) -> None:
    """Правку через `sed`/внешний редактор/слияние ветки хук правки не видит вовсе, и его
    молчание неотличимо от «файл в порядке». Замер на старте закрывает канал целиком."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    _write(cfg.project_root, "CLAUDE.md", "x" * 250)
    assert "CLAUDE.md" in H.instructions_session_start(cfg2)


def test_session_start_stays_silent_while_the_file_is_unchanged(cfg) -> None:
    """Возражение из заявки: сообщение о файле, который никто не менял, перестают читать, а
    вместе с ним перестают замечать настоящие предупреждения. Поэтому троттлинг — по
    ИЗМЕНЕНИЮ размера, а не по времени: решение «оставляю как есть» уже принято."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    p = _write(cfg.project_root, "CLAUDE.md", "x" * 250)
    assert H.instructions_session_start(cfg2) != ""     # первый раз — сказали
    assert H.instructions_session_start(cfg2) == ""     # файл не трогали — молчим
    p.write_text("x" * 400, encoding="utf-8")
    assert H.instructions_session_start(cfg2) != ""     # вырос — сказали снова


def test_session_start_speaks_again_after_a_fix_and_a_regrowth(cfg) -> None:
    """Край, который троттлинг по размеру ломает легче всего: файл починили (ушёл под
    ориентир), а потом он дорос ровно до ПРЕЖНЕГО числа. Если запись о старом размере
    пережила починку — страж промолчит, и молчание снова неотличимо от «всё хорошо».
    Поэтому маркер переписывается всегда, а не только когда есть о чём сказать."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    p = _write(cfg.project_root, "CLAUDE.md", "x" * 250)
    assert H.instructions_session_start(cfg2) != ""
    p.write_text("x" * 50, encoding="utf-8")            # починили
    assert H.instructions_session_start(cfg2) == ""
    p.write_text("x" * 250, encoding="utf-8")           # дорос до того же числа
    assert H.instructions_session_start(cfg2) != "", "маркер пережил починку — страж ослеп"


def test_session_start_survives_a_broken_marker(cfg) -> None:
    """Маркер — обычный файл на диске, его может испортить кто угодно. Битый JSON обязан
    значить «ничего не помню», а не смерть стража: движок fail-open, но здесь важно, что
    он именно СРАБОТАЕТ, а не тихо промолчит."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    _write(cfg.project_root, "CLAUDE.md", "x" * 250)
    (Path(cfg.memory_dir) / H.INSTRUCTIONS_MARKER_NAME).write_text("не json", encoding="utf-8")
    assert "CLAUDE.md" in H.instructions_session_start(cfg2)


def test_session_start_marker_is_a_private_file(cfg) -> None:
    """Маркер лежит в каталоге памяти рядом с уроками. Без приватной приставки `_` он попал
    бы в корпус: в оглавление, в подсказки ретривера и в счётчик уроков."""
    assert H.INSTRUCTIONS_MARKER_NAME.startswith("_")
    cfg2 = replace(cfg, instructions_budget_chars=100)
    _write(cfg.project_root, "CLAUDE.md", "x" * 250)
    H.instructions_session_start(cfg2)
    # Ключ маркера — АБСОЛЮТНЫЙ путь: относительные пути двух корней совпадают (см.
    # test_two_roots_do_not_collapse_into_one_entry), и по имени файлы неразличимы.
    assert json.loads(
        (Path(cfg.memory_dir) / H.INSTRUCTIONS_MARKER_NAME).read_text(encoding="utf-8")
    ) == {str(Path(cfg.project_root) / "CLAUDE.md"): 250}


# ── Соседство с проверкой файлов памяти ──────────────────────────────────────

def test_memory_bloat_check_is_not_disturbed(cfg) -> None:
    """Проверка файлов ПАМЯТИ живёт отдельной функцией вокруг инварианта «файл в memory_dir».
    Новый страж к ней не подмешан — и оба остаются собой: на файле инструкций молчит она,
    на файле памяти молчит он."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    ins = _write(cfg.project_root, "CLAUDE.md", "x" * 250)
    assert H.ev_bloat_check(_edit(ins), cfg2) == ""
    core = _write(cfg.memory_dir, cfg.core_file, "x" * (cfg.core_budget_bytes + 10))
    assert H.ev_bloat_check(_edit(core), cfg2) != ""
    assert H.ev_instructions_check(_edit(core), cfg2) == ""


def test_guard_is_listed_in_the_session_checklist(cfg) -> None:
    """README обещает: состав стражей всегда виден в чек-листе итогов сессии. Страж,
    которого в чек-listе нет, делает это обещание ложным — и, что хуже, выключенный страж
    становится неотличим от работающего. Проверяем ОБЕ половины: включённый попадает в
    «включены», выключенный — в «выключены», а не пропадает вовсе."""
    from claude_memory.stale_reconcile import _guard_states

    on, off = _guard_states(replace(cfg, instructions_budget_chars=20000))
    assert "instructions-size" in on and "instructions-size" not in off
    on, off = _guard_states(replace(cfg, instructions_budget_chars=0))
    assert "instructions-size" in off and "instructions-size" not in on


@pytest.mark.parametrize("tool_input", [None, "строка", {}, {"file_path": ""}])
def test_malformed_event_is_silent(cfg, tool_input) -> None:
    """Чужой/битый payload — не наше дело: fail-open, ни падения, ни ложного крика."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    _write(cfg.project_root, "CLAUDE.md", "x" * 250)
    assert H.ev_instructions_check({"tool_name": "Edit", "tool_input": tool_input}, cfg2) == ""


def test_relative_path_is_resolved_against_event_cwd(cfg) -> None:
    """Рабочий каталог процесса-хука не обязан совпадать с каталогом сессии. Резолви страж
    относительный путь от себя — он бы промахнулся мимо цели, и совершенно бесшумно."""
    cfg2 = replace(cfg, instructions_budget_chars=100)
    _write(cfg.project_root, "CLAUDE.md", "x" * 250)
    event = {"tool_name": "Edit", "tool_input": {"file_path": "CLAUDE.md"},
             "cwd": cfg.project_root}
    assert "CLAUDE.md" in H.ev_instructions_check(event, cfg2)
