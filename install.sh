#!/usr/bin/env bash
# claude-memory-engine — install the memory engine into a project (variant A: git + install.sh).
#
# What it does (touches nothing of yours, fully idempotent):
#   1. copies the engine package to   <project>/.claude/memory_engine/claude_memory/
#   2. copies the hooks wrapper to     <project>/.claude/hooks/cme_hook.sh
#   3. creates the config              <project>/.claude/claude-memory.config.json (if absent)
#   4. APPENDS the hook registrations to <project>/.claude/settings.json (yours are kept)
#   5. creates the memory directory if it does not exist yet
#
# Your lessons are NOT part of the engine and are NOT copied; keep them separately (private).
#
# Usage:
#   ./install.sh [PROJECT_DIR] [MEMORY_DIR]
#     PROJECT_DIR   target project root   (default: current directory)
#     MEMORY_DIR    lessons memory folder (default: ~/.claude/memory)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
fi

PROJECT_DIR="$(cd "${1:-$PWD}" && pwd)"
MEMORY_DIR_RAW="${2:-$HOME/.claude/memory}"
# normalize (create, then abspath)
mkdir -p "$MEMORY_DIR_RAW"
MEMORY_DIR="$(cd "$MEMORY_DIR_RAW" && pwd)"

CLAUDE_DIR="$PROJECT_DIR/.claude"
ENGINE_DEST="$CLAUDE_DIR/memory_engine"
HOOKS_DEST="$CLAUDE_DIR/hooks"
CONFIG_DEST="$CLAUDE_DIR/claude-memory.config.json"
SETTINGS="$CLAUDE_DIR/settings.json"

echo "claude-memory-engine → install"
echo "  project:  $PROJECT_DIR"
echo "  memory:   $MEMORY_DIR"
echo

# 1. движок (пакет) — переустанавливаем чисто (rsync-стиль: удаляем старую копию пакета)
mkdir -p "$ENGINE_DEST"
rm -rf "$ENGINE_DEST/claude_memory"
cp -R "$SCRIPT_DIR/claude_memory" "$ENGINE_DEST/claude_memory"
find "$ENGINE_DEST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
echo "✓ engine:    $ENGINE_DEST/claude_memory"

# 2. обёртка хуков
mkdir -p "$HOOKS_DEST"
cp "$SCRIPT_DIR/hooks/cme_hook.sh" "$HOOKS_DEST/cme_hook.sh"
chmod +x "$HOOKS_DEST/cme_hook.sh"
echo "✓ wrapper:   $HOOKS_DEST/cme_hook.sh"

# 3. конфиг (не затираем существующий)
if [[ -f "$CONFIG_DEST" ]]; then
  echo "• config already present, kept as is: $CONFIG_DEST"
else
  python3 - "$CONFIG_DEST" "$MEMORY_DIR" "$PROJECT_DIR" <<'PY'
import json, sys
dest, mem, proj = sys.argv[1], sys.argv[2], sys.argv[3]
# Minimal config: project paths. Everything else uses engine defaults (see examples/ for the full set).
cfg = {"memory_dir": mem, "project_root": proj}
with open(dest, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
  echo "✓ config:    $CONFIG_DEST (project paths; other options in examples/)"
fi

# 4. регистрация хуков в settings.json (идемпотентно, чужое сохраняется)
ADDED="$(PYTHONPATH="$ENGINE_DEST" python3 -m claude_memory.installer "$SETTINGS" "$HOOKS_DEST/cme_hook.sh")"
echo "✓ settings:  $ADDED"

# 5. проверка: движок импортируется и видит конфиг
echo
echo "install check:"
PYTHONPATH="$ENGINE_DEST" CLAUDE_MEMORY_CONFIG="$CONFIG_DEST" \
  python3 -c "from claude_memory.config import load; load()" \
  && echo "✓ engine imports, config loads" \
  || { echo "✗ engine does not import: check python3 on PATH"; exit 1; }

echo
echo "Done. Hooks activate from the NEXT Claude Code session in $PROJECT_DIR."
echo "To tune topics/thresholds, edit $CONFIG_DEST (options in examples/claude-memory.config.json)."
