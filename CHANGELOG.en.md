# Changelog

[Русский](CHANGELOG.md)

Notable changes to this project are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/), and versions follow [Semantic Versioning](https://semver.org/).

## [0.6.0] — 2026-06-21
### Added
- `claude-memory uninstall` — removes the engine from a project (hook registrations, wrapper, config, and the vendored copy for the git install); your lessons are not touched.
- An English `README.en.md` next to the primary `README.md`.
### Changed
- README rewritten for a broad audience (overview first; prose separated from commands).
- `install.sh` and CLI output translated to English.
### Fixed
- `install` / `uninstall` no longer crash on a valid-but-non-object `settings.json` or config file.
- Package version synchronized (`pyproject.toml` had lagged behind the code).

## Earlier releases (2026-06-17 – 2026-06-18)
- **0.5.2** — retention period for archived lessons + `archive_prune`.
- **0.5.1** — diagnostics: flag lessons with an empty name.
- **0.5.0** — pip package + one-command install `claude-memory init` (plus `doctor`, `config`, `--version`).
- **0.4.0** — SQLite cache for retrieval (same ranking, faster on large lesson sets).
- **0.3.0** — config self-check and a health pulse (broken cross-links, duplicate nudge).
- **0.2.0** — language-agnostic messages (English defaults + per-project overrides), fully configurable behavior, worktree support.
- **0.1.0** — first engine: frontmatter lessons, auto-CATALOG, offline retrieval, path-triggered lessons, auto-maintenance, parallel-session and sub-agent guards.
