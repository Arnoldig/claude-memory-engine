"""Суб-агент не наследует ни правил проекта, ни уроков — движок даёт ему указатели.

ЗАМЕР, РАДИ КОТОРОГО ЭТО НАПИСАНО (заявка #17, клиент 2.1.217). Контрольный вопрос
задавался с запретом вызывать инструменты — проверялось содержимое контекста, а не
умение открыть файл по пути:

| тип суб-агента  | правила проекта | указатель памяти | тексты уроков |
|-----------------|-----------------|------------------|---------------|
| general-purpose | видит           | видит            | НЕ видит      |
| Explore         | НЕ видит        | НЕ видит         | НЕ видит      |
| Plan            | НЕ видит        | НЕ видит         | НЕ видит      |

Подбор уроков движок печатает на UserPromptSubmit, а у суб-агента такого события нет:
за 405 запусков суб-агентов в одном проекте-потребителе и 75 в другом урок не дошёл
ни до одного.

ПОЧЕМУ БЕЗ СПИСКА ТИПОВ. Отдавать указатели только тем типам, которые «не видят
правил», значит держать в коде список, verность которого задаёт чужой клиент: он
менялся между версиями и сменится снова, а расхождение будет молчаливым — суб-агент
просто не получит указателя, и это неотличимо от «указатель не понадобился». Поэтому
адресат — ЛЮБОЙ суб-агент; лишние двести знаков дешевле молчания.

КАНАЛ. `SubagentStart` доставляет текст в контекст САМОГО суб-агента через
`hookSpecificOutput.additionalContext` — проверено живым замером: `Explore` процитировал
контрольную метку дословно. Голый stdout здесь в контекст не попадает, поэтому канал
проверяется отдельным тестом: неверный канал даёт ровно то же, что молчание.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from claude_memory import hooks_cli as H
from claude_memory import installer as I


def _правила_и_каталог(cfg, tmp_path: Path) -> tuple:
    """Создать файл правил и указатель уроков там, где движок их ищет."""
    правила = Path(cfg.project_root) / cfg.instructions_files[0]
    правила.write_text("ПРАВИЛА\n", encoding="utf-8")
    каталог = Path(cfg.memory_dir) / cfg.catalog_file
    каталог.write_text("# уроки\n", encoding="utf-8")
    return правила, каталог


def test_называет_и_правила_и_каталог_уроков(cfg, tmp_path) -> None:
    правила, каталог = _правила_и_каталог(cfg, tmp_path)

    текст = H.ev_subagent_start(cfg, cfg.project_root)

    assert str(правила) in текст, "без пути к правилам суб-агент их не найдёт"
    assert str(каталог) in текст, "без пути к уроками суб-агент о них не узнает"


def test_молчит_когда_указывать_не_на_что(cfg, tmp_path) -> None:
    """ПАРНЫЙ на пропуск: ни правил, ни каталога — выдумывать пути нельзя."""
    assert H.ev_subagent_start(cfg, cfg.project_root) == ""


def test_говорит_даже_если_есть_только_одно_из_двух(cfg, tmp_path) -> None:
    """Половина указателей лучше молчания: отсутствие второго файла — не повод молчать."""
    (Path(cfg.project_root) / cfg.instructions_files[0]).write_text("ПРАВИЛА\n", encoding="utf-8")

    текст = H.ev_subagent_start(cfg, cfg.project_root)

    assert текст, "есть правила — значит есть о чём сказать"
    assert cfg.catalog_file not in текст, "несуществующий каталог называть нельзя"


@pytest.mark.parametrize("тип", ["Explore", "Plan", "general-purpose", "тип-которого-ещё-нет"])
def test_адресат_любой_суб_агент_включая_незнакомый(cfg, tmp_path, тип) -> None:
    """Список типов в коде устареет молча — поэтому его нет.

    Незнакомый тип здесь не экзотика: набор встроенных типов задаёт клиент и он уже
    менялся. Тип, выпавший из списка, не получил бы указателя, а это неотличимо от
    «указатель не понадобился».
    """
    _правила_и_каталог(cfg, tmp_path)

    вывод = _прогнать_событие(cfg, tmp_path, {"agent_type": тип})

    assert "additionalContext" in вывод, f"тип {тип} остался без указателей"


def _прогнать_событие(cfg, tmp_path: Path, событие: dict) -> str:
    """Пропустить событие через диспетчер так, как его зовёт Claude Code."""
    import contextlib

    from claude_memory import config as C

    файл = tmp_path / "cme.json"
    файл.write_text(json.dumps({"memory_dir": cfg.memory_dir, "project_root": cfg.project_root}),
                    encoding="utf-8")
    полное = dict(событие)
    полное.setdefault("session_id", "s")
    полное.setdefault("cwd", cfg.project_root)
    захват = io.StringIO()
    старый_argv, старый_stdin = sys.argv, sys.stdin
    import os
    старый_конфиг = os.environ.get("CLAUDE_MEMORY_CONFIG")
    os.environ["CLAUDE_MEMORY_CONFIG"] = str(файл)
    sys.argv = ["cme", "subagent-start"]
    sys.stdin = io.StringIO(json.dumps(полное))
    C.reset_cache()
    try:
        with contextlib.redirect_stdout(захват), pytest.raises(SystemExit):
            H.main()
    finally:
        sys.argv, sys.stdin = старый_argv, старый_stdin
        if старый_конфиг is None:
            os.environ.pop("CLAUDE_MEMORY_CONFIG", None)
        else:
            os.environ["CLAUDE_MEMORY_CONFIG"] = старый_конфиг
        C.reset_cache()
    return захват.getvalue()


def test_канал_именно_тот_который_доходит_до_суб_агента(cfg, tmp_path) -> None:
    """Голый stdout на этом событии в контекст не попадает — нужен JSON нужной формы.

    Неверный канал даёт ровно то же, что молчание: хук отработал, вывод есть, а в
    контексте суб-агента пусто.
    """
    _правила_и_каталог(cfg, tmp_path)

    данные = json.loads(_прогнать_событие(cfg, tmp_path, {"agent_type": "Explore"}))

    вывод = данные["hookSpecificOutput"]
    assert вывод["hookEventName"] == "SubagentStart"
    assert вывод["additionalContext"].strip(), "текст обязан быть непустым"


def test_молчание_не_печатает_пустого_json(cfg, tmp_path) -> None:
    """ПАРНЫЙ на пропуск: нечего сказать — ничего не печатаем."""
    assert _прогнать_событие(cfg, tmp_path, {"agent_type": "Explore"}) == ""


def test_событие_зарегистрировано_установщиком() -> None:
    """Незарегистрированный хук неотличим от отсутствующего: он не сработает ни разу."""
    события = {(имя, ev) for имя, _matcher, ev, _timeout in I.HOOK_REGISTRATIONS}

    assert ("SubagentStart", "subagent-start") in события


def test_матчер_события_пустой() -> None:
    """Пустой matcher = все типы суб-агентов. Список типов в настройках протух бы так же
    молча, как список в коде."""
    matchers = [m for имя, m, _ev, _t in I.HOOK_REGISTRATIONS if имя == "SubagentStart"]

    assert matchers == [""], f"ожидался один пустой matcher, а не {matchers}"
