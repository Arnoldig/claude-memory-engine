#!/usr/bin/env bash
# claude-memory-engine — установка движка памяти в проект (вариант A: git + install.sh).
#
# Что делает (ничего чужого не трогает, всё идемпотентно):
#   1. кладёт пакет движка в  <project>/.claude/memory_engine/claude_memory/
#   2. кладёт обёртку хуков в  <project>/.claude/hooks/cme_hook.sh
#   3. создаёт конфиг          <project>/.claude/claude-memory.config.json (если нет)
#   4. ДОПИСЫВАЕТ регистрацию хуков в <project>/.claude/settings.json (чужие сохраняются)
#   5. создаёт каталог памяти, если его ещё нет
#
# Сами уроки памяти НЕ входят в движок и НЕ копируются — храните их отдельно (приватно).
#
# Usage:
#   ./install.sh [PROJECT_DIR] [MEMORY_DIR]
#     PROJECT_DIR  — корень целевого проекта (по умолчанию: текущий каталог)
#     MEMORY_DIR   — каталог уроков памяти   (по умолчанию: ~/.claude/memory)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
fi

PROJECT_DIR="$(cd "${1:-$PWD}" && pwd)"
MEMORY_DIR_RAW="${2:-$HOME/.claude/memory}"
# нормализуем (создадим, затем abspath)
mkdir -p "$MEMORY_DIR_RAW"
MEMORY_DIR="$(cd "$MEMORY_DIR_RAW" && pwd)"

CLAUDE_DIR="$PROJECT_DIR/.claude"
ENGINE_DEST="$CLAUDE_DIR/memory_engine"
HOOKS_DEST="$CLAUDE_DIR/hooks"
CONFIG_DEST="$CLAUDE_DIR/claude-memory.config.json"
SETTINGS="$CLAUDE_DIR/settings.json"

echo "claude-memory-engine → установка"
echo "  проект:  $PROJECT_DIR"
echo "  память:  $MEMORY_DIR"
echo

# 1. движок (пакет) — переустанавливаем чисто (rsync-стиль: удаляем старую копию пакета)
mkdir -p "$ENGINE_DEST"
rm -rf "$ENGINE_DEST/claude_memory"
cp -R "$SCRIPT_DIR/claude_memory" "$ENGINE_DEST/claude_memory"
find "$ENGINE_DEST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
echo "✓ движок:   $ENGINE_DEST/claude_memory"

# 2. обёртка хуков
mkdir -p "$HOOKS_DEST"
cp "$SCRIPT_DIR/hooks/cme_hook.sh" "$HOOKS_DEST/cme_hook.sh"
chmod +x "$HOOKS_DEST/cme_hook.sh"
echo "✓ обёртка:  $HOOKS_DEST/cme_hook.sh"

# 3. конфиг (не затираем существующий)
if [[ -f "$CONFIG_DEST" ]]; then
  echo "• конфиг уже есть, оставляю как есть: $CONFIG_DEST"
else
  python3 - "$CONFIG_DEST" "$MEMORY_DIR" "$PROJECT_DIR" <<'PY'
import json, sys
dest, mem, proj = sys.argv[1], sys.argv[2], sys.argv[3]
# Минимальный конфиг: пути проекта. Остальное — дефолты движка (см. examples/ для полного набора).
cfg = {"memory_dir": mem, "project_root": proj}
with open(dest, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
  echo "✓ конфиг:   $CONFIG_DEST (пути проекта; прочие опции — см. examples/)"
fi

# 4. регистрация хуков в settings.json (идемпотентно, чужое сохраняется)
ADDED="$(PYTHONPATH="$ENGINE_DEST" python3 -m claude_memory.installer "$SETTINGS" "$HOOKS_DEST/cme_hook.sh")"
echo "✓ settings: $ADDED"

# 5. проверка: движок импортируется и видит конфиг
echo
echo "проверка установки:"
PYTHONPATH="$ENGINE_DEST" CLAUDE_MEMORY_CONFIG="$CONFIG_DEST" \
  python3 -c "from claude_memory.config import load; load()" \
  && echo "✓ движок импортируется, конфиг читается" \
  || { echo "✗ движок не импортируется — проверьте python3 на PATH"; exit 1; }

echo
echo "Готово. Хуки активируются со СЛЕДУЮЩЕЙ сессии Claude Code в $PROJECT_DIR."
echo "Настроить таксономию тем/пороги — отредактируйте $CONFIG_DEST (опции в examples/claude-memory.config.json)."
