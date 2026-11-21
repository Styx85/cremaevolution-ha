#!/bin/bash
# End of the measurement: stop both services and put the HA log level back.
DIR=/root/ecam-capture
# Settings live in capture.env next to this script (see capture.env.example).
set -a; . "$DIR/capture.env"; set +a
systemctl stop ecam-capture.service ecam-halog.service
ssh -i "$DIR/ha_key" -o BatchMode=yes -o UserKnownHostsFile="$DIR/known_hosts" \
  "$HA_SSH" "bash -l -c 'curl -s -o /dev/null -X POST \
  -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\" -H \"Content-Type: application/json\" \
  -d '\''{\"aioecam\":\"warning\",\"custom_components.cremalink_ha\":\"warning\"}'\'' \
  http://supervisor/core/api/services/logger/set_level'"
echo "=== $(date -Iseconds) measurement ended" >> "$DIR/halog/ha-$(date +%F).log"
