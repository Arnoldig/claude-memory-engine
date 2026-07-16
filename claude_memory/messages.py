"""Каталог операторских сообщений движка памяти (i18n).

Все строки, которые движок ПОКАЗЫВАЕТ человеку/ассистенту или пишет в
человекочитаемое тело файла, собраны здесь как шаблоны с именованными
плейсхолдерами. Дефолты — английские (нейтральные, универсальные). Любой
проект переопределяет нужные ключи через MemoryConfig.messages (JSON-конфиг),
не трогая код — так библиотека работает на любом языке без потери скорости
(словарь + str.format, ноль зависимостей).

НЕ сюда: значения-данные (topic_order, no_topic_title, catalog_preamble,
precedent_keyword/pointer, маркеры AUTO-INDEX) — это поля конфига, а не сообщения;
и внутренние сентинелы логов (INHERITED/nosess) — это данные, не текст для человека.

msg(cfg, key, **params): берёт шаблон из cfg.messages (если задан), иначе из
DEFAULT_MESSAGES; форматирует плейсхолдеры. Неизвестный ключ → возвращает сам
ключ (fail-soft: хук не падает; тест ловит неизвестные ключи отдельно).
"""
from __future__ import annotations

from typing import Optional


DEFAULT_MESSAGES = {

    "applies_to.gate.footer": "\nAfter reading, repeat the edit — it will go through. [applies-to-gate]",
    "applies_to.gate.header": "Lessons attached to this file (applies_to) — read BEFORE your first edit (shown once per session per file):\n",
    "applies_to.unparsed_hint": "Accepted forms: inline list `[\"a/b.py\", \"c/*.js\"]`, YAML list of `- ` items, or a single glob string `a/b.py`. An empty value means the binding was declared but never filled in.",
    "applies_to.unparsed_warning": "[memory] {filename}: `applies_to:` is set to `{value}` but no glob was parsed from it — this lesson will NEVER surface on file edits, and the engine cannot tell that apart from having no binding at all. {hint} [applies-to-unparsed]",
    "archive.precedent_block_header": "\n## {date_str} ({source_name})\n\n{block_text}\n",
    "archive.precedent_file_header": "# {keyword} — {year} Q{quarter}\n\n",
    "archive.precedent_pointer_line": "**{keyword} {date_str}:** {pointer} [{qrelative}]({qrelative}).",
    "archive.session_markers_file_header": "# Session-end markers — {year} Q{quarter}\n\n",
    "archive.session_markers_section_header": "\n## {today} — auto-archive of markers >{threshold_days}d\n\n",
    "bloat.core_over": "[memory] {core_file}: {size} {unit} ({pct}% of budget {budget} {unit}) — OVER the hot-core budget; move details/links to the catalog.",
    "bloat.core_warn": "[memory] {core_file}: {size} {unit} ({pct}% of budget {budget} {unit}) — approaching the hot-core limit; move lesson links to the catalog.",
    "bloat.lesson_over": "[memory] {filename}: {size} {unit} (> {limit} {unit}) — large lesson file, consider splitting it.",
    "bloat.precedent_count": "[memory] {filename}: {count} original 'Precedent YYYY-MM-DD' blocks — auto-archive only handles >{days}d; compress the rest manually.",
    "bloat.empty_name": "[memory] {filename}: empty name (blank title) — weakens retrieval (name is weighted x2); restore a descriptive title now (raw edit if the edit tool re-blanks it).",
    "catalog.auto_index_note": "<!-- Index below is built automatically by catalog_generate (updated {today}, {count} lessons). Edits inside the markers will be overwritten — change `topic:`/`description:` in the lesson frontmatter. -->",
    "catalog.written": "{catalog_file} written ({count} lessons).",
    "compact.core_over": "[memory] before compact: {core_file} {size} {unit} ({pct}% of budget {budget} {unit}) — good moment to trim the hot core.",
    "diag.broken_link_item": "      - {src} → {tgt}",
    "diag.broken_links_count": "  broken links: {count}",
    "diag.header": "INDEX DIAGNOSTICS:",
    "diag.no_description": "  without description: {count}",
    "diag.no_frontmatter": "  without frontmatter: {count}",
    "diag.no_name": "  without name (title): {count}",
    "diag.no_name_item": "      - {f}",
    "diag.no_topic_count": "  without topic: {count}",
    "diag.no_topic_item": "      - {f}",
    "diag.oversize_count": "  oversized (>{oversize_bytes}B): {count}",
    "diag.separator": "============================================================",
    "diag.total": "  lessons total: {count}",
    "health.broken_links": "{bl} broken cross-lesson links",
    "health.broken_wikilinks": "{wbl} broken [[wiki]] cross-lesson links",
    "health.many_lessons": "{total} lessons (≥{limit}) — consider checking for duplicates (exact repeats only; do NOT generalize/merge — that loses detail)",
    "health.no_name": "{nn} lessons without a name/title (empty `name:` weakens retrieval — restore the title)",
    "health.no_topic": "{nt} lessons without a topic (⚠ section of the index)",
    "health.oversize": "{osz} oversized >{oversize_kb}K",
    "health.pulse_prefix": "[memory: health] ",
    "health.pulse_suffix": ". Details: python3 -m claude_memory.catalog_generate 2>&1",
    "installer.done": "settings.json: {added} engine hook(s) added (duplicates skipped).",
    "installer.usage": "usage: python3 -m claude_memory.installer <settings.json> <abs/cme_hook.sh>",
    "marker.violation_multiline": "spans multiple lines",
    "marker.violation_reason": "Session marker violates file format: ONE line ≤{limit} characters (yours: {actual_length_or_multiline}). Shorten the marker to the essential one-liner (`<!-- YYYY-MM-DD <hash> #tag — summary -->`), put the full session breakdown in drafts/<session>.md or archive/ and link to it. A retry with the corrected marker will pass. [session-marker-guard]",
    "model_guard.no_model_reason": "Sub-agent (type {subagent_type}) launched without specifying `model` — it will inherit the main-thread model (the most expensive). Tier rule: delegate bulk read/search/mechanical work to a cheap model, judgment tasks to a mid-tier model, sensitive delegated reviews to the upper delegable tier; keep the strongest model for the critical path and final decisions. Re-issue the call with an explicit `model:` — or repeat as-is if the strongest model is genuinely needed here (the repeat will pass). Reminder fires once per session. [subagent-model-guard]",
    "model_guard.strongest_model_reason": "Routine sub-agent (type {subagent_type}) launched on the STRONGEST model (`{model}`) — that is the main-thread tier; for a delegated call it is almost always overspend. If the strongest model is genuinely needed here, repeat the call as-is (it will pass); otherwise re-issue with the appropriate tier from the scheme. Reminder fires once per session. [subagent-model-guard]",
    "model_registry.unknown_model": "[model-registry] This session runs on model `{model}`, which is not in the known model registry — a new generation may have shipped or an id changed. Review and update `known_model_substrs` / `strongest_model_substr` in the memory config (and the tier guidance in your project docs), then bump `model_registry_verified_on`.",
    "model_registry.stale": "[model-registry] The model registry has not been re-verified for {days} days (since {date}). A model may have been deactivated (e.g. a retired generation) or a new one shipped. Re-check the lineup, update `strongest_model_substr` / `known_model_substrs` if needed, then set `model_registry_verified_on` to today to clear this reminder.",
    "llm_actuality.verify_ask": "[llm-actuality] The model lineup has not been verified for ≥{interval}h. Delegate a quick check to the cheapest model (e.g. Haiku) with web search: are the known families still current ({families}) — any new or deactivated model? Then record it: run `cme_hook.sh llm-verified` (no change) or `cme_hook.sh llm-changes \"<what changed>\" --families <a,b,c>`. If something changed, show the user so they pick which models to use.",
    "llm_actuality.checklist_verified": "LLM actuality: verified {ts}",
    "llm_actuality.checklist_changes": "⚠ LLM actuality: changes at {ts} — {note}",
    "llm_actuality.checklist_never": "LLM actuality: not verified yet — run a lineup check",
    "precedent.cli_usage": "usage: python3 -m claude_memory.precedent_index --index <archive> [--write] | --extract <archive> <query> | --add-header <archive>",
    "precedent.extract_not_found": "(no card found for query: {query!r})",
    "precedent.header_written": "Warning header written to {filename} (idempotent).",
    "precedent.index_preamble": "Addressable pointer to [{archive_name}]({archive_name}) ({card_count} cards). Do NOT read the archive in full — find the card here, then retrieve one card with `{extract_cmd}`.",
    "precedent.index_title": "# Precedent Index — {archive_name}",
    "precedent.index_written": "Index written: {filename} ({card_count} cards).",
    "precedent.unknown_mode": "unknown mode; see usage (run without arguments)",
    "precedent.warn_header": "> ⚠ **DO NOT READ IN FULL** — this file grows append-only (by end of quarter: hundreds of KB = tens of thousands of tokens, will overflow working memory).\n> Navigation: index is alongside — `*-INDEX.md` (date · topic · derived lessons). Retrieve one card without reading the whole file:\n> `{extract_cmd}`.\n",
    "retrieve.hook_header": "[memory:retrieve] Possibly relevant lessons — read the ones you need BEFORE acting (full list: CATALOG):",
    "retrieve.hook_keyword_item": "    - {b}: {d}",
    "retrieve.hook_path_item": "    - {fn}: {d}",
    "retrieve.hook_section_keyword": "  • by meaning (keyword):",
    "retrieve.hook_section_path": "  • by file path in query (applies_to):",
    "retrieve.usage": "usage: python3 -m claude_memory.memory_retrieve \"<query>\"",
    "self_check.bad_placeholder": "[config self-check] messages override `{msg_key}` uses placeholder(s) {extras} absent from the library default — the text may render wrong/in English (msg() degrades to the default, so nothing crashes). Fix the override to valid placeholders.",
    "self_check.ok": "config self-check: OK (all message overrides use valid placeholders).",
    "retrieve.verbose_keyword_item": "{s:5} | {b}\n        {d}",
    "retrieve.verbose_no_matches": "   (no matches)",
    "retrieve.verbose_path_item": "   * {fn}\n     {d}",
    "retrieve.verbose_query_label": "Query: {query}",
    "retrieve.verbose_section_keyword": "By meaning (keyword+IDF), top {top_n}:",
    "retrieve.verbose_section_path": "By file path (applies_to — high precision):",
    "archive_prune.apply_hint": "Re-run with --apply to back up (to _deleted/<date>/) and delete.",
    "archive_prune.deleted": "Deleted {count} archived lesson(s); backups in {backup_dir}/<date>/.",
    "archive_prune.deleted_item": "  - {name}",
    "archive_prune.list_header": "{count} archived lesson(s) past retention (dry-run — nothing deleted):",
    "archive_prune.list_item": "  - {name} (archived {d}, ~{months} mo)",
    "archive_prune.none": "No archived lessons past their retention period.",
    "staleness.pending_file.archive_hint": "Memory is not version-controlled — deletion is permanent. Keep longer: raise archive_stale_months; disable: set it to 0. Safe delete with backup: python3 -m claude_memory.archive_prune --apply",
    "staleness.pending_file.archive_item": "- **{name}** (archived {d}, ~{months} mo) — {desc}",
    "staleness.pending_file.archive_section_header": "## Archived lessons past retention (review; delete only if truly obsolete)",
    "staleness.pending_file.broken_hint": "Fix the glob in the lesson frontmatter to the current path (or remove the binding).",
    "staleness.pending_file.broken_item": "- **{name}**: {dead}",
    "staleness.pending_file.broken_section_header": "## Broken applies_to bindings (path not found — file moved/renamed?)",
    "staleness.pending_file.header": "# Memory — re-verification required (pre-verify)",
    "staleness.pending_file.preamble": "Generated {date} at SessionEnd. Verify against live code/config BEFORE applying.",
    "staleness.pending_file.stale_item": "- **{name}** (reverify_after {d}) — {desc}",
    "staleness.pending_file.stale_section_header": "## Stale rules (reverify_after < today)",
    "staleness.pending_file.reconcile_section_header": "## Re-verify for staleness (lessons attached to files changed last session, not updated)",
    "staleness.pending_file.reconcile_item": "- **{lesson}** (changed: {files}) — did last session's change make it false? Fix / mark stale / replace.",
    "staleness.pending_file.unparsed_section_header": "## Unparsed applies_to (value set, no glob parsed — these lessons never surface on edits)",
    "staleness.pending_file.unparsed_item": "- **{name}**: `{value}`",
    "staleness.pending_file.unparsed_hint": "Accepted forms: inline list `[\"a/b.py\", \"c/*.js\"]`, YAML list of `- ` items, or a single glob string `a/b.py`. An empty value means the binding was declared but never filled in.",
    "stale_reconcile.item": "- {lesson} (attached to: {files})",
    "stale_reconcile.related_header": "Possibly related by meaning (no path binding — also re-check for staleness):",
    "stale_reconcile.related_item": "- {lesson} ({label})",
    "stale_reconcile.reminder": "Stop blocked (once): the commit closes task #{task_id}. Before finishing, re-verify lessons your changes this session may have made STALE. For EACH below: fix it, mark it stale, or replace it with a new lesson if a fix would rewrite the whole thing — or confirm it is still valid. Then finish; a repeat will pass.\nAttached to files you edited but did NOT update:\n{lessons}{related}\n[stale-reconcile-gate]",
    "stale_reconcile.checklist.header": "Memory — session close. This block is WORKING DATA (state BEFORE reconciliation) — do not paste it as-is. First act on every flagged lesson below (update / mark stale / replace / confirm still valid). THEN post the user a final memory report reflecting the RESULT: one line per flagged lesson with its outcome, plus the status lines below (shelf-life, LLM actuality, guards on/off) with their exact values:",
    "stale_reconcile.checklist.counts": "Lessons shown on edits: {shown} | reconciled: {reconciled} | remaining: {remaining}",
    "stale_reconcile.checklist.candidates_header": "⚠ Re-verify for staleness (you edited the file but did NOT update the lesson):",
    "stale_reconcile.checklist.clean": "✓ memory: no stale lessons to reconcile",
    "stale_reconcile.checklist.shelf_clean": "Shelf-life / archive: clean",
    "stale_reconcile.checklist.shelf_pending": "Shelf-life / archive: pending review (shown in _stale_pending.md at next start)",
    "stale_reconcile.checklist.guards_on": "Guards on: {guards}",
    "stale_reconcile.checklist.guards_off": "Guards off: {guards}",
    "stale_reconcile.checklist.directive": "For each lesson under \"Re-verify\": update it / mark it stale / replace it — or confirm it is still valid. Include every outcome in the report to the user.",
    "stale_reconcile.guard.stale_lessons": "stale-lessons",
    "stale_reconcile.guard.record_lessons": "record-lessons",
    "stale_reconcile.guard.task_close": "task-close",
    "stale_reconcile.guard.archive_age": "archive-age",
    "stale_reconcile.guard.lesson_count": "lesson-count",
    "stale_reconcile.guard.model_registry": "model-registry",
    "stop_check.closure_reminder": "Stop blocked: the commit closes task #{task_id}, but no lesson for it exists in memory. Write your task output as a lesson file (referencing #{task_id}) or an entry in the precedent archive, then finish. [task-close-gate]",
    "stop_check.reminder_message": "Stop blocked: there is a recent commit, but no lesson or note has been written to memory after it. Record your session output as a lesson file (or mark \"routine — no lesson\" in the lesson log), then finish — this will release the block. [stop-lessons]",
    "unit.bytes": "bytes",
    "unit.chars": "chars",
}


