# Changelog

[Русский](CHANGELOG.md)

Notable changes to this project are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/), and versions follow [Semantic Versioning](https://semver.org/).

## [0.8.0] — 2026-06-30
### Added
- The stale-lessons guard (`stale_reconcile`) now fires on a SESSION-CLOSE PHRASE (the `UserPromptSubmit` hook) instead of the closing commit. When the user's message matches the new `session_close_pattern`, a session MEMORY CHECKLIST is injected into context: counts (lessons shown on edits / reconciled / remaining), precise re-verify candidates (you edited a file with an attached lesson but did not update the lesson), a by-meaning related list (now computed WHENEVER there are edited files — even without `applies_to` bindings), shelf-life/archive status, and the list of enabled/disabled guards. The checklist is ALWAYS shown on the close phrase, even when clean ("no stale lessons"). The trigger does not depend on a commit/PR/skill, so it works for projects without a closing command. `/compact` keeps the same `session_id` (markers stay visible); `/clear` is covered by the `SessionEnd` backstop.
- New config fields: `session_close_pattern` (close-phrase regex; empty → no phrase-triggered checklist) and `session_close_case_sensitive` (whether matching is case-sensitive; `true` removes false matches on common words like "Done").
### Changed
- Guards are now ON by default (affects ALL consumers): `stale_reconcile_gate` `False → True`, `archive_stale_months` `0 → 6`, `lesson_count_warn` `0 → 500` (duplicate-check hint on large collections), and `known_model_substrs` `() → (opus, sonnet, haiku, fable)` (the reactive "unknown model" check; the periodic lineup re-verify stays separate via `model_registry_verified_on`, off by default). The checklist always shows honestly what is enabled and what was found, so enabling does not create a false impression. Without `applies_to` bindings the precise list is empty, but the by-meaning list and the guard summary are still useful. A project can restore the previous behavior by setting these fields to `false`/`0`/`()`.
### Removed
- The previous `reconcile_reminder` trigger (a one-shot `Stop` block on task close) was removed — replaced by the close-phrase checklist. The separate "commit closed a task but no lesson for it" gate (`closure_reminder` / `task_close_lesson_gate`) is unchanged.

## [0.7.5] — 2026-06-29
### Changed
- Packaging: migrated the license metadata in `pyproject.toml` to PEP 639. The `license` field is now an SPDX expression (`license = "Apache-2.0"` instead of the TOML table `{ text = ... }`); the `License :: OSI Approved :: Apache Software License` classifier was removed (under PEP 639 the license is declared only via the `license` field, and the duplicate classifier is deprecated); and the build-requires floor was raised to `setuptools>=77.0.0`, the version that added PEP 639 support. This clears the two `SetuptoolsDeprecationWarning`s at build time (the `project.license` TOML table and license classifiers were deprecated and would stop working by 2027-02-18). Install behavior is unchanged; the license metadata on PyPI is now in the current SPDX format.

## [0.7.4] — 2026-06-29
### Changed
- CI: bumped the actions in `.github/workflows/publish.yml` to the majors that run natively on Node 24 — `actions/checkout@v4 → @v7` and `actions/setup-python@v5 → @v6`. GitHub deprecated Node 20 on its runners and was forcing these steps onto Node 24 with a warning at publish time; the bump removes the warning. As of this date these are the current stable majors (checkout v7 released 2026-06-18, setup-python v6.x; both declare `runs.using: node24`). For this trivial usage (checkout with no inputs; setup-python with only `python-version: "3.x"`) the upgrade is drop-in with no behavior change (pip caching in setup-python v6 stays opt-in). No package content changed — this release only exercises the updated workflow and confirms the warning is gone.

## [0.7.3] — 2026-06-29
### Fixed
- In the default `task_close_pattern`, the left boundary changed from `\b` to a negative lookbehind `(?<![\w-])`. `\b` also matches AFTER a hyphen, so a commit message like `prefixed-closes #10` (or `auto-closes #10`) was falsely read as closing task #10 — `extract_closed_task` returned `10` instead of `None`, and the close gates (`closure_reminder`, `stale_reconcile`) could show a spurious one-shot reminder. The lookbehind still accepts ordinary `Closes #id` / `Fixes #id` (at line start, after a space or a colon) but not `<word>-closes #id`. Found by a red-team check while adding a localized closure form. Affects only the generic default; consumers whose own `task_close_pattern` uses `\b` as the left boundary before the closure keyword (including anyone who copied the old default into their config) should apply the same `\b` → `(?<![\w-])` change.

## [0.7.2] — 2026-06-28
### Fixed
- `extract_closed_task` now returns the FIRST non-empty capture group of the match instead of hard-coding group 1, letting a project's `task_close_pattern` recognize task closure across DIFFERENT phrasings where the id sits on either side of the keyword. English `Closes #id` puts the id AFTER the keyword, while a localized form such as Russian `#id закрыт` puts it BEFORE — one capture group cannot cover both. The library default stays English (generic); a project supplies the form it needs via its own `task_close_pattern`. A pattern with no groups also no longer crashes (None instead of IndexError). Found on a real task close written in the Russian form `#id закрыт` (no `Closes`): both `closure_reminder` and `stale_reconcile` silently never fired. Second fix to closure detection after the `%B` fix in v0.7.1 (commit body).

## [0.7.1] — 2026-06-28
### Fixed
- The task-close gates (`closure_reminder` and the stale-lesson guard `stale_reconcile`) now detect `Closes #N` in the commit BODY, not only the subject (`%B` instead of `%s` in `last_commit_msg`). Previously, with `Closes #N` in the body (as GitHub recognizes it), both gates silently never fired. Found by dogfooding on a real task close.

## [0.7.0] — 2026-06-28
### Added
- Stale-lesson guard at task close (`stale_reconcile`, opt-in `stale_reconcile_gate`, off by default). On a closing commit (`Closes #N`), Stop shows ONCE the lessons attached to files you edited this session but did NOT update — asking "are they stale?" (fix / mark stale / replace). A semantic list (offline search over the edited files plus the commit subject) is appended to catch related lessons with no path binding. A repeat Stop passes (one-shot block). The same candidates are written as a section in `_stale_pending.md` as a SessionEnd backstop. Fail-open: any error, or inability to write the one-shot marker, degrades to not blocking.
### Changed
- The applies-gate marker (path-triggered lessons shown before an edit) now stores the names of the shown lessons — the stale-lesson guard collects them at task close.

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
