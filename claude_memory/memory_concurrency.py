"""Оптимистичная блокировка параллельной правки файлов памяти.

Каталог памяти — единое изменяемое хранилище, общее для ВСЕХ параллельных сессий
Claude Code, без git и без lock'а. Две сессии, делающие read→modify→write одного
файла (особенно горячих индексов — ядра/указателя), дают last-writer-wins: правка
одной молча теряется.

Решение — compare-and-swap по содержимому (ETag-стиль), а НЕ lock на запись. Потеря
возникает в окне read→modify→write, а hook — короткоживущий процесс и не может удержать
flock через harness-запись. Зато можно запомнить хеш версии, которую сессия видела
последней, и при следующей записи сравнить с тем, что на диске: изменилось «под нами»
→ другая сессия успела записать → блокируем с просьбой перечитать. Тихая потеря
превращается в громкий, восстановимый deny.

Состояние сессии — маркер-файл `<tmpdir>/claude-memcas-<session_id>/<sha256(abspath)>`
с последним виденным sha256. Ключ маркера включает session_id → сессии изолированы.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

MARKER_PREFIX = "claude-memcas-"


def content_hash(file_path: str) -> Optional[str]:
    """sha256 содержимого файла (hex) или None, если файл отсутствует/нечитаем."""
    try:
        with open(file_path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def marker_path(session_id: str, file_path: str, tmpdir: str) -> Path:
    """Путь маркера для (сессия, файл): <tmpdir>/claude-memcas-<sid>/<sha256(abspath)>.

    sha256 (не md5) — имя файла-маркера от пути, не криптозащита."""
    abspath = os.path.abspath(file_path)
    digest = hashlib.sha256(abspath.encode("utf-8")).hexdigest()
    return Path(tmpdir) / f"{MARKER_PREFIX}{session_id}" / digest


def record_seen(session_id: str, file_path: str, tmpdir: str) -> None:
    """Запомнить текущий on-disk хеш файла как «последнюю виденную» версию сессии.

    Вызывается после Read и после успешной Write/Edit. No-op, если файла нет
    (хеш None) или маркер не удаётся записать — fail-silent, страж всё равно fail-open.
    """
    h = content_hash(file_path)
    if h is None:
        return
    marker = marker_path(session_id, file_path, tmpdir)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(h, encoding="utf-8")
    except OSError:
        return


def conflict_reason(session_id: str, file_path: str, tmpdir: str) -> Optional[str]:
    """Текст-причина deny, если файл изменён другой сессией с момента, когда ЭТА
    сессия его последний раз видела (читала/писала); иначе None.

    Fail-OPEN (None = разрешить) во всех неоднозначных случаях — никогда не блокируем
    легитимную работу:
    - нет маркера (сессия не читала/не писала файл) → первая правка, не блокируем
      (harness сам требует Read-before-Write существующего файла);
    - файла нет на диске (current hash None) → создание нового, конфликта нет;
    - ошибка чтения маркера.
    """
    marker = marker_path(session_id, file_path, tmpdir)
    try:
        recorded = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None  # нет записи о версии → fail-open
    if not recorded:
        return None
    current = content_hash(file_path)
    if current is None:
        return None  # файла нет → нечего терять
    if current == recorded:
        return None  # правим ту версию, что видели → ок
    return (
        f"Файл памяти {os.path.basename(file_path)} изменён другой сессией с момента, "
        "когда ты его последний раз читал/писал (параллельная сессия успела записать "
        "между твоими чтением и правкой). Чтобы не затереть её изменение: перечитай "
        "файл (Read), затем повтори свою правку — она пройдёт. [memory-concurrency-guard]"
    )
