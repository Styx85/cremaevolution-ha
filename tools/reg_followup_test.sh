#!/usr/bin/env bash
# Does a follow-up local_reg trigger a fresh key exchange while the machine
# still holds a recent session? Each round: establish a session via POST,
# wait, then send the follow-up variant and record whether a callback came.
# Usage: tools/reg_followup_test.sh <device-ip> [rounds] [gap-seconds]
set -u
DEVICE=${1:?device ip}
ROUNDS=${2:-6}
GAP=${3:-15}
PY=${PY:-/home/caberliner/Projects/cremalink/.venv/bin/python}
PROBE="$(dirname "$0")/lan_probe.py"
port=10400
for round in $(seq 1 "$ROUNDS"); do
  for variant in "POST minimal" "PUT minimal" "PUT httpx"; do
    set -- $variant
    "$PY" -u "$PROBE" --device "$DEVICE" --port $port --timeout 10 reg --method POST --style minimal | tail -1 >/dev/null
    port=$((port + 1))
    sleep "$GAP"
    result=$("$PY" -u "$PROBE" --device "$DEVICE" --port $port --timeout 20 reg --method "$1" --style "$2" | tail -1)
    port=$((port + 1))
    echo "round=$round followup=$1/$2 $result"
    sleep "$GAP"
  done
done
