"""Тесты идемпотентного слияния хуков в settings.json."""
from __future__ import annotations

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
    assert "UserPromptSubmit" in merged["hooks"]


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
