#!/usr/bin/env bash
# Прогон тестов всех микросервисов, каждый в своём образе (стадия test Dockerfile).
#   ./run_tests.sh                       все сервисы
#   ./run_tests.sh chats_service auth_service   выбранные
#   PYTEST_ARGS="-x -k editor" ./run_tests.sh api_gateway
# Код выхода: 0, если все сервисы зелёные.
set -uo pipefail
cd "$(dirname "$0")"
COMPOSE=(docker compose -f docker-compose.test.yml)
ALL=(agent_sandbox agent_service api_gateway api_gateway_sigma auth_service chats_service max_service parsers_service telegram_service)
SERVICES=("$@"); [ ${#SERVICES[@]} -eq 0 ] && SERVICES=("${ALL[@]}")
LOG_DIR="${LOG_DIR:-.test-logs}"; mkdir -p "$LOG_DIR"

"${COMPOSE[@]}" build "${SERVICES[@]/%/_test}" || { echo "Сборка тестовых образов упала"; exit 2; }

declare -A RESULT; FAILED=0
for s in "${SERVICES[@]}"; do
  echo "=== $s"
  if [ -n "${PYTEST_ARGS:-}" ]; then
    # shellcheck disable=SC2086
    "${COMPOSE[@]}" run --rm -T "${s}_test" python -m pytest -q -p no:cacheprovider tests $PYTEST_ARGS 2>&1 | tee "$LOG_DIR/$s.log"
  else
    "${COMPOSE[@]}" run --rm -T "${s}_test" 2>&1 | tee "$LOG_DIR/$s.log"
  fi
  rc=${PIPESTATUS[0]}
  summary=$(grep -E '[0-9]+ (passed|failed|error)' "$LOG_DIR/$s.log" | tail -1 | sed 's/=//g; s/^ *//')
  case $rc in
    0) RESULT[$s]="OK    ${summary}" ;;
    5) RESULT[$s]="EMPTY тесты не найдены"; FAILED=1 ;;
    *) RESULT[$s]="FAIL  (rc=$rc) ${summary}"; FAILED=1 ;;
  esac
done

echo; echo "===== Итог ====="
for s in "${SERVICES[@]}"; do printf '%-18s %s\n' "$s" "${RESULT[$s]}"; done
exit $FAILED
