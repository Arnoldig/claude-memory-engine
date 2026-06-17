#!/usr/bin/env bash
# claude-memory-engine — единая тонкая обёртка хуков. Вся логика — в
# claude_memory.hooks_cli; здесь только окружение и вызов.
#
# Регистрируется в settings.json как `bash <.claude>/hooks/cme_hook.sh <event>`.
# Каталог движка и конфиг резолвятся ОТНОСИТЕЛЬНО расположения этого файла —
# подстановка путей внутрь обёртки не нужна (переносимо между машинами).
set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$(cd "$HOOK_DIR/.." && pwd)"

export PYTHONPATH="$CLAUDE_DIR/memory_engine:${PYTHONPATH:-}"
export CLAUDE_MEMORY_CONFIG="${CLAUDE_MEMORY_CONFIG:-$CLAUDE_DIR/claude-memory.config.json}"

exec python3 -m claude_memory.hooks_cli "$@"
