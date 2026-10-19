#!/usr/bin/env bash
# Open a profile's HTML timeline from your laptop.
#
#   ./open_profile.sh                       newest profile on the VM
#   ./open_profile.sh profile_20261019_051  that profile (name or path)
#   HOST=other-vm ./open_profile.sh         a different machine
#
# Serves the profile directory over the SSH tunnel and opens a browser; the
# HTML is self-contained, so the .svg and .csv are listed on the same page.
# Ctrl-C to stop.
set -euo pipefail

HOST="${HOST:-chia-dev.us-west4-c.chia-codesign26oak-1951}"
REMOTE_OUT="${REMOTE_OUT:-\$HOME/chia-tutorial/debugging/profiling/out}"
PORT="${PORT:-8800}"
WANTED="${1:-}"

dir=$(ssh "$HOST" "ls -dt $REMOTE_OUT/*${WANTED}*/ 2>/dev/null | head -1")
[ -n "$dir" ] || { echo "no profile matching '${WANTED}' in $REMOTE_OUT on $HOST" >&2; exit 1; }
echo "serving $dir"

ssh -f -L "$PORT:localhost:$PORT" "$HOST" \
    "cd '$dir' && python3 -m http.server $PORT >/dev/null 2>&1" \
    || { echo "could not start the server (port $PORT already forwarded?)" >&2; exit 1; }

url="http://localhost:$PORT/ChiaProfileCollector_profile.html"
sleep 1
(command -v xdg-open >/dev/null && xdg-open "$url") \
    || (command -v open >/dev/null && open "$url") \
    || echo "open it yourself: $url"
echo "open: $url"
echo "stop: ssh $HOST 'pkill -f \"http.server $PORT\"'"
