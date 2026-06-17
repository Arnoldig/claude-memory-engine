"""Офлайн-ретривер по памяти (слой «когда искать»).

Считает релевантность каждого урока к запросу по пересечению слов с его frontmatter
(`name` + `description` + `keywords`, высокий вес) и телом (низкий вес). Быстро,
офлайн, без LLM и без сети — ровно то, что может выполнить shell-хук UserPromptSubmit.

Стемминг — грубый префиксный (под падежи/спряжения): токены приводятся к нижнему
регистру и обрезаются до префикса. TF-IDF-подобный вес: редкие термины весят больше
частых, иначе общие слова топят специфичные уроки.

Режимы:
  memory_retrieve <запрос>      — verbose-ранжирование (ручной/тест).
  memory_retrieve --hook        — читает UserPromptSubmit-JSON из stdin, печатает
                                  релевантные уроки для инъекции (молчит, если < порога).

Все параметры (каталог памяти, отслеживаемые каталоги, пороги, стоп-слова) — из конфига.
Уроки по пути файла берутся из claude_memory.applies_to (один источник истины).
"""
from __future__ import annotations

import glob
import json
import math
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

from .applies_to import find_lessons_for_path
from .config import MemoryConfig, get_config


def _path_re(cfg: MemoryConfig) -> "re.Pattern[str]":
    """Регэксп path-подобных токенов в запросе («src/app.py», «docs/x.md»)."""
    dirs = "|".join(re.escape(d) for d in cfg.watched_dirs)
    return re.compile(
        rf"(?:{dirs})/[\w./*-]+"
        r"|[\w/-]+\.(?:py|html|js|ts|css|sh|ya?ml|json|md|rs|go|java|rb)\b"
    )


def tokenize(text: str, cfg: Optional[MemoryConfig] = None) -> set:
    cfg = cfg or get_config()
    stop = set(cfg.stopwords)
    raw = re.findall(r"[a-zA-Zа-яёА-ЯЁ0-9_]+", text.lower())
    stems = set()
    for w in raw:
        if len(w) < cfg.retrieve_min_token or w in stop:
            continue
        stems.add(w[: cfg.retrieve_stem])
    return stems


def read_fields(path: str, body_chars: int = 1500):
    """(name, description, keywords, body) для индексации.

    Поля frontmatter читаем на ЛЮБОМ отступе (движок памяти кладёт их под `metadata:`).
    Тело — текст после frontmatter, обрезанный до body_chars (топик-термины в начале)."""
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(4000)
    except OSError:
        return "", "", "", ""
    if not head.startswith("---"):
        return "", "", "", head[:body_chars]
    parts = head.split("\n---", 1)
    fm = parts[0]
    body = parts[1] if len(parts) > 1 else ""

    def field(k):
        m = re.search(rf"^[ \t]*{k}:\s*(.*)$", fm, re.MULTILINE)
        return m.group(1).strip().strip('"').strip("'") if m else ""

    return field("name"), field("description"), field("keywords"), body[:body_chars]


def score_files(query: str, cfg: Optional[MemoryConfig] = None):
    """TF-IDF-подобный скоринг по урокам в cfg.memory_dir. Возвращает [(score, file, label)]."""
    cfg = cfg or get_config()
    q = tokenize(query, cfg)
    if not q:
        return []
    skip = {cfg.core_file, cfg.catalog_file}
    docs = []
    df_title: Dict[str, int] = {}
    df_all: Dict[str, int] = {}
    for mf in glob.glob(os.path.join(cfg.memory_dir, "*.md")):
        base = os.path.basename(mf)
        if base in skip or base.startswith("_"):
            continue
        name, desc, kw, body = read_fields(mf, cfg.retrieve_body_chars)
        if not (name or desc or kw or body):
            continue
        nstems = tokenize(name, cfg) | tokenize(kw, cfg)  # имя + ключевые слова — высокий вес
        dstems = tokenize(desc, cfg)
        bstems = tokenize(body, cfg)
        docs.append((base, nstems, dstems, bstems, desc or name or base))
        title = nstems | dstems
        for s in title:
            df_title[s] = df_title.get(s, 0) + 1
        for s in title | bstems:
            df_all[s] = df_all.get(s, 0) + 1
    n = len(docs) or 1
    results = []
    for base, nstems, dstems, bstems, label in docs:
        score = 0.0
        for stem in q:
            if stem in nstems:
                score += 2.0 * math.log(1 + n / (1 + df_title.get(stem, 0)))
            elif stem in dstems:
                score += 1.0 * math.log(1 + n / (1 + df_title.get(stem, 0)))
            elif stem in bstems:
                score += 0.5 * math.log(1 + n / (1 + df_all.get(stem, 0)))
        if score > 0:
            results.append((round(score, 1), base, label))
    results.sort(key=lambda r: (-r[0], r[1]))
    return results


def path_lessons(query: str, cfg: Optional[MemoryConfig] = None) -> dict:
    """Уроки по путям файлов из запроса (applies_to). Высокая точность.

    Переиспользует claude_memory.applies_to (один источник истины) — без shell-вызова.
    """
    cfg = cfg or get_config()
    found: Dict[str, str] = {}
    for p in set(_path_re(cfg).findall(query)):
        for name, desc in find_lessons_for_path(p, cfg):
            found[name] = desc
    return found


def run(query: str, hook_mode: bool, cfg: Optional[MemoryConfig] = None) -> str:
    """Сформировать вывод (строкой) для запроса. Пустая строка = тишина (в hook-режиме)."""
    cfg = cfg or get_config()
    top_n = cfg.retrieve_top_n
    threshold = cfg.retrieve_threshold

    by_path = path_lessons(query, cfg)
    ranked = score_files(query, cfg)
    kw = [(s, b, d) for s, b, d in ranked if b not in by_path]

    if hook_mode:
        if not by_path and (not kw or kw[0][0] < threshold):
            return ""  # тишина — не шуметь на нерелевантных запросах
        out = ["[memory:retrieve] Возможно релевантные уроки — прочитай нужные ДО действий "
               "(полный список — CATALOG):"]
        if by_path:
            out.append("  • по пути файла в запросе (applies_to):")
            out += [f"    - {fn}: {d}" for fn, d in list(by_path.items())[:top_n]]
        if kw and kw[0][0] >= threshold:
            out.append("  • по смыслу (keyword):")
            out += [f"    - {b}: {d}" for _, b, d in kw[:top_n]]
        return "\n".join(out)

    lines = [f"Запрос: {query}", ""]
    if by_path:
        lines.append("По пути файла (applies_to — высокая точность):")
        for fn, d in by_path.items():
            lines.append(f"   * {fn}\n     {d[:140]}")
        lines.append("")
    lines.append(f"По смыслу (keyword+IDF), топ-{top_n}:")
    if not kw:
        lines.append("   (нет совпадений)")
    for s, b, d in kw[:top_n]:
        lines.append(f"{s:5} | {b}\n        {d[:140]}")
    return "\n".join(lines)


def main() -> None:
    args = sys.argv[1:]
    hook_mode = "--hook" in args
    if hook_mode:
        try:
            data = json.loads(sys.stdin.read() or "{}")
        except (ValueError, OSError):
            return
        query = data.get("prompt", "") or ""
    else:
        query = " ".join(a for a in args if a != "--hook")

    if not query.strip():
        if not hook_mode:
            print('usage: python3 -m claude_memory.memory_retrieve "<запрос>"')
        return

    out = run(query, hook_mode)
    if out:
        print(out)


if __name__ == "__main__":
    main()
