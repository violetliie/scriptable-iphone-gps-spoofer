#!/usr/bin/env bash
# Double-click this in Finder to check whether the phone is ready.
cd "$(dirname "$0")"
# doctor.sh exits non-zero when it finds a blocking problem, which is exactly when the
# user most needs to read it, so don't let that close the window.
./doctor.sh || true
echo
echo "Press return to close."
read -r _
