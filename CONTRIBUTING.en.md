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
