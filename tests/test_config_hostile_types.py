"""Чужой тип в настройке НАЗЫВАЮТ, а не выключаются от него молча (заявка #21).

Перечень случаев здесь не написан руками: поля берутся интроспекцией датакласса,
а чужеродное значение выводится из рода дефолта. Поэтому поле, добавленное в
конфиг завтра, попадёт под проверку само — без правки этого файла. Рукописный
список полей повторил бы ровно ту беду, которую чинит заявка: забытое поле
выпало бы разом и из кода, и из теста.

Проверяем ТРИ звена, потому что любые два без третьего дают молчание:
  1. `load()` не бросает — иначе `hooks_cli.main()` выходит нулём ДО диспетчера
     событий, и никакая жалоба физически недостижима;
  2. событие `session-start` не бросает — иначе внешний `except → exit(0)`
     (хуки fail-open) съест исключение вместе с жалобой;
  3. `self_check.warnings()` называет ПОЛЕ по имени — иначе «настройка испорчена»
     неотличимо от «подсказывать нечего», а это и есть предмет заявки.

Замерено 2026-07-20 до починки: 280 случаев из 288 не выполняли контракт.
Проходили только `task_close_pattern` и `session_close_pattern` — два поля, у
которых именная жалоба на чужой тип была написана отдельно и заранее.
"""
import dataclasses
import json

import pytest

from claude_memory import hooks_cli, self_check
from claude_memory.config import MemoryConfig, load


def _род(значение):
    """Род поля выводим из его дефолта — это и есть объявление ожидаемого типа."""
    if isinstance(значение, bool):
        return "флаг"
    if isinstance(значение, int) and not isinstance(значение, bool):
        return "число"
    if isinstance(значение, str):
        return "строка"
    if isinstance(значение, tuple):
        return "список"
    if isinstance(значение, dict):
        return "словарь"
    return None  # дефолт None и прочее — род не объявлен, требовать нечего


# Чужое значение для каждого рода. `null` намеренно НЕ берём: для списочных полей
# он документирован как «не задано», и требовать на него жалобу значило бы сделать
# тест придирчивее кода.
ЧУЖОЕ = {
    "флаг":   [42, "да", [1]],
    "число":  ["x", [1], {"a": 1}],
    "строка": [42, [1], {"a": 1}],
    "список": [42, "строка", {"a": 1}],
    "словарь": [42, "строка", [1]],
}


def _случаи():
    for поле in dataclasses.fields(MemoryConfig):
        if поле.name in ("unknown_config_keys", "mistyped_config_keys"):
            continue  # служебные: заполняет их load(), из JSON не принимаются вовсе
        род = _род(поле.default)
        if род is None:
            continue
        for чужое in ЧУЖОЕ[род]:
            yield поле.name, чужое


СЛУЧАИ = list(_случаи())


@pytest.mark.parametrize("поле,чужое", СЛУЧАИ,
                         ids=[f"{п}={ч!r}" for п, ч in СЛУЧАИ])
def test_чужой_тип_назван_а_не_проглочен(tmp_path, поле, чужое) -> None:
    память = tmp_path / "память"
    память.mkdir()
    путь = tmp_path / "claude-memory.config.json"
    путь.write_text(json.dumps(
        {"memory_dir": str(память), "project_root": str(tmp_path), поле: чужое},
        ensure_ascii=False), encoding="utf-8")

    try:
        cfg = load(str(путь))
    except Exception as e:                                    # noqa: BLE001
        pytest.fail(f"{поле}={чужое!r}: load() упал ({type(e).__name__}) — "
                    f"движок выключится целиком, до жалобы дело не дойдёт")

    try:
        hooks_cli.ev_session_start({"cwd": str(tmp_path), "session_id": "s1"}, cfg)
    except Exception as e:                                    # noqa: BLE001
        pytest.fail(f"{поле}={чужое!r}: событие session-start упало "
                    f"({type(e).__name__}) — хук fail-open проглотит это молча")

    жалобы = self_check.warnings(cfg, verbose=True)
    assert any(поле in ж for ж in жалобы), (
        f"{поле}={чужое!r} принято молча: движок работает на дефолте, "
        f"а владелец уверен, что настройка действует"
    )
