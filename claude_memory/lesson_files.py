"""Единый источник истины: ЧТО такое файл-урок и КАКОГО он типа.

Зачем модуль. До 0.10.0 ответ на «что такое урок» жил в пяти местах и разошёлся:
  • ШИРОКО — `catalog_generate._lesson_paths`, `memory_retrieve._candidate_files`:
    любой `*.md` в корне memory_dir, кроме ядра/указателя/приватных;
  • УЗКО — `stop_check.newest_lesson_mtime`/`task_lesson_recorded`, bloat-check,
    `catalog_generate` (сверка wikilink-ссылок), `precedent_index`: только файлы с
    маской `f"{prefix}_*.md"` по `cfg.lesson_prefixes`;
  • «ПОЛУШИРОКО» — `staleness`, `applies_to`: `*.md` вообще без исключений.
Файл без приставки БЫЛ уроком для каталога и ретривера и НЕ существовал для стража:
страж требовал записать урок, урок писали, а он требовал снова. Симптом читался как
«хук сломан и ругается всегда».

Почему приставка вообще не годится как признак. Файлы уроков создаёт НЕ движок —
их СОЗДАЁТ встроенная авто-память Claude Code (её подпись — `metadata.originSessionId`
в каждом файле). Движок в этом каталоге гость: он читает, индексирует и сторожит, а имя
файлу не даёт НИКОГДА. (В существующие уроки движок пишет — авто-архивация вырезает из
файла старые карточки прецедентов, — но не создаёт их и не именует.) Требовать от чужого
писателя приставку в имени — требовать того, чем не управляешь. На живых корпусах это дало два разных отказа при одном корне:
  • модель именует файл по формуле `<значение поля type>_<слаг>.md` (замер: 421 файл
    из 426 — приставка ТОЧНО равна полю `type`), но типов в ходу девять, а
    `lesson_prefixes` знал три → уроки типов `role`/`prompt`/`principle`/`user` невидимы;
  • либо модель следует инструкции авто-памяти и пишет чистый kebab-case без приставки
    вовсе → невидимы все 13 таких уроков.

Решение. Уроком считается ЛЮБОЙ `*.md` в корне memory_dir, кроме ядра, указателя и
приватных `_*`. Тип берётся из поля `type` frontmatter — там, где его и пишет автор
файла (замер: поле есть в 513 файлах из 514). Имя файла движку безразлично.

ГРАНИЦА МОДУЛЯ: импортирует только stdlib + `.config`. Все потребители уже импортируют
config, поэтому цикла нет по построению. `lesson_type` НЕ читает файл — принимает уже
разобранный frontmatter (разбор — дело `catalog_generate.parse_frontmatter`).
"""
from __future__ import annotations

import glob
import os
from typing import Dict, List

from .config import MemoryConfig


def is_lesson_file(basename: str, cfg: MemoryConfig) -> bool:
    """Подходит ли ИМЯ файла уроку (половина признака — без места файла!).

    Имя урока = `*.md`, кроме ядра (`core_file`), указателя (`catalog_file`) и приватных
    служебных файлов (`private_file_prefix`).

    ВАЖНО, ЧЕГО ЭТА ФУНКЦИЯ НЕ ЗНАЕТ: где файл лежит. `archive/x.md` и `drafts/x.md` носят
    законное имя урока, но уроками НЕ являются — уроки живут только в КОРНЕ memory_dir.
    Поэтому: есть путь → зови `is_lesson_path`. Эта функция — для тех, у кого на руках
    только имя И кто уже знает, что файл из корня.

    Намеренно НЕ смотрит на приставку и НЕ смотрит на тип: файл без поля `type` — всё
    равно урок (иначе он выпал бы у стража, а страж не имеет права требовать того, чего
    не умеет замечать). Тип — отдельный вопрос, см. `lesson_type`.
    """
    if not basename.endswith(".md"):
        return False
    if basename in (cfg.core_file, cfg.catalog_file):
        return False
    if cfg.private_file_prefix and basename.startswith(cfg.private_file_prefix):
        return False
    return True


def is_lesson_path(path: str, cfg: MemoryConfig, memory_dir: str = None) -> bool:
    """Урок ли файл ПО ПОЛНОМУ ПУТИ: лежит в КОРНЕ memory_dir И имя подходит уроку.

    Зачем отдельно от `is_lesson_file`. Имя — только половина признака. Кто знает лишь имя,
    ответит «да» и на `drafts/chernovik.md` — и разойдётся с `lesson_paths`, которая
    обходит только корень. Ровно это и случилось в 0.10.0: `hooks_cli` звал
    `is_lesson_file` на голом basename произвольного пути, и нудж «пустой `name`» прилетал
    на черновик в подпапке, которого корпус памяти не видит вовсе (у боевого проекта такая
    подпапка есть). Два ответа на «что такое урок» из ОДНОГО модуля — то самое расхождение,
    ради искоренения которого модуль и заведён; проглядела его я в модуле, названном единым
    источником истины.
    """
    root = os.path.abspath(memory_dir if memory_dir is not None else cfg.memory_dir)
    if os.path.dirname(os.path.abspath(path)) != root:
        return False
    return is_lesson_file(os.path.basename(path), cfg)


def lesson_paths(cfg: MemoryConfig, memory_dir: str = None) -> List[str]:
    """Отсортированные пути всех уроков в КОРНЕ memory_dir (подпапки — не уроки).

    Подпапки исключены намеренно: `archive/` — это архив (у него свой обход), а
    `drafts/` и подобные — черновики проекта, не корпус памяти.
    """
    base = memory_dir if memory_dir is not None else cfg.memory_dir
    return sorted(
        p for p in glob.glob(os.path.join(base, "*.md"))
        if is_lesson_file(os.path.basename(p), cfg)
    )


def lesson_type(fm: Dict[str, str]) -> str:
    """Тип урока из разобранного frontmatter ("" — поле не задано).

    Источник — поле `type` (в т.ч. вложенное `metadata:\\n  type:`, которое пишет
    авто-память Claude Code; `parse_frontmatter` разбирает обе формы). Официальный
    словарь Claude Code — `user | feedback | project | reference`, но НЕ ограничиваем:
    модель заводит и свои (`role`, `prompt`, `principle`), и это законно — движку тип
    нужен для группировки и нуджей, а не для допуска.

    Фолбэка на приставку имени здесь НЕТ намеренно: он законсервировал бы ровно то
    второе определение, ради искоренения которого модуль и заведён. Файл без поля
    получает пустой тип — это ЧЕСТНЫЙ результат, видимый в пульсе здоровья, а не
    молчаливая догадка по имени.
    """
    return (fm.get("type") or "").strip()
