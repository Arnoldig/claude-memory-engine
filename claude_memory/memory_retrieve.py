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
import hashlib
import json
import math
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

from . import sqlite_index
from .applies_to import find_lessons_for_path, read_head
from .config import MemoryConfig, get_config
from .messages import msg


def _path_re(cfg: MemoryConfig) -> "re.Pattern[str]":
    """Регэксп path-подобных токенов в запросе («src/app.py», «docs/x.md», «.claude/hooks/x.sh»).

    Во второй ветви `[\\w./-]+` (с точкой) — чтобы ловить dotfile-пути с ведущей точкой
    (`.claude/…`, `.github/…`) и точки внутри пути (`claude-memory.config.json`); иначе
    высокоточный applies_to-канал ретривера слеп к путям движка памяти в `.claude/`."""
    dirs = "|".join(re.escape(d) for d in cfg.watched_dirs)
    exts = "|".join(re.escape(e) for e in cfg.retrieve_extensions)
    return re.compile(
        rf"(?:{dirs})/[\w./*-]+"
        rf"|[\w./-]+\.(?:{exts})\b"
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
    Тело — текст после frontmatter, обрезанный до body_chars (топик-термины в начале).
    Frontmatter читаем до закрывающей `---` без жёсткого окна (был лимит 4000 → длинный
    frontmatter молча терялся), с предохранителем 64К на гигантские файлы."""
    try:
        head = read_head(path)
    except OSError:
        return "", "", "", ""
    if not head.startswith("---"):
        return "", "", "", head[:body_chars]
    parts = head.split("\n---", 1)
    fm = parts[0]
    body = parts[1] if len(parts) > 1 else ""

    def field(k, top_level=False):
        # Ведущий якорь согласован с parse_frontmatter/applies_to/staleness: name и
        # description — ТОЛЬКО top-level (это поля первого уровня); keywords живут и под
        # `metadata:` → допускаем любой отступ. Иначе поиск «видел» бы description с
        # отступом, а указатель CATALOG — нет (рассинхрон половин системы).
        # `:[ \t]*` (не `:\s*`) — иначе пустое поле съедает `\n` и хватает следующую строку.
        anchor = "" if top_level else r"[ \t]*"
        m = re.search(rf"^{anchor}{k}:[ \t]*(.*)$", fm, re.MULTILINE)
        return m.group(1).strip().strip('"').strip("'") if m else ""

    return field("name", top_level=True), field("description", top_level=True), field("keywords"), body[:body_chars]


def _parse_doc(path: str, cfg: MemoryConfig):
    """Разбор ОДНОГО файла-урока в (is_empty, nstems, dstems, bstems, label).

    Единый источник истины разбора и для полного скана, и для SQLite-кэша — стемы в
    обоих путях считаются ТУТ, поэтому ранжирование идентично. `is_empty` повторяет
    отбраковку file-scan (`if not (name or desc or kw or body)`): пустые уроки в скоринг
    не идут, чтобы n и df совпадали 1:1 между кэшем и сканом.
    """
    name, desc, kw, body = read_fields(path, cfg.retrieve_body_chars)
    is_empty = not (name or desc or kw or body)
    nstems = tokenize(name, cfg) | tokenize(kw, cfg)  # имя + ключевые слова — высокий вес
    dstems = tokenize(desc, cfg)
    bstems = tokenize(body, cfg)
    label = desc or name or os.path.basename(path)
    return is_empty, nstems, dstems, bstems, label


def _params_fingerprint(cfg: MemoryConfig) -> str:
    """Отпечаток параметров токенизации. Смена любого → кэш стемов устарел → обнулить.

    Покрывает ровно то, что влияет на содержимое стемов: длину стема, минимальный
    токен, окно тела, набор стоп-слов. body_chars влияет на bstems (сколько тела
    индексируем). Не включает веса/IDF — они считаются по стемам на лету, кэш их не хранит.
    """
    payload = json.dumps(
        {
            "stem": cfg.retrieve_stem,
            "min_token": cfg.retrieve_min_token,
            "body_chars": cfg.retrieve_body_chars,
            "stopwords": sorted(cfg.stopwords),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _candidate_files(cfg: MemoryConfig) -> List[str]:
    """Пути файлов-уроков для скоринга: все *.md в memory_dir минус ядро/каталог/приватные."""
    skip = {cfg.core_file, cfg.catalog_file}
    out = []
    for mf in glob.glob(os.path.join(cfg.memory_dir, "*.md")):
        base = os.path.basename(mf)
        if base in skip or base.startswith(cfg.private_file_prefix):
            continue
        out.append(mf)
    return out


def _scan_docs(cfg: MemoryConfig, files: List[str]):
    """Полный file-scan (fallback, когда кэш недоступен): читает и разбирает каждый файл."""
    docs = []
    for mf in files:
        is_empty, nstems, dstems, bstems, label = _parse_doc(mf, cfg)
        if is_empty:
            continue
        docs.append((os.path.basename(mf), nstems, dstems, bstems, label))
    return docs


def score_files(query: str, cfg: Optional[MemoryConfig] = None):
    """TF-IDF-подобный скоринг по урокам в cfg.memory_dir. Возвращает [(score, file, label)].

    Сперва пробует SQLite-кэш (sqlite_index); если он недоступен/выключен/повреждён —
    откатывается на полный file-scan. Оба пути дают ОДИН и тот же набор docs (стемы из
    `_parse_doc`), поэтому df, n и итоговое ранжирование идентичны.
    """
    cfg = cfg or get_config()
    q = tokenize(query, cfg)
    if not q:
        return []
    files = _candidate_files(cfg)
    docs = sqlite_index.load_docs(
        cfg, files, _params_fingerprint(cfg), lambda p: _parse_doc(p, cfg)
    )
    if docs is None:  # кэш недоступен → полный скан (поведение 1:1 с прежним)
        docs = _scan_docs(cfg, files)

    df_title: Dict[str, int] = {}
    df_all: Dict[str, int] = {}
    for _base, nstems, dstems, bstems, _label in docs:
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
        out = [msg(cfg, "retrieve.hook_header")]
        if by_path:
            out.append(msg(cfg, "retrieve.hook_section_path"))
            out += [msg(cfg, "retrieve.hook_path_item", fn=fn, d=d)
                    for fn, d in list(by_path.items())[:top_n]]
        if kw and kw[0][0] >= threshold:
            out.append(msg(cfg, "retrieve.hook_section_keyword"))
            out += [msg(cfg, "retrieve.hook_keyword_item", b=b, d=d)
                    for _, b, d in kw[:top_n]]
        return "\n".join(out)

    lines = [msg(cfg, "retrieve.verbose_query_label", query=query), ""]
    if by_path:
        lines.append(msg(cfg, "retrieve.verbose_section_path"))
        for fn, d in by_path.items():
            lines.append(msg(cfg, "retrieve.verbose_path_item", fn=fn, d=d[:140]))
        lines.append("")
    lines.append(msg(cfg, "retrieve.verbose_section_keyword", top_n=top_n))
    if not kw:
        lines.append(msg(cfg, "retrieve.verbose_no_matches"))
    for s, b, d in kw[:top_n]:
        lines.append(msg(cfg, "retrieve.verbose_keyword_item", s=f"{s:5}", b=b, d=d[:140]))
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
            print(msg(get_config(), "retrieve.usage"))
        return

    out = run(query, hook_mode)
    if out:
        print(out)


if __name__ == "__main__":
    main()