def _safe_format(template: str, params: dict) -> Optional[str]:
    """template.format(**params) или None, если в шаблоне битый/неизвестный плейсхолдер.

    Ловит KeyError (нет такого именованного поля), IndexError (позиционный {} без арга)
    и ValueError (битая фигурная скобка/спецификатор). None = «этот шаблон не подошёл»."""
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        return None


def msg(cfg, key: str, **params) -> str:
    """Операторское сообщение по ключу: override из cfg.messages → дефолт → сам ключ.

    Fail-soft на ДВУХ уровнях: (1) неизвестный ключ → возвращаем ключ; (2) битый
    плейсхолдер в override проекта (напр. `{len(cards)}` вместо `{card_count}`) НЕ роняет
    хук — деградируем на дефолт библиотеки (его плейсхолдеры заведомо верны), а если и он
    не форматируется — отдаём сырой шаблон. Иначе одна опечатка в конфиге тихо отключала
    бы целую функцию (хуки fail-open проглатывают исключение)."""
    overrides = getattr(cfg, "messages", None) if cfg is not None else None
    override = overrides.get(key) if overrides else None
    default = DEFAULT_MESSAGES.get(key)
    if override is not None:
        out = _safe_format(override, params)
        if out is not None:
            return out
        # битый плейсхолдер в override → не падаем, пробуем дефолт
    if default is not None:
        out = _safe_format(default, params)
        if out is not None:
            return out
    # последний рубеж: сырой шаблон без подстановки (виден текст) или сам ключ
    return override or default or key
