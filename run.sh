#!/usr/bin/env bash
# Start the map UI. First run creates the venv and installs dependencies.
set -euo pipefail
cd "$(dirname "$0")"

# shellcheck source=bootstrap.sh
. ./bootstrap.sh

ensure_venv
warn_old_python

exec ./.venv/bin/python -m spoofer.server "$@"
