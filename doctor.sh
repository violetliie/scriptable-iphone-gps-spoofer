#!/usr/bin/env bash
# Preflight: walks the same chain the app does and reports where it breaks.
set -euo pipefail
cd "$(dirname "$0")"

# shellcheck source=bootstrap.sh
. ./bootstrap.sh

ensure_venv

exec ./.venv/bin/python -m spoofer.doctor "$@"
