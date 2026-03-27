#!/usr/bin/env bash
set -euo pipefail

HEALTHCHECK_URL="${MAILTRACK_HEALTHCHECK_URL:-https://mailtrack.tachyonfuture.com/health}"
curl --fail --silent --show-error "${HEALTHCHECK_URL}"
echo
