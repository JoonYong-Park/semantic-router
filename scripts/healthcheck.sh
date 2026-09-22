#!/usr/bin/env bash
# Retrying health check against the backend (FastAPI) /health endpoint.
#
# Usage:
#   scripts/healthcheck.sh [URL] [MAX_RETRIES] [INTERVAL_SECONDS]
#
# Success: exit 0
# Failure (all retries exhausted): exit 1

set -euo pipefail

HEALTH_URL="${1:-http://localhost:8000/health}"
MAX_RETRIES="${2:-10}"
INTERVAL="${3:-3}"

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1"
}

log "Starting health check: ${HEALTH_URL} (max ${MAX_RETRIES} attempts, ${INTERVAL}s interval)"

for attempt in $(seq 1 "$MAX_RETRIES"); do
    http_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
        --max-time 5 "$HEALTH_URL" || echo "000")"

    if [ "$http_code" = "200" ]; then
        log "Health check succeeded (attempt ${attempt}/${MAX_RETRIES}, HTTP ${http_code})"
        exit 0
    fi

    log "Health check failed (attempt ${attempt}/${MAX_RETRIES}, HTTP ${http_code})"

    if [ "$attempt" -lt "$MAX_RETRIES" ]; then
        sleep "$INTERVAL"
    fi
done

log "Health check failed after ${MAX_RETRIES} attempts."
exit 1
