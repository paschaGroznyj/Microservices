#!/usr/bin/env bash
# restart.sh — пересборка и перезапуск сервисов compose с уведомлением всех вкладок.
#
#   ./restart.sh [опции] <сервис> [сервис ...]
#
# Порядок: build → уведомление (Redis sys:notice) → пауза → stop → up -d →
# ожидание running/healthy → фаза «готово» (короткий TTL).
# Канал до фронта: поле notice в /api/presence/count (опрос раз в 15 с, при работах 5 с).
#
# Опции:
#   -m, --message FILE  markdown-текст уведомления (по умолчанию ./notice.md)
#   -n, --no-notice     без уведомления
#       --no-build      без docker compose build
#   -d, --delay SEC     пауза между уведомлением и stop (по умолчанию 20)
#   -w, --wait-agent SEC  ждать active_requests == 0, но не дольше SEC (по умолчанию 0 = не ждать)
#   -t, --timeout SEC   сколько ждать готовности после up (по умолчанию 180)
#       --ttl SEC       TTL уведомления в фазе работ (по умолчанию 900)
#   -h, --help
#
# Примеры:
#   ./restart.sh agent_service
#   ./restart.sh -m notice.md -w 120 agent_service chats_service
#   ./restart.sh --no-build -d 5 auth_service
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

MSG_FILE="notice.md"
NOTICE=1
BUILD=1
DELAY=20
WAIT_AGENT=0
TIMEOUT=180
TTL=900
DONE_TTL=60
REDIS_SVC="${REDIS_SVC:-redis}"
GW_URL="${GW_URL:-http://127.0.0.1:7000}"
KEY="sys:notice"
SERVICES=()

usage() { sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; }
log()   { printf '\033[1;34m[restart]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[restart]\033[0m %s\n' "$*" >&2; }
die()   { printf '\033[1;31m[restart]\033[0m %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--message)    MSG_FILE="${2:?}"; shift 2 ;;
    -n|--no-notice)  NOTICE=0; shift ;;
    --no-build)      BUILD=0; shift ;;
    -d|--delay)      DELAY="${2:?}"; shift 2 ;;
    -w|--wait-agent) WAIT_AGENT="${2:?}"; shift 2 ;;
    -t|--timeout)    TIMEOUT="${2:?}"; shift 2 ;;
    --ttl)           TTL="${2:?}"; shift 2 ;;
    -h|--help)       usage; exit 0 ;;
    --)              shift; SERVICES+=("$@"); break ;;
    -*)              die "неизвестная опция: $1 (см. --help)" ;;
    *)               SERVICES+=("$1"); shift ;;
  esac
done

[[ ${#SERVICES[@]} -gt 0 ]] || { usage; die "не указаны сервисы"; }

# Проверка имён по docker-compose.yml.
KNOWN="$(docker compose config --services)"
for s in "${SERVICES[@]}"; do
  grep -qxF "$s" <<<"$KNOWN" || die "нет сервиса '$s' в compose. Есть: $(tr '\n' ' ' <<<"$KNOWN")"
done

# Перезапуск redis или гейтвеев обрывает сам канал уведомления.
for s in "${SERVICES[@]}"; do
  case "$s" in
    "$REDIS_SVC"|gateway|gateway_sigma)
      if [[ $NOTICE -eq 1 ]]; then
        warn "$s в списке: уведомление может не дойти или пропасть до конца работ"
      fi ;;
  esac
done

rcli() { docker compose exec -T "$REDIS_SVC" redis-cli "$@"; }

NOTICE_ID=""
notice_set() {   # $1 — фаза, $2 — TTL
  [[ $NOTICE -eq 1 ]] || return 0
  rcli HSET "$KEY" id "$NOTICE_ID" phase "$1" services "${SERVICES[*]}" ts "$(date +%s)" >/dev/null
  rcli EXPIRE "$KEY" "$2" >/dev/null
}

on_exit() {
  local rc=$?
  if [[ $rc -ne 0 && $NOTICE -eq 1 && -n "$NOTICE_ID" ]]; then
    warn "скрипт упал (rc=$rc): уведомление остаётся до TTL. Снять вручную: docker compose exec $REDIS_SVC redis-cli DEL $KEY"
  fi
}
trap on_exit EXIT

# 1. Сборка до простоя.
if [[ $BUILD -eq 1 ]]; then
  log "build: ${SERVICES[*]}"
  docker compose build "${SERVICES[@]}"
fi

# 2. Уведомление.
if [[ $NOTICE -eq 1 ]]; then
  [[ -f "$MSG_FILE" ]] || die "нет файла уведомления: $MSG_FILE (или --no-notice)"
  NOTICE_ID="r-$(date +%Y%m%d-%H%M%S)"
  log "уведомление $NOTICE_ID из $MSG_FILE"
  rcli DEL "$KEY" >/dev/null
  # Текст идёт через stdin (-x) — markdown любой длины, без экранирования.
  rcli -x HSET "$KEY" text <"$MSG_FILE" >/dev/null
  notice_set maintenance "$TTL"
  log "ждём $DELAY с, пока вкладки получат уведомление"
  sleep "$DELAY"
fi

# 3. Ожидание активных запросов к агенту.
if [[ "$WAIT_AGENT" -gt 0 ]]; then
  log "ждём завершения запросов к агенту (до $WAIT_AGENT с)"
  end=$(( $(date +%s) + WAIT_AGENT ))
  while :; do
    ar=$(curl -fsS --max-time 3 "$GW_URL/api/presence/count" 2>/dev/null \
         | sed -n 's/.*"active_requests":[[:space:]]*\([0-9]*\).*/\1/p')
    if [[ "${ar:-?}" == "0" ]]; then log "активных запросов нет"; break; fi
    if (( $(date +%s) >= end )); then warn "таймаут: активных запросов ${ar:-?}, продолжаем"; break; fi
    sleep 3
  done
fi

# 4. stop → up.
log "stop: ${SERVICES[*]}"
docker compose stop "${SERVICES[@]}"
log "up -d: ${SERVICES[*]}"
docker compose up -d --no-build "${SERVICES[@]}"

# 5. Готовность: healthy, если есть healthcheck, иначе running.
log "ждём готовности (до $TIMEOUT с)"
end=$(( $(date +%s) + TIMEOUT ))
for s in "${SERVICES[@]}"; do
  while :; do
    cid=$(docker compose ps -q "$s" | head -1)
    st=""
    if [[ -n "$cid" ]]; then
      st=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || true)
    fi
    if [[ "$st" == "healthy" || "$st" == "running" ]]; then log "$s: $st"; break; fi
    if (( $(date +%s) >= end )); then die "$s не поднялся за $TIMEOUT с (статус: ${st:-нет контейнера})"; fi
    sleep 2
  done
done

# 6. Фаза «готово»: открытые модалки покажут «Сервисы снова доступны» и закроются.
if [[ $NOTICE -eq 1 ]]; then
  notice_set done "$DONE_TTL"
  log "уведомление $NOTICE_ID → done (TTL $DONE_TTL с)"
fi
log "готово"
