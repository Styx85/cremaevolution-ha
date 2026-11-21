# Long-running capture of the Wi-Fi module's traffic

Scripts used to watch what the coffee machine's Wi-Fi module sends to the
Ayla cloud over two weeks, together with the integration's debug log
([#36](https://github.com/Styx85/cremaevolution-ha/issues/36)). They run on
any always-on Linux box in the LAN (here: a Proxmox host) and survive Home
Assistant restarts.

- `fritz_capture.py`: logs in to a FRITZ!Box and streams its packet capture
  of the LAN bridge (pcap). `--list` shows the capture interfaces.
- `capture.sh`: filters that stream to the module's MAC address and writes
  6-hour pcap files to `pcap/`.
- `halog.sh`: follows the Home Assistant log over SSH, keeps only the
  integration's lines in `halog/ha-YYYY-MM-DD.log`, and re-applies the debug
  log level every 2 minutes (an HA restart resets it).
- `stop.sh` with `ecam-stop.timer`: ends the measurement and sets the log
  level back to warning.
- `analyze.py`: summarizes a capture (peers, DNS, connection attempts, UDP).

## Setup

Everything lives in `/root/ecam-capture` (change `DIR` in the scripts
otherwise).

1. Copy `capture.env.example` to `capture.env` and fill in the module's MAC
   address, the box address and the Home Assistant SSH login.
2. Create `fritz.cred` with `FRITZ_USER=...` and `FRITZ_PASS=...` for a
   FRITZ!Box user with the right to use the packet capture (FRITZ!OS 7.24
   or later for the PBKDF2 login).
3. Put a dedicated SSH key for the Home Assistant SSH add-on in `ha_key`.
   Its host key goes in `known_hosts`.
4. `chmod 600 capture.env fritz.cred ha_key` and `mkdir pcap halog`.
5. Copy the `*.service` and `*.timer` files to `/etc/systemd/system/`, set
   the end date in `ecam-stop.timer`, then
   `systemctl enable --now ecam-capture ecam-halog ecam-stop.timer`.

Credentials never go on a command line. `capture.env`, `fritz.cred`, the key
and all captures are excluded by `.gitignore`. The pcap files contain your
network's traffic to and from the module: don't publish them unreviewed.

Limits: the FRITZ!Box only sees what crosses the box itself. Traffic between
Home Assistant and the module that a repeater or switch passes directly does
not show up. The cloud connections are TLS, so only their timing and size are
visible.

Analyze:

```bash
tcpdump -nn -tttt -r pcap/ecam-20261116-093951.pcap | python3 analyze.py 192.168.1.50
```
