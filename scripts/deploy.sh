#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${MAILTRACK_REMOTE_HOST:-michael@tachyonfuture.com}"
REMOTE_PATH="${MAILTRACK_REMOTE_PATH:-/home/michael/docker-configs/mailtrack}"
HEALTHCHECK_URL="${MAILTRACK_HEALTHCHECK_URL:-https://mailtrack.tachyonfuture.com/health}"
APP_CONTAINER_NAME="${MAILTRACK_APP_CONTAINER_NAME:-mailtrack}"
DB_CONTAINER_NAME="${MAILTRACK_DB_CONTAINER_NAME:-mailtrack-mysql}"
REMOTE_HEALTHCHECK_RETRIES="${MAILTRACK_REMOTE_HEALTHCHECK_RETRIES:-30}"
REMOTE_HEALTHCHECK_DELAY_SECONDS="${MAILTRACK_REMOTE_HEALTHCHECK_DELAY_SECONDS:-5}"
HEALTHCHECK_RETRIES="${MAILTRACK_HEALTHCHECK_RETRIES:-20}"
HEALTHCHECK_DELAY_SECONDS="${MAILTRACK_HEALTHCHECK_DELAY_SECONDS:-5}"

wait_for_http_healthcheck() {
  local attempt

  for ((attempt = 1; attempt <= HEALTHCHECK_RETRIES; attempt++)); do
    if curl --fail --silent --show-error "${HEALTHCHECK_URL}"; then
      echo
      return 0
    fi

    echo "Waiting for public health check (${attempt}/${HEALTHCHECK_RETRIES})"
    sleep "${HEALTHCHECK_DELAY_SECONDS}"
  done

  echo "Public health check failed: ${HEALTHCHECK_URL}" >&2
  return 1
}

echo "Deploying to ${REMOTE_HOST}:${REMOTE_PATH}"
ssh "${REMOTE_HOST}" bash -s -- \
  "${REMOTE_PATH}" \
  "${APP_CONTAINER_NAME}" \
  "${DB_CONTAINER_NAME}" \
  "${REMOTE_HEALTHCHECK_RETRIES}" \
  "${REMOTE_HEALTHCHECK_DELAY_SECONDS}" <<'EOF'
set -euo pipefail

remote_path="$1"
app_container_name="$2"
db_container_name="$3"
healthcheck_retries="$4"
healthcheck_delay_seconds="$5"

wait_for_container_health() {
  local container_name="$1"
  local attempt status

  for ((attempt = 1; attempt <= healthcheck_retries; attempt++)); do
    status="$(
      docker inspect \
        --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
        "${container_name}" 2>/dev/null || true
    )"

    case "${status}" in
      healthy|running)
        echo "Container ${container_name} is ${status}"
        return 0
        ;;
      unhealthy)
        echo "Container ${container_name} reported unhealthy status" >&2
        docker logs --tail 50 "${container_name}" || true
        ;;
      "")
        echo "Container ${container_name} is not available yet"
        ;;
      *)
        echo "Waiting for container ${container_name} (${attempt}/${healthcheck_retries}): ${status}"
        ;;
    esac

    sleep "${healthcheck_delay_seconds}"
  done

  echo "Timed out waiting for container ${container_name} health" >&2
  docker logs --tail 100 "${container_name}" || true
  return 1
}

cd "${remote_path}"
git pull --ff-only
docker compose up -d --build

echo "Removing old Docker image layers"
docker image prune -f
if ! docker builder prune -f; then
  echo "Skipping Docker builder cache prune"
fi

echo "Running remote container health checks"
wait_for_container_health "${db_container_name}"
wait_for_container_health "${app_container_name}"
EOF

echo "Running public health check: ${HEALTHCHECK_URL}"
wait_for_http_healthcheck

echo "Deploy complete."
