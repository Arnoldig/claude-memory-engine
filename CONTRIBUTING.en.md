# Contributing

[Русский](CONTRIBUTING.md)

Thanks for your interest! Questions, bug reports, and ideas are welcome via the Issues tab.

## Development

```
git clone https://github.com/Arnoldig/claude-memory-engine.git
cd claude-memory-engine
pip install pytest
python3 -m pytest
```

## Ground rules
- Zero third-party dependencies: the engine runs from a shell hook and must stay on the pure Python standard library.
- Keep the tests green and add a test for every new behavior.
- Operator-facing output is in English; messages are templated in `claude_memory/messages.py` and overridable per project.
- Keep changes small and focused; describe the user-facing effect in your pull request.
- **Never rename a config field without keeping an alias.** The engine ships as a vendored copy, so a project's config and engine can drift apart in version. `config._coerce` drops unknown keys silently (forward-compat), and `self_check.typo_key_issues` flags an unknown key that is close to a known one (difflib). So renaming `foo` → `foos` makes an OLD engine copy complain about a perfectly valid newer config, every session, with no way to silence it. When you rename a field, keep the old name working as an alias (and remove it in a separate change once the copies have caught up).
- **Never silently override what the human stated explicitly.** If a field is set but the engine did not understand it — or decided to ignore it — the engine must SAY SO (`frontmatter.unparsed_*`, `self_check.*`). An empty parse result is indistinguishable from "not set", and that kind of defect survives for years: it already happened with a `topic` from a foreign taxonomy, with the Stop gate on a Russian closing phrase, with a scalar `applies_to`, and with non-ISO dates. A special case of the same mistake is the "clever" exception: a blanket skip of `.claude/` used to cancel explicit bindings to the project code that lives there.
