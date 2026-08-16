"""Подсказка «высокий ярус поиска пуст для языка каталога» (заявка #31).

ЧТО СТЕРЕЖЁМ. Ранжирование складывает слова `name` и `keywords` в ОДИН набор веса ×2
(`memory_retrieve._parse_doc`). Встроенная авто-память Claude Code при создании файла
урока переписывает `name` в латинское имя, а `keywords` не трогает вовсе (замер
2026-08-16: русский заголовок стал `canonical`, заголовок без латинских букв стал пустым,
русские ключевые слова уцелели целиком). Значит на каталоге НЕ на английском ярус ×2
остаётся пустым до тех пор, пока автор не заполнит `keywords`, и запрос на языке проекта
не может совпасть с ним никогда — поиск молча съезжает на ярусы ×1 и ×0.5.

ОРАКУЛ ЗДЕСЬ — САМ ДВИЖОК, а не регэксп теста: ожидание подсказки считается через
`memory_retrieve.tokenize`, то есть через тот же токенизатор, которым ретривер строит
ярусы. Поэтому забытая форма записи поля не выпадает разом из кода и из теста: оракул
отвечает за код, а не повторяет его.

ФОРМЫ ПОЛЯ ПОРОЖДАЮТСЯ ОСЯМИ (размещение × значение × заголовок), а не пишутся по
памяти: список, писанный по памяти, неполон всегда. Размещение — отдельная ось потому,
что хост кладёт `keywords` ВНУТРЬ блока `metadata:`, и разбор, читающий только верхний
уровень, объявил бы пустыми все уроки самого ухоженного каталога (замер: 487 из 487).

У КАЖДОГО СРАБАТЫВАНИЯ ЕСТЬ ПАРНЫЙ СЛУЧАЙ ПРОПУСКА (нижняя половина файла): набор из
одних срабатываний зеленеет и на страже, который печатает жалобу всегда.
"""
from dataclasses import replace
from pathlib import Path

from claude_memory import hooks_cli as H
from claude_memory import memory_retrieve as mr
from claude_memory.messages import msg

RU_DESC = "Описание урока на языке проекта, целиком русскими словами"
LAT_DESC = "Lesson description written entirely in latin words"


def _nudged(out: str, base: str, cfg) -> bool:
    """Пришла ли ИМЕННО эта подсказка.

    Сверяем с готовой строкой самого движка, а не с именем файла: имя файла стоит и в
    соседней жалобе про пустой `name`, и проверка «имя в выводе» зеленела бы на ней.
    """
    return msg(cfg, "bloat.no_keywords", filename=base) in out


def _event(p: Path) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": str(p)}}


def _write(memory_dir: str, base: str, fm_lines, body: str = "тело урока") -> Path:
    p = Path(memory_dir) / base
    p.write_text("---\n" + "\n".join(fm_lines) + "\n---\n" + body + "\n", encoding="utf-8")
    return p


def _tier_is_dead(path: Path, cfg) -> bool:
    """Оракул: ярус ×2 не содержит НИ ОДНОГО слова языка описания.

    Считается токенизатором ретривера — тем же, что строит ярусы при ранжировании.
    """
    name, desc, kw, _body = mr.read_fields(str(path), cfg.retrieve_body_chars)
    high = mr.tokenize(name, cfg) | mr.tokenize(kw, cfg)
    mid = mr.tokenize(desc, cfg)
    non_latin = mr.has_indexable_non_latin
    return any(non_latin(s) for s in mid) and not any(non_latin(s) for s in high)


# ── оси: размещение × значение × заголовок ───────────────────────────────────

PLACEMENTS = [
    ("верхний уровень", lambda v: [f"keywords: {v}"]),
    ("под metadata", lambda v: ["metadata:", f"  keywords: {v}"]),
]
VALUES = [
    ("слово", "поиск"),
    ("в кавычках", '"поиск, память"'),
    ("инлайн-список", "[поиск, память]"),
    ("латиница", "search, memory"),
    ("пусто", ""),
    ('пустая строка ""', '""'),
]
NAMES = [
    ("латинский слаг", "some-latin-slug"),
    ("русский заголовок", "Русский заголовок урока"),
    ("пустой", ""),
]


def test_nudge_matches_the_engines_own_tiers_on_every_generated_form(cfg) -> None:
    """Подсказка приходит РОВНО тогда, когда ярус ×2 пуст по мерке самого ретривера."""
    n = 0
    for pi, (place_label, place) in enumerate(PLACEMENTS):
        for vi, (val_label, value) in enumerate(VALUES):
            for ni, (name_label, name) in enumerate(NAMES):
                base = f"feedback_axis_{pi}{vi}{ni}.md"
                p = _write(cfg.memory_dir, base,
                           [f"name: {name}", f"description: {RU_DESC}", "topic: testing"]
                           + place(value))
                out = H.ev_bloat_check(_event(p), cfg)
                expected = _tier_is_dead(p, cfg)
                assert _nudged(out, base, cfg) is expected, (
                    f"размещение={place_label}, значение={val_label}, заголовок={name_label}: "
                    f"подсказка {'ожидалась' if expected else 'НЕ ожидалась'}, получено {out!r}"
                )
                n += 1
    assert n == len(PLACEMENTS) * len(VALUES) * len(NAMES)


