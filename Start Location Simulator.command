#!/usr/bin/env bash
# Double-click this in Finder. It starts the server and opens the map in your browser.
cd "$(dirname "$0")"

PORT=8765
URL="http://127.0.0.1:$PORT"

if curl -s -m 2 "$URL/api/state" >/dev/null 2>&1; then
  echo "Already running — opening $URL"
  open "$URL"
  echo
  echo "The server is running in another window. Close this one."
  exit 0
fi

# Open the browser once the server answers, then hand the terminal over to the server.
(
  for _ in $(seq 1 60); do
    if curl -s -m 1 "$URL/api/state" >/dev/null 2>&1; then
      open "$URL"
      exit 0
    fi
    sleep 0.5
  done
) &

echo "Starting the iPhone location simulator…"
echo "Leave this window open. Press Control-C to stop and give the phone its real GPS back."
echo
exec ./run.sh --port "$PORT"
