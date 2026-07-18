#!/bin/bash
# Stop: БЛОКИРУЕТ завершение, если заявку закрыл не человек, а слияние/коммит, и основания
# закрытия у неё так и нет.
#
# ЗАЧЕМ ОТДЕЛЬНО ОТ `issue_close_basis_guard.sh`. Тот стережёт КОМАНДУ `gh issue close` и
# по построению не видит ничего другого. Между тем заявку закрывают шестью путями:
# ключевым словом в сообщении коммита, ключевым словом в описании PR, кнопкой в вебе, из
# мобильного приложения, через REST/GraphQL, ботом. Так и ушла заявка #6: `Resolves #6`
# стояло и в теле squash-коммита, и в описании PR, команда закрытия не выполнялась вовсе,
# страж не вызывался, основание пришлось дописывать вручную через пять минут.
# Сторожить каждый новый канал — гонка, которую не выиграть (ровно урок заявки #6: там
# страж знал единственный источник сигнала). Поэтому здесь проверяется не КАНАЛ, а
# РЕЗУЛЬТАТ: у закрытой заявки либо есть основание, либо нет, и чем её закрыли — неважно.
#
# ПРИЗНАК КАНАЛА — `ClosedEvent.closer`, и только он. Временнóе окно «комментарий рядом с
# закрытием» проверялось и ОТВЕРГНУТО на живых данных: у #6 основание отстало на 294 с
# (окно 300 с сработало бы с запасом в шесть секунд, окно 60–120 с дало бы ложную
# тревогу), а за 69 минут ДО закрытия там лежит содержательный комментарий на 3741 знак,
# который основанием не является, — окно назад засчитало бы его и промолчало. `closer`
# отвечает прямо: null — закрыл человек, `Commit`/`PullRequest`/`ProjectV2` — закрыто
# автоматически. В REST этого нет (`commit_id` у события `closed` равен null в обоих
# случаях), поэтому запрос именно GraphQL.
#
# ПОЧЕМУ МОЛЧИМ ПРИ `closer=null`. Это ручное закрытие: командой (её стережёт PreToolUse-
# страж, требующий `--comment`) либо кнопкой в вебе. Второе — ЗАПИСАННАЯ ГРАНИЦА: закрытие
# кнопкой без комментария здесь не ловится ничем и ловиться не будет. Требовать
# комментарий и при `closer=null` нельзя: у стража есть законный второй путь — оставить
# основание отдельной командой `gh issue comment` ДО закрытия, и такая заявка получила бы
# ложную тревогу.
#
# ГРАНИЦЫ, НАЗВАННЫЕ ВСЛУХ (чинить не планируется):
#   • Закрытие между сессиями (веб, телефон) обнаруживается с задержкой — до ближайшего
#     завершения сессии в этом репозитории. Мгновенной реакции здесь нет и не будет:
#     GitHub Action ради двух заявок одного владельца — инфраструктура не по размеру.
#   • Ручное закрытие в вебе БЕЗ комментария не отличимо от дисциплинированного
#     `gh issue close --comment` по признаку `closer` — оба дают null. Не покрыто.
#   • Настройка репозитория «Auto-close issues with merged linked pull requests»
#     (Settings → General → Issues) выключает авто-закрытие в корне, но её состояние не
#     читается ни REST, ни GraphQL (`autoCloseIssuesEnabled` → undefinedField, проверено
#     2026-07-18). Значит закрепить её тестом нечем, и тихий откат назад заметит только
#     этот скрипт — следующим же авто-закрытием без основания. Он и есть тест на неё.
#   • Основанием тут считается ЛЮБОЙ комментарий после закрытия, без разбора текста.
#     Разбор содержания вводить только если появятся ложные зачёты: пустая отписка,
#     засчитанная за разбор, — меньшее зло, чем ложная блокировка, которая сносит стража
#     целиком вместе с пользой.
#
# ПРОТОКОЛ. Блокировка Stop — это JSON `{"continue": false, "stopReason": …}` в stdout при
# коде 0 (как `hooks_cli.ev_stop`), а НЕ код 2. Три исхода различаются жёстко: нарушение —
# JSON с текстом; чисто — полное молчание; НЕ СМОГ ПРОВЕРИТЬ — жалоба в stderr и код 0,
# но НИКОГДА не блокировка: сетевая моргалка не имеет права запирать сессию. Пустой вывод
# при ненулевом коде `gh` не трактуется как «нарушений нет» — это ровно тот класс, что
# описан в памяти проекта: «разбор молча вернул пустоту» неотличим от «нечего находить».
#
# Вердикт выносит python, а не `RESULT=$(...)` вокруг него: внутри подстановки команды bash
# ищет пару литеральной обратной кавычке даже в кавычённом heredoc, и на этом уже слегала
# первая версия соседнего стража — а bash отдаёт на синтаксической ошибке код 2, тот же,
# которым хук отклоняет вызов. См. tests/test_issue_close_basis_audit.py: у пропуска
# обязан быть свой тест, иначе проверяется только умение говорить «нет».
set -uo pipefail

WINDOW_DAYS="${CME_AUDIT_WINDOW_DAYS:-14}"
THROTTLE_SECONDS="${CME_AUDIT_THROTTLE_SECONDS:-900}"
STAMP="${TMPDIR:-/tmp}/claude-issue-basis-audit-$(id -u)"

# Троттлинг: полная проверка не чаще раза в THROTTLE_SECONDS. Метку ставим ТОЛЬКО после
# УДАЧНОГО запроса — иначе одна сетевая моргалка глушила бы проверку на весь период, и
# страж уснул бы, выглядя работающим.
if [ -f "$STAMP" ]; then
  last=$(cat "$STAMP" 2>/dev/null || echo 0)
  case "$last" in ''|*[!0-9]*) last=0 ;; esac
  now=$(date +%s)
  if [ $((now - last)) -lt "$THROTTLE_SECONDS" ]; then
    exit 0
  fi
