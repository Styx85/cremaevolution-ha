#!/usr/bin/env python3
"""Passively watch a running cremalink-server's LAN session health.

Polls /get_monitor and logs whenever the session state changes: monitor value,
seq_no (resets on a new key exchange), and staleness. Read-only: it never
registers with the machine, so it does not disturb the add-on's session.

  python3 tools/addon_watch.py --server http://192.168.178.46:10280
"""

import argparse
import base64
import json
import time
import urllib.request


def ts() -> str:
    return time.strftime("%H:%M:%S")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--server", required=True)
    p.add_argument("--interval", type=float, default=2)
    p.add_argument("--stale", type=float, default=15, help="seconds without a fresh monitor before flagging STALE")
    args = p.parse_args()

    last_seq = last_value = None
    stale = False
    print(f"{ts()}  watching {args.server}", flush=True)
    while True:
        try:
            with urllib.request.urlopen(f"{args.server}/get_monitor", timeout=5) as r:
                d = json.load(r)
        except Exception as exc:  # noqa: BLE001 - diagnostics tool, log anything
            print(f"{ts()}  SERVER_ERROR {type(exc).__name__}: {exc}", flush=True)
            time.sleep(args.interval)
            continue
        mon = d.get("monitor") or {}
        seq = mon.get("seq_no")
        value = d.get("monitor_b64")
        age = round(time.time() - d["received_at"], 1) if d.get("received_at") else None
        status = None
        if value:
            raw = base64.b64decode(value + "=" * (-len(value) % 4))
            status = raw[9] if len(raw) > 9 else None  # ECAM monitor DATA2: machine status byte
        if seq is not None and last_seq is not None and int(seq) < int(last_seq):
            print(f"{ts()}  NEW_SESSION seq {last_seq} -> {seq} (key exchange happened)", flush=True)
        if value != last_value:
            print(f"{ts()}  monitor changed status_byte={status} value={value} age={age}s seq={seq}", flush=True)
        if age is not None and age > args.stale and not stale:
            print(f"{ts()}  STALE age={age}s seq={seq}", flush=True)
            stale = True
        elif age is not None and age <= args.stale and stale:
            print(f"{ts()}  RECOVERED age={age}s seq={seq}", flush=True)
            stale = False
        last_seq, last_value = seq, value
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
