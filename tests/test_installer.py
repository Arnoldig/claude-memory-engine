"""Тесты идемпотентного слияния хуков в settings.json."""
from __future__ import annotations

import json

from claude_memory import installer as I

SCRIPT = "/proj/.claude/hooks/cme_hook.sh"


def _commands(settings: dict) -> list:
    out = []
    for groups in settings.get("hooks", {}).values():
        for g in groups:
            for h in g.get("hooks", []):
                out.append(h.get("command", ""))
    return out


def test_merge_into_empty_adds_all() -> None:
    merged, added = I.merge_settings({}, SCRIPT)
    assert added == len(I.HOOK_REGISTRATIONS)
    cmds = _commands(merged)
    assert f"bash {SCRIPT} retrieve" in cmds
    assert f"bash {SCRIPT} agent-guard" in cmds
    assert f"bash {SCRIPT} session-end" in cmds   # SessionEnd staleness
    assert f"bash {SCRIPT} stop-check" in cmds     # Stop lessons reminder
    assert "UserPromptSubmit" in merged["hooks"]
    assert "SessionEnd" in merged["hooks"] and "Stop" in merged["hooks"]


def test_merge_is_idempotent() -> None:
    once, a1 = I.merge_settings({}, SCRIPT)
    twice, a2 = I.merge_settings(once, SCRIPT)
    assert a1 == len(I.HOOK_REGISTRATIONS)
    assert a2 == 0                                   # повтор ничего не добавил
    assert _commands(once) == _commands(twice)        # дублей нет


def test_foreign_hooks_preserved() -> None:
    existing = {
        "hooks": {
            "UserPromptSubmit": [
                {"matcher": "", "hooks": [{"type": "command", "command": "bash /other/foo.sh"}]}
            ],
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "bash /other/bar.sh"}]}
            ],
        }
    }
    merged, added = I.merge_settings(existing, SCRIPT)
    cmds = _commands(merged)
    assert "bash /other/foo.sh" in cmds              # чужой UserPromptSubmit сохранён
    assert "bash /other/bar.sh" in cmds              # чужой PreToolUse сохранён
    assert f"bash {SCRIPT} retrieve" in cmds         # наш добавлен
    assert added == len(I.HOOK_REGISTRATIONS)


def test_input_not_mutated() -> None:
    src = {"hooks": {}}
    I.merge_settings(src, SCRIPT)
    assert src == {"hooks": {}}                       # вход не мутирован (глубокая копия)


def test_same_matcher_reused_not_duplicated() -> None:
    # PreToolUse Edit|Write|MultiEdit и PostToolUse Write|Edit|MultiEdit — разные события;
    # внутри одного события один matcher не плодит группы при повторе.
    merged, _ = I.merge_settings({}, SCRIPT)
    pre_groups = merged["hooks"]["PostToolUse"]
    matchers = [g["matcher"] for g in pre_groups]
    assert len(matchers) == len(set(matchers))        # уникальные matcher-группы


# --- снятие регистраций (uninstall) -----------------------------------------

def test_remove_round_trips_merge() -> None:
    merged, added = I.merge_settings({}, SCRIPT)
    cleaned, removed = I.remove_engine_hooks(merged)
    assert removed == added == len(I.HOOK_REGISTRATIONS)
    assert cleaned == {}                               # вернулись к исходному пустому состоянию


def test_remove_is_idempotent() -> None:
    merged, _ = I.merge_settings({}, SCRIPT)
    once, r1 = I.remove_engine_hooks(merged)
    twice, r2 = I.remove_engine_hooks(once)
    assert r1 == len(I.HOOK_REGISTRATIONS)
    assert r2 == 0                                      # повторное снятие ничего не находит


def test_remove_preserves_foreign_hooks() -> None:
    existing = {
        "hooks": {
            "UserPromptSubmit": [
                {"matcher": "", "hooks": [{"type": "command", "command": "bash /other/foo.sh"}]}
            ],
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "bash /other/bar.sh"}]}
            ],
        }
    }
    merged, _ = I.merge_settings(existing, SCRIPT)
    cleaned, removed = I.remove_engine_hooks(merged)
    cmds = _commands(cleaned)
    assert removed == len(I.HOOK_REGISTRATIONS)
    assert "bash /other/foo.sh" in cmds                # чужой UserPromptSubmit сохранён
    assert "bash /other/bar.sh" in cmds                # чужой PreToolUse сохранён
    assert not any("cme_hook.sh" in c for c in cmds)   # наших не осталось


def test_remove_keeps_foreign_in_shared_group() -> None:
    # наш хук и чужой попадают в ОДНУ matcher=""-группу события UserPromptSubmit
    existing = {
        "hooks": {
            "UserPromptSubmit": [
                {"matcher": "", "hooks": [{"type": "command", "command": "bash /other/foo.sh"}]}
            ]
        }
    }
    merged, _ = I.merge_settings(existing, SCRIPT)
    cleaned, _ = I.remove_engine_hooks(merged)
    cmds = _commands(cleaned)
    assert "bash /other/foo.sh" in cmds                # чужой в общей группе уцелел
    assert not any("cme_hook.sh" in c for c in cmds)
    assert "UserPromptSubmit" in cleaned["hooks"]      # событие не удалено (в нём остался чужой)


def test_remove_on_empty_is_noop() -> None:
    assert I.remove_engine_hooks({}) == ({}, 0)


def test_remove_does_not_mutate_input() -> None:
    merged, _ = I.merge_settings({}, SCRIPT)
    snapshot = json.loads(json.dumps(merged))
    I.remove_engine_hooks(merged)
    assert merged == snapshot                           # вход не мутирован (глубокая копия)


def test_remove_tolerates_non_object_settings() -> None:
    # валидный JSON, но не объект (массив/строка) — не падаем, снимать нечего
    assert I.remove_engine_hooks([]) == ({}, 0)
    assert I.remove_engine_hooks("nonsense") == ({}, 0)


def test_merge_tolerates_non_object_settings() -> None:
    merged, added = I.merge_settings([], SCRIPT)        # вход не объект → как пустой
    assert added == len(I.HOOK_REGISTRATIONS)
    assert f"bash {SCRIPT} retrieve" in _commands(merged)


def test_load_settings_coerces_non_object(tmp_path) -> None:
    p = tmp_path / "settings.json"
    p.write_text("[]", encoding="utf-8")               # валидный JSON-массив, не объект
    assert I.load_settings(str(p)) == {}               # приводим к {}
