#!/usr/bin/env bash
# Create the virtualenv if it is missing or half-built. Sourced by run.sh and doctor.sh
# so a fresh clone works from any entry point, not just run.sh.
set -euo pipefail

ensure_venv() {
  # A venv left behind by an interrupted install has a python but no dependencies, and
  # every later run would skip setup and fail confusingly. Treat that as absent.
  if [ -x .venv/bin/python ] && ./.venv/bin/python -c "import pymobiledevice3" 2>/dev/null; then
    return 0
  fi

  if [ -e .venv ] && [ ! -x .venv/bin/python ]; then
    echo "Removing a partially built .venv…"
    rm -rf .venv
  fi

  local py
  py="$(pick_python)"
  if [ ! -x .venv/bin/python ]; then
    echo "Creating virtualenv with $("$py" --version 2>&1)…"
    "$py" -m venv .venv
  fi

  echo "Installing dependencies (about a minute the first time)…"
  if ! ./.venv/bin/pip install -q --upgrade pip || ! ./.venv/bin/pip install -q -r requirements.txt; then
    echo
    echo "Install failed. Removing the incomplete .venv so the next run starts clean." >&2
    rm -rf .venv
    exit 1
  fi
}

pick_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1; then echo "$c"; return; fi
  done
  echo "python3"
}

warn_old_python() {
  local minor
  minor="$(./.venv/bin/python -c 'import sys; print(sys.version_info[1])')"
  if [ "$minor" -lt 10 ]; then
    echo "note: venv runs Python 3.$minor, which is end of life. The app works, but the"
    echo "      bundled pymobiledevice3 CLI cannot import. 'brew install python@3.12',"
    echo "      delete .venv, and rerun to move up."
  fi
}
