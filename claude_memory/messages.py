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
    "frontmatter.unparsed_hint_applies_to": "Accepted forms: inline list `[\"a/b.py\", \"c/*.js\"]`, YAML list of `- ` items, or a single glob string `a/b.py`. An empty value means the binding was declared but never filled in. Until fixed, this lesson never surfaces on file edits.",
    "frontmatter.unparsed_hint_date": "Dates must be strict ISO `YYYY-MM-DD` (e.g. 2026-01-01) and nothing else on the line — a trailing ` # comment` is NOT supported in frontmatter scalars (put the reasoning in the lesson body). Until fixed, this field does nothing: a `reverify_after` never comes due, an `archived_on` never reaches retention.",
    "frontmatter.unparsed_warning": "[memory] {filename}: `{field}:` is set to `{value}` but the engine could not parse it — the field silently does nothing, and that is indistinguishable from not setting it at all. {hint} [frontmatter-unparsed]",
    "archive.precedent_block_header": "\n## {date_str} ({source_name})\n\n{block_text}\n",
    "archive.precedent_file_header": "# {keyword} — {year} Q{quarter}\n\n",
    "archive.precedent_pointer_line": "**{keyword} {date_str}:** {pointer} [{qrelative}]({qrelative}).",
    "archive.session_markers_file_header": "# Session-end markers — {year} Q{quarter}\n\n",
    "archive.session_markers_section_header": "\n## {today} — auto-archive of markers >{threshold_days}d\n\n",
    "bloat.core_over": "[memory] {core_file}: {size} {unit} ({pct}% of budget {budget} {unit}) — OVER the hot-core budget; move details/links to the catalog.",
    "bloat.core_warn": "[memory] {core_file}: {size} {unit} ({pct}% of budget {budget} {unit}) — approaching the hot-core limit; move lesson links to the catalog.",
    "bloat.instructions_large": "[memory] {filename} (project instructions): {size} {unit}, past the ~{budget} {unit} guideline. This is NOT a technical limit — Claude Code loads instruction files in full regardless of length and truncates nothing. The cost is behavioural: the more instructions share the context, the more of them are silently skipped, and a skipped rule is indistinguishable from a rule you never wrote. So do NOT trim down to the number. Keep every line whose omission is expensive (money, data loss, an irreversible action) even if the file stays above the guideline; move what merely saves a re-read into lessons or path-scoped rules. (The hard threshold, where content really is dropped, belongs to a different file: auto-memory — Claude Code loads only its first 25KB, or its first 200 lines, whichever comes first.) [instructions-size]",
    "bloat.lesson_over": "[memory] {filename}: {size} {unit} (> {limit} {unit}) — large lesson file, consider splitting it.",
    "bloat.precedent_legacy_keyword": "[memory] {filename}: cards use the pre-0.11.0 Russian keyword (`**Прецедент YYYY-MM-DD`), but `precedent_keyword` is left at the library default (`Precedent`) — so auto-archiving no longer sees them and the live-card counter reads zero, silently. This project relied on the old default. Fix: add to your config — `\"precedent_keyword\": \"Прецедент\", \"precedent_pointer\": \"перенесён в\"`. [memory]\n",
    "bloat.precedent_count": "[memory] {filename}: {count} original 'Precedent YYYY-MM-DD' blocks — auto-archive only handles >{days}d; compress the rest manually.",
    "bloat.description_long": "[memory] {filename}: `description` is {size} chars (> {limit}) — it is the SUMMARY, and the edit gate and retriever print it in full, so oversized ones become a wall of text that stops being read. Do not shorten by dropping substance: move the detail into the lesson body and leave a summary here. If it covers two different topics, that is two lessons — split the file.",
    "bloat.empty_name": "[memory] {filename}: empty name (blank title) — weakens retrieval (name is weighted x2); restore a descriptive title now (raw edit if the edit tool re-blanks it).",
    "catalog.auto_index_note": "<!-- Index below is built automatically by catalog_generate (updated {today}, {count} lessons). Edits inside the markers will be overwritten — change `topic:`/`description:` in the lesson frontmatter. -->",
    "catalog.unknown_topic_note": " ⟵ `topic: {topic}` is not in the configured topic list — fix the slug in the lesson, or add the topic to `topic_order` in the memory config.",
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
    "diag.unknown_topic_count": "  topic set but not in the configured topic list: {count}",
    "diag.unknown_topic_item": "      - {f} (topic: {topic})",
    "diag.oversize_count": "  oversized (>{oversize_bytes}B): {count}",
    "diag.separator": "============================================================",
    "diag.total": "  lessons total: {count}",
    "health.broken_links": "{bl} broken cross-lesson links",
    "health.broken_wikilinks": "{wbl} broken [[wiki]] cross-lesson links",
    "health.many_lessons": "{total} lessons (≥{limit}) — consider checking for duplicates (exact repeats only; do NOT generalize/merge — that loses detail)",
    "health.no_name": "{nn} lessons without a name/title (empty `name:` weakens retrieval — restore the title)",
    "health.no_topic": "{nt} lessons without a topic (⚠ section of the index)",
    "health.unknown_topic": "{ut} lessons whose topic is not in the configured list ({topics}) — they land in the ⚠ section although the field IS set; fix the slug or add the topic to `topic_order`",
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
    "self_check.bad_date": "[config self-check] `{field}` is set to `{value}`, which is not a strict ISO date (`YYYY-MM-DD`) — the engine cannot read it, so the check that depends on this field is silently OFF, exactly as if you had never set it. Fix the value to `YYYY-MM-DD`.",
    "self_check.bad_regex": "[config self-check] `{field}` is not a valid regular expression ({error}) — the engine catches the error and treats it as 'no match', so this gate is silently OFF and looks the same as 'nothing found'. Fix the pattern.",
    "self_check.close_pattern_lag": "[config self-check] `{field}` does not recognise every documented form of a GitHub closing keyword — missing: {missing}. GitHub accepts all nine keywords in both spellings (`Closes #42` and `Closes: #42`), so a commit like `{example}` will close the task while the lesson gate stays silent, and 'did not recognise' is indistinguishable from 'no closure happened'. This usually means the pattern was copied from an older default and froze: the current library default covers all nine, so rebuild your additions on top of it.",
    "self_check.empty_topic_order": "[config self-check] `topic_order` is an empty list — the lessons index gets NO topic sections at all, and every lesson lands in the ⚠ 'no topic' bucket regardless of its `topic:` field. This looks exactly like 'no lessons have topics yet'. Either list your topics, or remove the key entirely to use the engine default.",
    "self_check.ok": "config self-check: OK (message overrides, key names, patterns and dates all valid).",
    "self_check.auto_memory_off": "[config self-check] Claude Code auto-memory is DISABLED ({scope}), but the engine's lesson gates are ON. Nobody can write lessons: the engine never creates them — it only reads, indexes and guards; the writer is Claude Code's auto-memory. The Stop gate will therefore block after every fresh commit and there is no way to satisfy it. Either re-enable auto-memory, or turn off `stop_lessons_enabled` / `task_close_lesson_gate`.",
    "self_check.memory_dir_mismatch": "[config self-check] `memory_dir` is `{memory_dir}`, but Claude Code writes its auto-memory to `{auto_dir}` (`autoMemoryDirectory` set in {scope}). Lessons are written by Claude Code, so the engine is reading a directory nobody writes to: the catalog stays empty, retrieval stays silent, and the Stop gate blocks after every commit with no way to satisfy it. Point `memory_dir` at `{auto_dir}`.",
    "self_check.memory_dir_empty_elsewhere": "[config self-check] `memory_dir` is `{memory_dir}` and the engine sees 0 lessons there — but Claude Code's auto-memory directory `{auto_dir}` holds {count}. Lessons are written by Claude Code, so the engine is almost certainly pointed at the wrong directory (engines installed before 0.10.0 defaulted to `~/.claude/memory`, which nobody writes to). Point `memory_dir` at `{auto_dir}`.",
    "self_check.memory_dir_divergent": "[config self-check] `memory_dir` is `{memory_dir}` and the engine sees {own_count} lesson(s) there — but Claude Code writes its auto-memory to `{auto_dir}` (confirmed by files on disk). Lessons are written by Claude Code, so every NEW lesson lands in a directory the engine never reads: what you see is a dead tail that will never grow, retrieval keeps serving stale lessons, and the Stop gate blocks after every commit with no way to satisfy it. This usually happens by itself — renaming or moving the repository directory changes the slug, so Claude Code starts writing next door. Point `memory_dir` at `{auto_dir}` (move the old lessons over if you want to keep them), or set `autoMemoryDirectory` explicitly if the split is deliberate.",
    "self_check.report_header": "config self-check report:",
    "self_check.report_memory_dir": "  engine memory_dir : {memory_dir}",
    "self_check.report_auto_dir": "  Claude Code memory: {auto_dir}{note}",
    "self_check.report_match": "  same directory    : {verdict}",
    "self_check.report_lessons": "  lessons visible   : {count}{types}",
    "self_check.report_messages_coverage": "  messages translated: {done} of {total} — the rest fall back to the English library default, silently and per key, so localized output ends up mixed (neighbouring lines of one checklist in different languages). Missing, e.g.: {sample}",
    "self_check.report_auto_off": "  auto-memory       : DISABLED ({scope}) — nobody writes lessons",
    "self_check.report_note_derived": "  (derived, not confirmed — no lessons found there)",
    "self_check.report_note_explicit": "  (set via autoMemoryDirectory in {scope})",
    "self_check.typo_key": "[config self-check] unknown config key `{key}`, very close to the known key `{near}` — a typo, or a key from a newer engine version? Unknown keys are dropped silently, so if this is a typo your setting is NOT in effect and the English default is being used instead. Rename it to `{near}`, or ignore this if the key is intentional.",
    "self_check.mistyped_key": "[config self-check] `{field}` holds a value of the wrong kind (a number where a list belongs, a string where a number belongs, and so on). The value was dropped and the built-in default is in force — the setting you wrote is NOT active. Fix the value's type in the config.",
    "self_check.unknown_keys_info": "[config self-check] other keys the engine does not know (dropped; fine if they target a newer version or another tool): {keys}",
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
    "staleness.pending_file.unparsed_section_header": "## Unparsed frontmatter values (field set, but not understood — it silently does nothing)",
    "staleness.pending_file.unparsed_item": "- **{name}** — `{field}: {value}`",
    "staleness.pending_file.unparsed_more": "- … and {count} more.",
    "staleness.pending_file.unparsed_hint": "Fix the value in the lesson frontmatter. `applies_to`: inline list `[\"a/b.py\"]`, YAML list of `- ` items, or a single glob string. Dates (`reverify_after`, `archived_on`): strict ISO `YYYY-MM-DD`. Trailing ` # comments` are not supported in frontmatter scalars — put the reasoning in the lesson body. An empty value means the field was declared but never filled in.",
    "stale_reconcile.item": "- {lesson} (attached to: {files})",
    "stale_reconcile.related_header": "Possibly related by meaning (no path binding — also re-check for staleness):",
    "stale_reconcile.related_item": "- {lesson} ({label})",
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
    "stale_reconcile.guard.task_close_watch": "task-close-watch",
    "stale_reconcile.guard.instructions_size": "instructions-size",
    "stale_reconcile.guard.archive_age": "archive-age",
    "stale_reconcile.guard.lesson_count": "lesson-count",
    "stale_reconcile.guard.model_registry": "model-registry",
    "stop_check.closure_reminder": "Stop blocked: the commit closes task #{task_id}, but no lesson for it exists in memory. Write your task output as a lesson file (referencing #{task_id}) or an entry in the precedent archive, then finish. [task-close-gate]",
    "issue_close.ack": "[task-close-watch] Noticed: you closed issue #{task_id} from the command line. No lesson referencing #{task_id} is in memory yet — I will ask for it before this session can finish. Write the outcome as a lesson (mention #{task_id} in it) whenever you are ready.",
    "issue_close.ack_unknown": "[task-close-watch] Noticed: you closed an issue from the command line, but I could not read its number. I will ask for a lesson before this session can finish.",
    "stop_check.closure_command_reminder": "Stop blocked: issue #{task_id} was closed with `gh issue close`, but no lesson for it exists in memory. Write your task output as a lesson file (referencing #{task_id}) or an entry in the precedent archive, then finish. If the issue was closed without code (duplicate, won't fix), say so in a lesson too — that is the record. [task-close-watch]",
    "stop_check.closure_command_reminder_unknown": "Stop blocked: an issue was closed with `gh issue close`, but no lesson has been written to memory since. Record what the task taught you as a lesson file, then finish. [task-close-watch]",
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
    # isinstance, а не просто truthy: `_coerce` типы не приводит, поэтому `"messages": []`
    # (или строка/число) из JSON доезжает сюда как есть, а `.get` у списка нет. Уронило бы
    # это НЕ одно сообщение, а всё, что печатает движок, — то есть описка в типе гасила бы
    # разом каждый хук, включая самодиагностику, которая должна была о ней сказать.
    override = overrides.get(key) if isinstance(overrides, dict) else None
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
