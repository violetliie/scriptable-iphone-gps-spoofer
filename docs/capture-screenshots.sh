#!/usr/bin/env bash
# Capture the three screenshots the README expects.
#
# Run it, then for each prompt click once on the browser window showing the app.
# macOS captures that window only, at full retina resolution, with no other
# windows or desktop contents included.
set -euo pipefail
cd "$(dirname "$0")/screenshots"

shot() {
  local file="$1" what="$2"
  echo
  echo "  $what"
  echo "  Press return, then click the browser window."
  read -r _
  screencapture -w -o "$file"
  echo "  saved $file"
}

echo "Start the app first (./run.sh, or ./run.sh --mock) and open http://127.0.0.1:8765"

shot main.png     "1/3  Teleport mode, connected, marker somewhere recognisable."
shot route.png    "2/3  Route mode, a few waypoints dropped, ideally mid-walk."
shot joystick.png "3/3  Joystick mode, with the direction pad visible."

echo
echo "Done. All three are in docs/screenshots/."
