#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${MAILTRACK_REMOTE_HOST:-michael@tachyonfuture.com}"
CONTAINER_NAME="${MAILTRACK_CONTAINER_NAME:-mailtrack}"

ssh "${REMOTE_HOST}" "docker logs -f '${CONTAINER_NAME}'"
