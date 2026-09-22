#!/usr/bin/env bash
# docker compose based deployment script.
#
# Order: backup current images as :previous -> build -> up -d -> healthcheck -> rollback on failure
#
# "Rollback" here means a deployment rollback: reverting containers to the
# previous image tagged :previous. It is NOT a source code rollback like
# git revert. The main branch code is left untouched; only the running
# container images are reverted.
#
# exit code:
#   0 - new version deployed successfully
#   1 - new version deploy failed, rollback to previous version succeeded
#   2 - new version deploy failed, rollback also failed (manual intervention required)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

# Fix the compose project name regardless of the checkout directory name, so
# :previous tagging always points at the same image repository.
PROJECT_NAME="semantic-router"
COMPOSE_FILE="docker-compose.yml"
SERVICES=(backend frontend)

HEALTH_URL="${HEALTH_URL:-http://localhost:8000/health}"
HEALTH_RETRIES="${HEALTH_RETRIES:-10}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-3}"

compose() {
    docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
}

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1"
}

backup_current_images() {
    log "Backing up current images with :previous tag."
    for svc in "${SERVICES[@]}"; do
        local image="${PROJECT_NAME}-${svc}"
        if docker image inspect "${image}:latest" >/dev/null 2>&1; then
            docker tag "${image}:latest" "${image}:previous"
            log "  - ${image}:latest -> ${image}:previous backed up"
        else
            log "  - ${image}:latest not found, skipping backup (likely first deploy)."
        fi
    done
}

build_and_up() {
    log "Building new images (docker compose build)."
    if ! compose build; then
        log "Build failed."
        return 1
    fi

    log "Starting new containers (docker compose up -d)."
    if ! compose up -d; then
        log "Container startup failed."
        return 1
    fi

    return 0
}

rollback() {
    log "Starting rollback: reverting containers to the :previous tag."
    local rollback_ok=true

    for svc in "${SERVICES[@]}"; do
        local image="${PROJECT_NAME}-${svc}"
        if docker image inspect "${image}:previous" >/dev/null 2>&1; then
            docker tag "${image}:previous" "${image}:latest"
            log "  - ${image}:previous -> ${image}:latest restored"
        else
            log "  - ${image}:previous not found, nothing to roll back to."
            rollback_ok=false
        fi
    done

    if [ "$rollback_ok" = false ]; then
        log "Rollback not possible: no previous image exists (likely the first deploy). Manual intervention required."
        return 1
    fi

    log "Restarting containers with the previous image."
    if ! compose up -d --force-recreate --no-build "${SERVICES[@]}"; then
        log "Container restart after rollback failed. Manual intervention required."
        return 1
    fi

    log "Re-running health check after rollback."
    if "${SCRIPT_DIR}/healthcheck.sh" "$HEALTH_URL" "$HEALTH_RETRIES" "$HEALTH_INTERVAL"; then
        log "Rollback complete: previous version is healthy."
        return 0
    else
        log "Health check still failing after rollback. Manual intervention required."
        return 1
    fi
}

main() {
    log "===== Deployment started (project=${PROJECT_NAME}) ====="

    backup_current_images

    if ! build_and_up; then
        log "Build/startup step failed. Attempting rollback."
        if rollback; then
            log "===== Deploy failed, rollback succeeded ====="
            exit 1
        else
            log "===== Deploy failed, rollback also failed (manual intervention required) ====="
            exit 2
        fi
    fi

    log "Running health check for the new version."
    if "${SCRIPT_DIR}/healthcheck.sh" "$HEALTH_URL" "$HEALTH_RETRIES" "$HEALTH_INTERVAL"; then
        log "===== Deploy succeeded ====="
        exit 0
    fi

    log "Health check failed. Attempting rollback."
    if rollback; then
        log "===== Deploy failed, rollback succeeded ====="
        exit 1
    else
        log "===== Deploy failed, rollback also failed (manual intervention required) ====="
        exit 2
    fi
}

main "$@"