fi

REPO_SLUG=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)
if [ -z "$REPO_SLUG" ]; then
  # Не репозиторий GitHub, нет gh или нет авторизации — молча уходим: этот хук не вправе
  # мешать работе там, где ему нечего проверять. Жалоба ниже — только про сбой ЗАПРОСА,
  # когда репозиторий заведомо есть.
  exit 0
fi
OWNER="${REPO_SLUG%%/*}"
NAME="${REPO_SLUG##*/}"

QUERY='query($owner:String!,$name:String!){
  repository(owner:$owner,name:$name){
    issues(states:CLOSED, last:20, orderBy:{field:UPDATED_AT, direction:ASC}){
      nodes{
        number title closedAt
        timelineItems(last:1, itemTypes:[CLOSED_EVENT]){
          nodes{ ... on ClosedEvent { createdAt closer { __typename } } }
        }
        comments(last:20){ nodes{ createdAt } }
      }
    }
  }
}'

PAYLOAD=$(gh api graphql -f query="$QUERY" -F owner="$OWNER" -F name="$NAME" 2>/dev/null)
GH_RC=$?
if [ $GH_RC -ne 0 ] || [ -z "$PAYLOAD" ]; then
  case $GH_RC in
    4) reason="gh не авторизован (gh auth login)" ;;
    *) reason="нет сети либо недействительный токен (код $GH_RC)" ;;
  esac
  echo "[issue-basis-audit] проверка НЕ ВЫПОЛНЕНА: $reason. Это не значит «нарушений нет»." >&2
  exit 0
fi

HOOK_PAYLOAD="$PAYLOAD" WINDOW_DAYS="$WINDOW_DAYS" STAMP="$STAMP" python3 - <<'PY'
import json, os, sys, time
from datetime import datetime, timedelta, timezone

AUTO = {"Commit", "PullRequest", "ProjectV2"}


def offenders(payload: dict, window_days: int, now: datetime) -> list:
    """Заявки, закрытые АВТОМАТИЧЕСКИ и без единого комментария после закрытия.

    Чистая функция над ответом GraphQL — вся логика вердикта здесь, чтобы тесты гоняли её
    на фикстурах без сети. Любая неожиданная форма ответа = «нарушений не вижу»: страж,
    падающий на чужом JSON, мешает работе вместо того, чтобы помогать."""
    out = []
    horizon = now - timedelta(days=window_days)
    try:
        nodes = payload["data"]["repository"]["issues"]["nodes"]
    except (KeyError, TypeError):
        return out
    for issue in nodes or []:
        if not isinstance(issue, dict):
            continue
        events = ((issue.get("timelineItems") or {}).get("nodes")) or []
        event = events[-1] if events else None
        if not isinstance(event, dict):
            continue
        closer = (event.get("closer") or {}).get("__typename")
        if closer not in AUTO:
            continue                      # закрыл человек → не наш случай (см. шапку)
        closed_at = _parse(event.get("createdAt"))
        if closed_at is None or closed_at < horizon:
            continue                      # старое не ворошим: сессию держит только свежий долг
        comments = ((issue.get("comments") or {}).get("nodes")) or []
        if any((_parse(c.get("createdAt")) or horizon) >= closed_at for c in comments):
            continue                      # основание есть — любой комментарий после закрытия
        out.append({"number": issue.get("number"), "title": issue.get("title") or ""})
    return out


def _parse(value) -> "datetime|None":
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def render(items: list) -> str:
    lines = ["Завершение заблокировано: заявка закрыта автоматически, а основания у неё нет.", ""]
    for it in items:
        lines.append(f"  #{it['number']}  {it['title'][:70]}")
    lines += [
        "",
        "Такую заявку закрыло слияние или коммит (ключевое слово Closes/Fixes/Resolves),",
        "поэтому страж основания не вызывался — он стережёт только команду закрытия.",
        "",
        "Основание — это разбор: чем починено (версия, ссылка на PR), чем доказано",
        "(тесты, воспроизведение, проверка на живых данных), что не пустит дефект обратно.",
        "Если закрыто без кода — довод: дубль #N / так задумано, потому что … /",
        "не воспроизводится на версии … . «Не актуально» основанием не считается.",
        "",
        "Оставь основание и заверши:",
    ]
    for it in items:
        lines.append(f"  gh issue comment {it['number']} --body \"<основание>\"")
    lines += [
        "",
        "Чтобы это не повторялось: пиши основание в заявку ДО слияния закрывающего PR,",
        "либо не ставь Resolves/Closes в описании PR и в сообщении коммита, а закрывай",
        "командой gh issue close --comment после выкладки.",
    ]
    return "\n".join(lines)


def main() -> None:
    try:
        payload = json.loads(os.environ.get("HOOK_PAYLOAD") or "{}")
    except ValueError:
        sys.exit(0)                        # чужой ответ не повод мешать работе
    window = int(os.environ.get("WINDOW_DAYS") or 14)
    items = offenders(payload, window, datetime.now(timezone.utc))
    stamp = os.environ.get("STAMP")
    if stamp:                              # метка ТОЛЬКО после удачного запроса
        try:
            with open(stamp, "w", encoding="utf-8") as fh:
                fh.write(str(int(time.time())))
        except OSError:
            pass
    if items:
        print(json.dumps({"continue": False, "stopReason": render(items)}, ensure_ascii=False))
    sys.exit(0)


main()
PY
exit $?
