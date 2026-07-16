#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${RUNTIME_ENV_FILE:-$ROOT/.env.runtime}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Runtime environment file not found: $ENV_FILE" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ -z "${SRS_RTC_EIP:-}" && -n "${PUBLIC_IPADDR:-}" && -n "${VAST_TCP_PORT_10200:-}" ]]; then
    export SRS_RTC_EIP="${PUBLIC_IPADDR}:${VAST_TCP_PORT_10200}"
fi

PYTHON="${LIVETALKING_PYTHON:-$ROOT/.venv/bin/python}"
cd "$ROOT"

exec "$PYTHON" runtime-manager/manager.py
