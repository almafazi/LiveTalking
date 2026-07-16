#!/usr/bin/env bash
set -euo pipefail

SRS_DIR="${SRS_DIR:-/usr/local/srs}"
cd "$SRS_DIR"

exec ./objs/srs -c conf/livetalking.conf
