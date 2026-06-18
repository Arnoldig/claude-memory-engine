"""SQLite-кэш разобранных токенов уроков — слой ускорения офлайн-ретривера.

Источник истины — markdown-файлы уроков. Этот модуль лишь КЭШИРУЕТ результат их
разбора (наборы стемов + label), чтобы `memory_retrieve.score_files` не перечитывал и
не токенизировал весь корпус на каждый запрос (хук на КАЖДЫЙ промпт). Прирост растёт
с числом уроков: file-scan линеен, кэш обрезает именно растущую часть.

Свежесть проверяется ПРИ ЧТЕНИИ (а не по расписанию): на каждый поиск для каждого
файла сравниваем (`st_mtime_ns`, `st_size`) с сохранёнными — операция `stat`, файлы НЕ
читаются. Совпало → берём готовую выжимку; изменилось/новый → пере-разбираем ТОЛЬКО
этот файл и обновляем строку; файл исчез → удаляем строку. Точность ~100% в точке
использования: кэш не доверяет себе вслепую и ловит правку любым способом (ассистент,
скрипт, git). Сравнение целочисленное (наносекунды + байты), без капканов float.

Кэш АГНОСТИЧЕН к формуле скоринга: вызывающий передаёт `parse_fn` (как разобрать файл
в стемы/label) и `fingerprint` (отпечаток параметров токенизации). Смена fingerprint
обнуляет кэш — стемы пересчитываются под новые параметры. Так формула весов остаётся в
одном месте (`memory_retrieve`), а этот модуль переиспользуем и тестируется отдельно.

Параллельные сессии: WAL (много читателей + один писатель) + busy-timeout (ждать, не
падать). Запись редка (только когда файл реально изменился) и идемпотентна (две сессии
разберут один файл одинаково). Любая `sqlite3.Error`/`OSError` → `load_docs` вернёт
None, и вызывающий сделает обычный file-scan: КЭШ НИКОГДА НЕ ЛОМАЕТ ПОИСК (fail-open).

База — приватный `_*`-файл в memory_dir. Это не `.md` → она и её WAL-спутники
(`-wal`/`-shm`) вне всех глобов движка (ретривер/каталог/applies_to/staleness глобят
только `*.md`). CAS-страж `memory_concurrency` её не трогает: файл не правится
инструментами Edit/Write — конкуренцию держит сам SQLite.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Callable, List, Optional, Set, Tuple

from .config import MemoryConfig

# Версия СХЕМЫ таблиц (не данных). Меняем при изменении набора колонок → старая база
# дропается и пересоздаётся. Несовместимость данных под другие параметры токенизации
# ловится отдельно — через fingerprint (см. ensure_schema).
SCHEMA_VERSION = 1

# parse_fn(path) -> (is_empty, nstems, dstems, bstems, label)
ParseResult = Tuple[bool, Set[str], Set[str], Set[str], str]
ParseFn = Callable[[str], ParseResult]
# Документ для скоринга: (имя_файла, стемы_name+kw, стемы_desc, стемы_body, label)
Doc = Tuple[str, Set[str], Set[str], Set[str], str]


def _serialize(stems: Set[str]) -> str:
    """Стемы → строка через пробел. Стем не содержит пробелов (tokenize выдаёт
    `[a-zA-Zа-яё0-9_]`-токены), поэтому пробел-джойн обратим простым split() — дешевле
    JSON. Сортировка делает строку детерминированной (стабильный diff при отладке)."""
    return " ".join(sorted(stems))


def _deserialize(blob: str) -> Set[str]:
    return set(blob.split())


def _connect(cfg: MemoryConfig) -> sqlite3.Connection:
    """Соединение с кэш-БД в memory_dir. timeout = busy-timeout для параллельных сессий
    (ждать освобождения блокировки записи, а не падать с 'database is locked')."""
    db_path = os.path.join(cfg.memory_dir, cfg.retrieve_cache_file)
    # sqlite3.connect(timeout=...) ждёт в СЕКУНДАХ → переводим мс конфига в секунды.
    timeout_s = max(0, int(cfg.retrieve_cache_busy_timeout_ms)) / 1000.0
    conn = sqlite3.connect(db_path, timeout=timeout_s)
    # WAL: конкурентные читатели не блокируют писателя и наоборот. Если ФС не
    # поддерживает WAL — pragma тихо вернёт другой режим (не исключение), конкуренцию
    # тогда держит busy-timeout поверх rollback-журнала. synchronous=NORMAL безопасен
    # при WAL и быстрее: кэш восстановим из файлов, потеря последней транзакции некритична.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_schema(conn: sqlite3.Connection, fingerprint: str) -> None:
    """Создать схему; обнулить кэш при смене версии схемы ИЛИ параметров токенизации.

    Версия схемы не та (или базы нет) → DROP+пересоздать (колонки могли поменяться).
    Версия та, но fingerprint параметров другой → данные устарели под новые параметры
    (другой стем/стоп-слова/длина тела) → DELETE строк, структуру сохраняем.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None or row[0] != str(SCHEMA_VERSION):
        conn.execute("DROP TABLE IF EXISTS lessons")
        conn.execute(
            "CREATE TABLE lessons ("
            "base TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL, "
            "is_empty INTEGER NOT NULL, nstems TEXT NOT NULL, dstems TEXT NOT NULL, "
            "bstems TEXT NOT NULL, label TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('params_fingerprint', ?)",
            (fingerprint,),
        )
        return
    fp = conn.execute("SELECT value FROM meta WHERE key='params_fingerprint'").fetchone()
    if fp is None or fp[0] != fingerprint:
        conn.execute("DELETE FROM lessons")
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('params_fingerprint', ?)",
            (fingerprint,),
        )


