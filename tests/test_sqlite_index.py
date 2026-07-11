"""Тесты SQLite-кэша ретривера (sqlite_index) и его интеграции в score_files.

Главный инвариант: кэш даёт ТО ЖЕ ранжирование, что полный file-scan (иначе задача не
закрыта). Плюс: инвалидция по mtime+size при чтении, прунинг/добавление, обнуление при
смене параметров, параллельные сессии, fail-open при битой БД/выключенном кэше,
приватность файла-БД, и доказательство, что тёплый кэш не перечитывает файлы.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

from claude_memory import memory_retrieve as MR
from claude_memory import sqlite_index
from conftest import write_lesson


def _seed(cfg) -> None:
    """Небольшой разнородный корпус (имена/описания/тело + редкие и частые термины)."""
    write_lesson(cfg.memory_dir, "feedback_kafka.md", name="kafka smz", description="payment kafka pipeline")
    write_lesson(cfg.memory_dir, "feedback_card.md", name="payment card", description="payment visa mastercard")
    write_lesson(cfg.memory_dir, "feedback_sbp.md", name="payment sbp", description="payment fast system",
                 body="redis caching strategy for sbp transfers")
    write_lesson(cfg.memory_dir, "feedback_docker.md", name="docker compose", description="container orchestration",
                 body="kafka broker runs in docker compose for local testing")


def _disabled(cfg):
    """Тот же конфиг, но кэш выключен → чистый file-scan (эталон для сравнения)."""
    return replace(cfg, retrieve_cache_enabled=False)


def _cache_path(cfg) -> Path:
    return Path(cfg.memory_dir) / cfg.retrieve_cache_file


# ── Главный инвариант: кэш == file-scan ──────────────────────────────────────

def test_cache_matches_filescan_across_queries(cfg) -> None:
    _seed(cfg)
    queries = ["kafka", "payment", "docker compose", "redis sbp", "visa", "kafka payment docker", "zzz"]
    for q in queries:
        with_cache = MR.score_files(q, cfg)
        no_cache = MR.score_files(q, _disabled(cfg))
        assert with_cache == no_cache, f"divergence on query {q!r}: {with_cache} != {no_cache}"


def test_cache_is_actually_used_not_just_fallback(cfg) -> None:
    # Кэш-файл реально создаётся при включённом кэше и НЕ создаётся при выключенном.
    _seed(cfg)
    MR.score_files("kafka", cfg)
    assert _cache_path(cfg).is_file()


# ── Инвалидция при чтении (mtime+size) ───────────────────────────────────────

def test_invalidation_on_size_change(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_x.md", name="topicmarker", body="alpha")
    assert any(b == "feedback_x.md" for _, b, _ in MR.score_files("alpha", cfg))
    # переписать тело — другой размер → строка пере-разбирается при следующем чтении
    write_lesson(cfg.memory_dir, "feedback_x.md", name="topicmarker", body="omegaword different length")
    assert not MR.score_files("alpha", cfg), "stale body still matched — invalidation failed"
    assert any(b == "feedback_x.md" for _, b, _ in MR.score_files("omegaword", cfg))


def test_invalidation_on_mtime_only_same_size(cfg) -> None:
    p = write_lesson(cfg.memory_dir, "feedback_y.md", name="topicmarker", body="aaaaa")
    assert any(b == "feedback_y.md" for _, b, _ in MR.score_files("aaaaa", cfg))  # cache built
    # тот же размер (5 символов), другое содержимое, принудительно сдвинутый mtime
    write_lesson(cfg.memory_dir, "feedback_y.md", name="topicmarker", body="bbbbb")
    st = os.stat(p)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    assert not MR.score_files("aaaaa", cfg), "same-size edit not caught by mtime"
    assert any(b == "feedback_y.md" for _, b, _ in MR.score_files("bbbbb", cfg))


def test_prunes_deleted_lesson(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_keep.md", name="keepword", description="stable")
    write_lesson(cfg.memory_dir, "feedback_gone.md", name="goneword", description="temporary")
    assert any(b == "feedback_gone.md" for _, b, _ in MR.score_files("goneword", cfg))  # build
    os.remove(Path(cfg.memory_dir) / "feedback_gone.md")
    assert not MR.score_files("goneword", cfg)
    # строка удалена из БД (а не просто отфильтрована при выдаче)
    with sqlite3.connect(_cache_path(cfg)) as conn:
        bases = {r[0] for r in conn.execute("SELECT base FROM lessons")}
    assert "feedback_gone.md" not in bases
    assert "feedback_keep.md" in bases


def test_new_lesson_added_after_cache_built(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_first.md", name="firstword")
    MR.score_files("firstword", cfg)  # cache built with one lesson
    write_lesson(cfg.memory_dir, "feedback_second.md", name="secondword")
    assert any(b == "feedback_second.md" for _, b, _ in MR.score_files("secondword", cfg))
    # и эквивалентность сохраняется на расширенном корпусе
    assert MR.score_files("firstword secondword", cfg) == MR.score_files("firstword secondword", _disabled(cfg))


# ── Обнуление при смене параметров токенизации ───────────────────────────────

def test_fingerprint_change_rebuilds_cache(cfg) -> None:
    _seed(cfg)
    MR.score_files("kafka", cfg)  # cache built under default params
    # другой набор стоп-слов → fingerprint меняется → кэш обнуляется и пересчитывается
    cfg2 = replace(cfg, stopwords=cfg.stopwords + ("kafka",))
    assert MR.score_files("kafka payment", cfg2) == MR.score_files("kafka payment", _disabled(cfg2))
    # "kafka" теперь стоп-слово → не матчит
    assert not MR.score_files("kafka", cfg2)


def test_fingerprint_changes_with_each_relevant_param(cfg) -> None:
    fp0 = MR._params_fingerprint(cfg)
    assert MR._params_fingerprint(replace(cfg, retrieve_stem=7)) != fp0
    assert MR._params_fingerprint(replace(cfg, retrieve_min_token=4)) != fp0
    assert MR._params_fingerprint(replace(cfg, retrieve_body_chars=2000)) != fp0
    assert MR._params_fingerprint(replace(cfg, stopwords=("zzz",))) != fp0
    # порядок стоп-слов не важен (сортируем перед хешем)
    assert MR._params_fingerprint(replace(cfg, stopwords=("a", "b"))) == \
           MR._params_fingerprint(replace(cfg, stopwords=("b", "a")))


# ── Обнуление при смене ВЕРСИИ ЛОГИКИ парсера (не параметров) ─────────────────

def test_fingerprint_changes_with_parser_logic_version(cfg, monkeypatch) -> None:
    # Смена _PARSER_LOGIC_VERSION (правка кода разбора при тех же параметрах) обязана
    # менять отпечаток — иначе кэш не заметит не-1:1 правку парсера.
    fp0 = MR._params_fingerprint(cfg)
    monkeypatch.setattr(MR, "_PARSER_LOGIC_VERSION", MR._PARSER_LOGIC_VERSION + 1)
    assert MR._params_fingerprint(cfg) != fp0


def test_parser_logic_bump_invalidates_cache(cfg, monkeypatch) -> None:
    # Полный путь: холодный кэш под текущей версией логики → «правка логики» (bump) →
    # ensure_schema обязан DELETE строки и записать новый fingerprint (а не отдать стемы
    # старого парсера по совпавшим mtime/size). Это и есть замена ручного `rm` кэша.
    _seed(cfg)
    MR.score_files("kafka", cfg)  # cache built under current logic version
    with sqlite3.connect(_cache_path(cfg)) as conn:
        fp_before = conn.execute(
            "SELECT value FROM meta WHERE key='params_fingerprint'").fetchone()[0]
    monkeypatch.setattr(MR, "_PARSER_LOGIC_VERSION", MR._PARSER_LOGIC_VERSION + 1)
    MR.score_files("kafka", cfg)  # тёплый по mtime/size, но версия логики другая → пересбор
    with sqlite3.connect(_cache_path(cfg)) as conn:
        fp_after = conn.execute(
            "SELECT value FROM meta WHERE key='params_fingerprint'").fetchone()[0]
    assert fp_after != fp_before, "смена версии логики парсера не обнулила кэш"
    # после пересбора кэш остаётся эквивалентен чистому скану
    assert MR.score_files("kafka payment", cfg) == MR.score_files("kafka payment", _disabled(cfg))


# ── fail-open: выключенный кэш / битая БД ─────────────────────────────────────

def test_disabled_creates_no_db(cfg) -> None:
    _seed(cfg)
    MR.score_files("kafka", _disabled(cfg))
    assert not _cache_path(cfg).exists()


def test_corrupt_db_falls_back_to_filescan(cfg) -> None:
    _seed(cfg)
    _cache_path(cfg).write_bytes(b"this is not a sqlite database at all")
    # не должно бросить; результат должен совпасть с чистым сканом
    assert MR.score_files("kafka payment", cfg) == MR.score_files("kafka payment", _disabled(cfg))


def test_load_docs_returns_none_when_disabled(cfg) -> None:
    files = MR._candidate_files(cfg)
    out = sqlite_index.load_docs(_disabled(cfg), files, "fp", lambda p: MR._parse_doc(p, cfg))
    assert out is None


# ── Приватность файла-БД ──────────────────────────────────────────────────────

def test_cache_file_not_in_results_and_excluded_from_candidates(cfg) -> None:
    _seed(cfg)
    MR.score_files("kafka", cfg)  # creates _retrieve_cache.sqlite3 (+ maybe -wal/-shm)
    assert _cache_path(cfg).is_file()
    # ни кандидаты скоринга, ни выдача не содержат файла-БД
    cand = {os.path.basename(p) for p in MR._candidate_files(cfg)}
    assert not any("retrieve_cache" in b for b in cand)
    res_bases = {b for _, b, _ in MR.score_files("kafka payment docker redis", cfg)}
    assert not any("retrieve_cache" in b for b in res_bases)


# ── Пустой урок исключён точь-в-точь как в file-scan ─────────────────────────

def test_empty_lesson_excluded_identically(cfg) -> None:
    write_lesson(cfg.memory_dir, "feedback_real.md", name="realword", description="payment")
    # урок без name/desc/kw и без тела (frontmatter заканчивается ровно на ---)
    (Path(cfg.memory_dir) / "feedback_empty.md").write_text("---\nfoo: bar\n---", encoding="utf-8")
    assert MR.score_files("payment realword", cfg) == MR.score_files("payment realword", _disabled(cfg))
    # пустой урок не попадает в скоринг ни в одном пути
    assert not any(b == "feedback_empty.md" for _, b, _ in MR.score_files("payment realword", cfg))


# ── Тёплый кэш не перечитывает файлы (доказательство оптимизации) ────────────

def test_warm_cache_does_not_reparse(cfg, monkeypatch) -> None:
    _seed(cfg)
    calls = {"n": 0}
    real_read_fields = MR.read_fields

    def counting_read_fields(path, body_chars=1500):
        calls["n"] += 1
        return real_read_fields(path, body_chars)

    monkeypatch.setattr(MR, "read_fields", counting_read_fields)
    MR.score_files("kafka", cfg)            # холодный кэш — разбирает все файлы
    cold = calls["n"]
    assert cold >= 4, "cold cache should parse every candidate file"
    calls["n"] = 0
    MR.score_files("payment", cfg)          # тёплый кэш — файлы не менялись
    assert calls["n"] == 0, "warm cache re-parsed unchanged files"


# ── Параллельные сессии: WAL + busy-timeout, без повреждения ─────────────────

def test_concurrent_sessions_no_corruption(cfg) -> None:
    _seed(cfg)
    errors = []

    def worker(i):
        try:
            for _ in range(8):
                # каждый «сеанс» правит свой файл (write-contention) и ищет
                write_lesson(cfg.memory_dir, f"feedback_w{i}.md", name=f"worker{i}", body="payment kafka")
                MR.score_files("kafka payment docker", cfg)
        except Exception as e:  # noqa: BLE001 — любой сбой конкуренции фиксируем
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"concurrency errors: {errors}"
    # БД не повреждена и читаема; на финальном корпусе кэш == чистый скан (после
    # конкурентных записей кэш консистентен; df меняется одинаково для обоих путей).
    assert MR.score_files("kafka payment docker", cfg) == MR.score_files("kafka payment docker", _disabled(cfg))


# ── Версионирование схемы: смена SCHEMA_VERSION дропает и пересоздаёт таблицу ─

def test_schema_version_bump_drops_and_rebuilds(cfg, monkeypatch) -> None:
    _seed(cfg)
    MR.score_files("kafka", cfg)  # построить кэш под текущей версией схемы
    assert _cache_path(cfg).is_file()
    # эмулируем выход новой версии схемы: ensure_schema должен DROP+пересоздать lessons,
    # после чего поиск по тёплому (уже под новой версией) кэшу остаётся эквивалентен скану
    monkeypatch.setattr(sqlite_index, "SCHEMA_VERSION", sqlite_index.SCHEMA_VERSION + 1)
    assert MR.score_files("kafka payment", cfg) == MR.score_files("kafka payment", _disabled(cfg))
    with sqlite3.connect(_cache_path(cfg)) as conn:
        ver = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert ver and ver[0] == str(sqlite_index.SCHEMA_VERSION)
