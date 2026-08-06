#!/usr/bin/env bash
# Double-click this if "Developer Mode" is missing from the phone's Settings.
# Plug the phone in and unlock it first.
cd "$(dirname "$0")"

# shellcheck source=bootstrap.sh
. ./bootstrap.sh
ensure_venv

./.venv/bin/python -m spoofer.reveal || true
echo "Press return to close."
read -r _