def test_nudge_fires_when_the_whole_high_tier_is_latin(cfg) -> None:
    """Опорная точка оси: латинский слаг + латинские ключевые слова + русское описание."""
    p = _write(cfg.memory_dir, "feedback_dead_tier.md",
               ["name: latin-slug-only", f"description: {RU_DESC}", "topic: testing",
                "metadata:", "  keywords: search, memory"])
    assert _nudged(H.ev_bloat_check(_event(p), cfg), "feedback_dead_tier.md", cfg)


def test_keywords_under_metadata_are_seen_as_filled(cfg) -> None:
    """Форма, которой пишет сам хост: `keywords` внутри блока `metadata`.

    Разбор, читающий только верхний уровень, объявил бы пустыми ВСЕ уроки боевого
    каталога (замер 2026-08-16: 487 из 487) — то есть максимальный шум ровно там, где
    всё сделано правильно.
    """
    p = _write(cfg.memory_dir, "feedback_nested_kw.md",
               ["name: latin-slug-only", f"description: {RU_DESC}", "topic: testing",
                "metadata:", "  keywords: поиск, память, ярус"])
    assert not _nudged(H.ev_bloat_check(_event(p), cfg), "feedback_nested_kw.md", cfg)


# ── парные случаи ПРОПУСКА: по одному на каждую ветвь хука ───────────────────

def test_silent_when_keywords_carry_the_projects_language(cfg) -> None:
    p = _write(cfg.memory_dir, "feedback_kw_ok.md",
               ["name: latin-slug-only", f"description: {RU_DESC}", "topic: testing",
                "keywords: поиск, память"])
    assert not _nudged(H.ev_bloat_check(_event(p), cfg), "feedback_kw_ok.md", cfg)


def test_silent_when_name_already_carries_the_projects_language(cfg) -> None:
    """Ярус ×2 НЕ пуст: слова языка проекта пришли из заголовка.

    Без этой ветви сообщение «ярус пуст» было бы ложным на 360 уроках боевого каталога.
    """
    p = _write(cfg.memory_dir, "feedback_ru_name.md",
               ["name: Русский заголовок урока", f"description: {RU_DESC}", "topic: testing"])
    assert not _nudged(H.ev_bloat_check(_event(p), cfg), "feedback_ru_name.md", cfg)


def test_silent_on_a_fully_latin_catalog(cfg) -> None:
    """Англоязычный каталог не задевается никогда: ярус ×2 у него рабочий."""
    p = _write(cfg.memory_dir, "feedback_latin.md",
               ["name: latin-slug-only", f"description: {LAT_DESC}", "topic: testing"],
               body="latin body text")
    assert not _nudged(H.ev_bloat_check(_event(p), cfg), "feedback_latin.md", cfg)


def test_silent_without_frontmatter(cfg) -> None:
    p = Path(cfg.memory_dir) / "feedback_no_fm.md"
    p.write_text("просто текст без frontmatter\n", encoding="utf-8")
    assert not _nudged(H.ev_bloat_check(_event(p), cfg), "feedback_no_fm.md", cfg)


def test_silent_on_a_draft_in_a_subfolder(cfg) -> None:
    """Корпус памяти — только корень каталога; черновик уроком не является."""
    sub = Path(cfg.memory_dir) / "drafts"
    sub.mkdir(exist_ok=True)
    p = _write(str(sub), "feedback_draft.md",
               ["name: latin-slug-only", f"description: {RU_DESC}", "topic: testing"])
    assert not _nudged(H.ev_bloat_check(_event(p), cfg), "feedback_draft.md", cfg)


def test_silent_outside_the_memory_dir(cfg, tmp_path: Path) -> None:
    p = _write(str(tmp_path), "feedback_outside.md",
               ["name: latin-slug-only", f"description: {RU_DESC}", "topic: testing"])
    assert not _nudged(H.ev_bloat_check(_event(p), cfg), "feedback_outside.md", cfg)


def test_silent_for_a_size_exempt_file(cfg) -> None:
    p = _write(cfg.memory_dir, "feedback_exempt.md",
               ["name: latin-slug-only", f"description: {RU_DESC}", "topic: testing"])
    cfg2 = replace(cfg, size_exempt=("feedback_exempt.md",))
    assert not _nudged(H.ev_bloat_check(_event(p), cfg2), "feedback_exempt.md", cfg2)


def test_fail_open_on_a_broken_event(cfg) -> None:
    """Хук движка fail-open: чужая форма события не должна ронять запись урока."""
    assert H.ev_bloat_check({"tool_name": "Write", "tool_input": None}, cfg) == ""
    assert H.ev_bloat_check({}, cfg) == ""


def test_the_nudge_is_on_by_default_and_the_switch_really_silences_it(cfg) -> None:
    """Ручка `no_keywords_nudge_enabled`: включена по умолчанию, `false` гасит подсказку.

    Обе половины в одном тесте намеренно. Проверка «выключается» в одиночку зеленеет и на
    страже, который не работает вовсе, а проверка «включён по умолчанию» в одиночку не
    заметила бы, что ручка ни к чему не подключена.
    """
    from dataclasses import replace as _replace

    p = _write(cfg.memory_dir, "feedback_switch.md",
               ["name: latin-slug-only", f"description: {RU_DESC}", "topic: testing"])
    assert cfg.no_keywords_nudge_enabled is True
    assert _nudged(H.ev_bloat_check(_event(p), cfg), "feedback_switch.md", cfg)
    off = _replace(cfg, no_keywords_nudge_enabled=False)
    assert not _nudged(H.ev_bloat_check(_event(p), off), "feedback_switch.md", off)
