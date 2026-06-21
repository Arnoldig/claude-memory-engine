"""CLI: безопасное удаление архивных уроков с истёкшим сроком хранения.

Кандидаты = `staleness.scan_archive_stale` (архивные уроки с `archived_on` старше
`archive_stale_months`). Без `--apply` — только печать (dry-run). С `--apply` — каждый
файл СНАЧАЛА копируется в бэкап `<memory_dir>/_deleted/<сегодня>/` (вне всех глобов
движка → не всплывёт снова), ПОТОМ удаляется оригинал. Память не под git → удаление
необратимо, бэкап — единственная подстраховка; чистить `_deleted/` пользователь может сам.

Память сама себя не удаляет: эту команду запускает человек осознанно (а напоминание о
кандидатах приходит из `_stale_pending` на старте сессии). 0 токенов ИИ — чистый скрипт.
"""
from __future__ import annotations

import datetime
import shutil
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from .config import MemoryConfig, get_config
from .messages import msg
from . import staleness

# Бэкап удалённого — в `_`-каталоге верхнего уровня memory_dir: вне archive/** (скан
# хранения), вне `*.md` верхнего уровня (каталог/ретрив/applies_to) → не переоткроется.
BACKUP_DIR = "_deleted"


def prune(
    cfg: Optional[MemoryConfig] = None,
    apply: bool = False,
    today: Optional[datetime.date] = None,
) -> Tuple[List[Tuple[str, str, int, str]], List[str]]:
    """(кандидаты, удалённые). Без apply — удалённые пусты. С apply — бэкап ДО удаления."""
    cfg = cfg or get_config()
    today = today or datetime.date.today()
    cands = staleness.scan_archive_stale(cfg, today)
    if not apply or not cands:
        return cands, []
    arc_root = Path(cfg.memory_dir) / cfg.archive_dir_name
    backup_root = Path(cfg.memory_dir) / BACKUP_DIR / today.isoformat()
    deleted: List[str] = []
    for _d, name, _months, _desc in cands:
        matches = list(arc_root.rglob(name))
        if not matches:
            continue
        src = matches[0]
        backup_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, backup_root / name)
        src.unlink()
        deleted.append(name)
    return cands, deleted


def main() -> None:
    cfg = get_config()
    apply = "--apply" in sys.argv[1:]
    cands, deleted = prune(cfg, apply=apply)
    if not cands:
        print(msg(cfg, "archive_prune.none"))
        return
    if not apply:
        print(msg(cfg, "archive_prune.list_header", count=len(cands)))
        for d, name, months, _desc in cands:
            print(msg(cfg, "archive_prune.list_item", name=name, d=d, months=months))
        print(msg(cfg, "archive_prune.apply_hint"))
    else:
        print(msg(cfg, "archive_prune.deleted", count=len(deleted), backup_dir=BACKUP_DIR))
        for name in deleted:
            print(msg(cfg, "archive_prune.deleted_item", name=name))


if __name__ == "__main__":
    main()
