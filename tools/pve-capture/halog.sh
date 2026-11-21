#!/bin/bash
# Follow the Home Assistant log for the coffee machine integration at debug level.
# The level is re-applied every 2 minutes, because an HA restart resets it.
DIR=/root/ecam-capture
# Settings live in capture.env next to this script (see capture.env.example).
set -a; . "$DIR/capture.env"; set +a
SSH=(ssh -i "$DIR/ha_key" -o BatchMode=yes -o UserKnownHostsFile="$DIR/known_hosts"
     -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ConnectTimeout=15
     "$HA_SSH")
LEVELS='{"aioecam":"debug","custom_components.cremalink_ha":"debug"}'

note() { echo "=== $(date -Iseconds) $*" >> "$DIR/halog/ha-$(date +%F).log"; }

set_level() {
  "${SSH[@]}" "bash -l -c 'curl -s -o /dev/null -w %{http_code} -X POST \
    -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\" -H \"Content-Type: application/json\" \
    -d '\''$LEVELS'\'' http://supervisor/core/api/services/logger/set_level'" 2>/dev/null
}

(
  while true; do
    code=$(set_level)
    [ "$code" = 200 ] || note "set_level failed (${code:-no answer})"
    sleep 120
  done
) &
trap 'kill $! 2>/dev/null' EXIT

while true; do
  note "following"
  "${SSH[@]}" "bash -l -c 'ha core logs --follow --lines 20'" 2>&1 \
    | sed -u 's/\x1b\[[0-9;]*m//g' \
    | grep --line-buffered -E "cremalink|aioecam|Home Assistant (initialized|is stopping)" \
    | while IFS= read -r line; do
        printf '%s\n' "$line" >> "$DIR/halog/ha-$(date +%F).log"
      done
  note "disconnected"
  sleep 15
done
