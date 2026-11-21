#!/bin/bash
# FRITZ!Box LAN-bridge capture, filtered to the coffee machine's Wi-Fi module.
# Files rotate every 6 hours; names must end in .pcap (tcpdump's AppArmor profile).
set -o pipefail
DIR=/root/ecam-capture
# Settings live in capture.env next to this script (see capture.env.example).
set -a; . "$DIR/capture.env"; set +a
python3 -I "$DIR/fritz_capture.py" "$FRITZ_IFACE" \
  | tcpdump -nn -U -Z root -r - -G 21600 -w "$DIR/pcap/ecam-%Y%m%d-%H%M%S.pcap" "ether host $MODULE_MAC"