def load_docs(
    cfg: MemoryConfig,
    files: List[str],
    fingerprint: str,
    parse_fn: ParseFn,
) -> Optional[List[Doc]]:
    """Вернуть документы для скоринга через кэш, либо None (тогда вызывающий делает
    обычный file-scan).

    files       — абсолютные пути к файлам-урокам (уже отфильтрованы вызывающим:
                  исключены ядро/каталог/приватные).
    fingerprint — отпечаток параметров токенизации; при его смене кэш обнуляется.
    parse_fn    — как разобрать ОДИН файл в (is_empty, nstems, dstems, bstems, label).

    Возвращает [(base, nstems, dstems, bstems, label)] ТОЛЬКО для непустых уроков
    (is_empty=False) — ровно те, что попадают в скоринг у file-scan, чтобы n и df
    совпали 1:1. None — кэш недоступен/выключен/повреждён (fail-open).
    """
    if not cfg.retrieve_cache_enabled:
        return None  # киллсвитч → вызывающий делает полный file-scan
    conn = None
    try:
        conn = _connect(cfg)
        ensure_schema(conn, fingerprint)
        conn.commit()  # зафиксировать создание/обнуление схемы до чтения

        cached = {}
        for base, mtime_ns, size, is_empty, n, d, b, label in conn.execute(
            "SELECT base, mtime_ns, size, is_empty, nstems, dstems, bstems, label FROM lessons"
        ):
            cached[base] = (mtime_ns, size, is_empty, n, d, b, label)

        docs: List[Doc] = []
        upserts = []
        seen: Set[str] = set()
        for path in files:
            base = os.path.basename(path)
            seen.add(base)
            try:
                st = os.stat(path)
            except OSError:
                # файл исчез между glob и stat → пропускаем (как read_fields OSError в file-scan)
                continue
            mtime_ns, size = st.st_mtime_ns, st.st_size
            row = cached.get(base)
            if row is not None and row[0] == mtime_ns and row[1] == size:
                is_empty, n, d, b, label = row[2], row[3], row[4], row[5], row[6]
                if not is_empty:
                    docs.append((base, _deserialize(n), _deserialize(d), _deserialize(b), label))
                continue
            # новый или изменённый файл → пере-разбираем ТОЛЬКО его
            is_empty, nstems, dstems, bstems, label = parse_fn(path)
            upserts.append((
                base, mtime_ns, size, 1 if is_empty else 0,
                _serialize(nstems), _serialize(dstems), _serialize(bstems), label,
            ))
            if not is_empty:
                docs.append((base, nstems, dstems, bstems, label))

        stale = [b for b in cached if b not in seen]
        if upserts or stale:
            with conn:  # одна транзакция на пакет апсертов+удалений (атомарно, быстро)
                if upserts:
                    conn.executemany(
                        "INSERT OR REPLACE INTO lessons "
                        "(base, mtime_ns, size, is_empty, nstems, dstems, bstems, label) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        upserts,
                    )
                if stale:
                    conn.executemany("DELETE FROM lessons WHERE base = ?", [(b,) for b in stale])
        return docs
    except (sqlite3.Error, OSError):
        return None  # любая беда с БД → молча откатываемся на file-scan
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
