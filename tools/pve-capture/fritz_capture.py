#!/usr/bin/env python3
"""Stream a FRITZ!Box packet capture of the LAN bridge to stdout (pcap).

Credentials come from fritz.cred (FRITZ_USER=..., FRITZ_PASS=...), never from
the command line. The box address comes from FRITZ_URL (default
http://fritz.box). The caller filters and stores the stream (tcpdump).
"""

import hashlib
import os
import pathlib
import re
import shutil
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BOX = os.environ.get("FRITZ_URL", "http://fritz.box").rstrip("/")
CRED = pathlib.Path(__file__).with_name("fritz.cred")
IFACE = sys.argv[1] if len(sys.argv) > 1 else "1-lan"


def log(msg: str) -> None:
    print(f"fritz_capture: {msg}", file=sys.stderr, flush=True)


def creds() -> tuple[str, str]:
    values = dict(
        line.split("=", 1) for line in CRED.read_text().splitlines() if "=" in line
    )
    return values["FRITZ_USER"].strip(), values["FRITZ_PASS"].rstrip("\n")


def answer(challenge: str, password: str) -> str:
    """PBKDF2 challenge response (login_sid.lua version 2, FRITZ!OS 7.24 and later)."""
    if not challenge.startswith("2$"):
        sys.exit("fritz_capture: the box offers no PBKDF2 login; FRITZ!OS 7.24 or later is needed")
    _, iter1, salt1, iter2, salt2 = challenge.split("$")
    hash1 = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt1), int(iter1))
    hash2 = hashlib.pbkdf2_hmac("sha256", hash1, bytes.fromhex(salt2), int(iter2))
    return f"{salt2}${hash2.hex()}"


def login() -> str:
    user, password = creds()
    with urllib.request.urlopen(f"{BOX}/login_sid.lua?version=2", timeout=10) as resp:
        challenge = ET.fromstring(resp.read()).findtext("Challenge") or ""
    data = urllib.parse.urlencode({"username": user, "response": answer(challenge, password)})
    with urllib.request.urlopen(f"{BOX}/login_sid.lua?version=2", data.encode(), timeout=10) as resp:
        sid = ET.fromstring(resp.read()).findtext("SID") or ""
    if not re.fullmatch(r"[0-9a-f]{16}", sid) or sid == "0" * 16:
        sys.exit("fritz_capture: login failed (user, password or rights)")
    return sid


def interfaces(sid: str) -> None:
    """Print the capture interfaces the box offers (for choosing IFACE)."""
    data = urllib.parse.urlencode({"sid": sid, "page": "cap", "xhr": "1"})
    with urllib.request.urlopen(f"{BOX}/data.lua", data.encode(), timeout=10) as resp:
        text = resp.read().decode("utf-8", "replace")
    for name, label in re.findall(r'name="?ifaceorminor"?\s+value="([^"]+)"[^>]*>.*?(?:<td[^>]*>)?([^<]{0,60})', text, re.S):
        print(name, label.strip())
    if "ifaceorminor" not in text:
        print(text[:400])


def main() -> None:
    sid = login()
    if IFACE == "--list":
        interfaces(sid)
        return
    log(f"logged in, capturing interface {IFACE}")
    query = urllib.parse.urlencode(
        {"ifaceorminor": IFACE, "snaplen": "", "capture": "Start", "sid": sid}
    )
    # The bridge always carries some traffic; a 5-minute silence means a dead stream.
    with urllib.request.urlopen(f"{BOX}/cgi-bin/capture_notimeout?{query}", timeout=300) as resp:
        head = resp.read(4)
        # pcap, the "modified" pcap the box sends, and pcapng
        if head not in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x34\xcd\xb2\xa1",
                        b"\x0a\x0d\x0d\x0a"):
            sys.exit(f"fritz_capture: no pcap stream (got {head!r}); wrong interface or rights")
        out = sys.stdout.buffer
        out.write(head)
        shutil.copyfileobj(resp, out, 4096)
    log("stream ended")


if __name__ == "__main__":
    main()
