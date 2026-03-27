#!/usr/bin/env sh
set -eu

IMAGE="${MAILTRACK_TEST_IMAGE:-python:3.12-alpine}"
WORKDIR="/workspace"

docker run --rm \
  -v "$(pwd):${WORKDIR}" \
  -w "${WORKDIR}" \
  "${IMAGE}" \
  sh -lc 'apk add --no-cache tzdata >/dev/null && PIP_ROOT_USER_ACTION=ignore pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt >/dev/null && python -m pytest "$@"' \
  sh "$@"
