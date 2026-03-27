#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${MAILTRACK_REMOTE_HOST:-michael@tachyonfuture.com}"
REMOTE_PATH="${MAILTRACK_REMOTE_PATH:-/home/michael/docker-configs/mailtrack}"
HEALTHCHECK_URL="${MAILTRACK_HEALTHCHECK_URL:-https://mailtrack.tachyonfuture.com/health}"

echo "Deploying to ${REMOTE_HOST}:${REMOTE_PATH}"
ssh "${REMOTE_HOST}" "cd '${REMOTE_PATH}' && git pull --ff-only && docker compose up -d --build"

echo "Waiting for health check: ${HEALTHCHECK_URL}"
curl --fail --silent --show-error "${HEALTHCHECK_URL}"
echo
echo "Deploy complete."
