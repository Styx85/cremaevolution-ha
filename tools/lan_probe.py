#!/usr/bin/env python3
"""Ayla LAN-mode probe for De'Longhi ECAM machines.

Standalone diagnostic tool to test why the local handshake is flaky. It acts as
the "app" side of the Ayla LAN protocol with raw HTTP on both ends, so request
headers and response framing are fully under control.

Subcommands:
  reg    one local_reg attempt (method/style selectable), then listen for the
         machine's callback; completes key exchange + one monitor read if a
         key file is present.
  matrix run reg for every method/style combination in sequence.
  watch  long-running session with keep-alive, logs every event with
         timestamps (for the app-concurrency and WAN-blocked tests).

The LAN key is read from a JSON file (default ~/.config/cremalink-probe.json):
  {"lan_key": "<base64 string as given by Ayla>", "key_id": 1234, "dsn": "AC000W..."}
The key is never printed.

Stop the Cremalink add-on / server before running: the machine only keeps one
LAN registration, so the probe and the add-on would steal it from each other.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import socket
import sys
import time
import urllib.request
from pathlib import Path

from Crypto.Cipher import AES

DEFAULT_KEY_FILE = Path("~/.config/cremalink-probe.json").expanduser()


def ts() -> str:
    return time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"


def log(event: str, **details) -> None:
    extra = " ".join(f"{k}={v}" for k, v in details.items())
    print(f"{ts()}  {event:<22} {extra}", flush=True)


def local_ip_towards(device_ip: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((device_ip, 80))
        return s.getsockname()[0]


# --- crypto (Ayla LAN session) ---------------------------------------------


def _h(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _derive(lan_key: bytes, seed: bytes) -> bytes:
    return _h(lan_key, _h(lan_key, seed) + seed)


class Session:
    """Both CBC streams persist across messages (chained), as on the device."""

    def __init__(self, lan_key: str, random_1: str, random_2: str, time_1: str, time_2: str):
        # The LAN key is used as the ASCII bytes of its base64 string, not decoded.
        key = lan_key.encode()
        app_seed = (random_1 + random_2 + time_1 + time_2).encode()
        dev_seed = (random_2 + random_1 + time_2 + time_1).encode()
        self.app_sign = _derive(key, app_seed + b"0")
        app_crypto = _derive(key, app_seed + b"1")
        app_iv = _derive(key, app_seed + b"2")[:16]
        self.dev_sign = _derive(key, dev_seed + b"0")
        dev_crypto = _derive(key, dev_seed + b"1")
        dev_iv = _derive(key, dev_seed + b"2")[:16]
        self._enc = AES.new(app_crypto, AES.MODE_CBC, app_iv)
        self._dec = AES.new(dev_crypto, AES.MODE_CBC, dev_iv)
        self.seq = 0

    def seal(self, data: dict) -> dict:
        # seq_no as a string, like cremalink, so only the variables under test differ.
        plain = json.dumps({"seq_no": str(self.seq), "data": data}, separators=(",", ":")).encode()
        self.seq += 1
        padded = plain + b"\x00" * (16 - len(plain) % 16)
        enc = self._enc.encrypt(padded)
        sign = hmac.new(self.app_sign, plain, hashlib.sha256).digest()
        return {"enc": base64.b64encode(enc).decode(), "sign": base64.b64encode(sign).decode()}

    def open(self, msg: dict) -> tuple[dict | None, bool]:
        plain = self._dec.decrypt(base64.b64decode(msg["enc"])).rstrip(b"\x00")
        expected = hmac.new(self.dev_sign, plain, hashlib.sha256).digest()
        sign_ok = hmac.compare_digest(expected, base64.b64decode(msg.get("sign", "")))
        try:
            return json.loads(plain), sign_ok
        except ValueError:
            return None, sign_ok


# --- raw HTTP client for local_reg -----------------------------------------


HTTPX_LIKE_HEADERS = (
    "Accept: */*\r\n"
    "Accept-Encoding: gzip, deflate\r\n"
    "Connection: keep-alive\r\n"
    "User-Agent: python-httpx/0.28.1\r\n"
)


async def send_local_reg(device_ip: str, method: str, style: str, my_ip: str, my_port: int, notify: int) -> tuple[int | None, str]:
    """Send local_reg.json. style=minimal: curl-like compact request; style=httpx: what cremalink sends."""
    reg = {"local_reg": {"ip": my_ip, "notify": notify, "port": my_port, "uri": "/local_lan"}}
    if style == "httpx":
        body = json.dumps(reg).encode()  # httpx default separators, with spaces
        extra = HTTPX_LIKE_HEADERS
    else:
        body = json.dumps(reg, separators=(",", ":")).encode()
        extra = ""
    req = (
        f"{method} /local_reg.json HTTP/1.1\r\n"
        f"Host: {device_ip}\r\n"
        f"{extra}"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "\r\n"
    ).encode() + body
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(device_ip, 80), 5)
        writer.write(req)
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(4096), 5)
        writer.close()
    except (OSError, asyncio.TimeoutError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    line = raw.split(b"\r\n", 1)[0].decode(errors="replace")
    try:
        code = int(line.split()[1])
    except (IndexError, ValueError):
        code = None
    return code, line


# --- raw HTTP server for the machine's callbacks ---------------------------


class Probe:
    def __init__(self, device_ip: str, key: dict | None, monitor_prop: str, verbose: bool):
        self.device_ip = device_ip
        self.key = key
        self.monitor_prop = monitor_prop
        self.verbose = verbose
        self.session: Session | None = None
        self.pending: list[dict] = []
        self.events: list[str] = []
        self.got_callback = asyncio.Event()
        self.got_monitor = asyncio.Event()
        self.got_key_exchange = asyncio.Event()
        self.last_activity = 0.0

    def queue_monitor_read(self) -> None:
        self.pending.append({"cmds": [{"cmd": {
            "cmd_id": 1, "data": "", "method": "GET",
            "resource": f"property.json?name={self.monitor_prop}",
            "uri": "/local_lan/property/datapoint.json",
        }}]})

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")[0]
        try:
            while True:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
                lines = head.decode(errors="replace").split("\r\n")
                method, path, _ = (lines[0].split(" ") + ["", ""])[:3]
                headers = {k.strip().lower(): v.strip() for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)}
                body = await reader.readexactly(int(headers.get("content-length", 0) or 0))
                self.last_activity = time.time()
                self.got_callback.set()
                if self.verbose:
                    log("  <- headers", peer=peer, h=json.dumps(headers))
                status, payload = self.route(method, path, body, peer)
                out = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else b""
                writer.write(
                    f"HTTP/1.1 {status} {'OK' if status < 300 else 'ERR'}\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(out)}\r\n\r\n".encode() + out
                )
                await writer.drain()
                if headers.get("connection", "").lower() == "close":
                    break
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()

    def route(self, method: str, path: str, body: bytes, peer: str) -> tuple[int, dict | None]:
        path = path.split("?")[0]
        if path.endswith("/key_exchange.json"):
            kx = json.loads(body or b"{}").get("key_exchange", {})
            log("<- key_exchange", peer=peer, key_id=kx.get("key_id"), ver=kx.get("ver"), proto=kx.get("proto"))
            if not self.key:
                log("   (no key file: stopping after callback)")
                return 404, None
            if self.key.get("key_id") is not None and kx.get("key_id") != self.key["key_id"]:
                log("   KEY_ID MISMATCH", expected=self.key["key_id"], got=kx.get("key_id"))
            random_2 = base64.b64encode(os.urandom(12)).decode()
            time_2 = str(int(time.time() * 1000))
            self.session = Session(self.key["lan_key"], kx["random_1"], random_2, str(kx["time_1"]), time_2)
            self.got_key_exchange.set()
            self.queue_monitor_read()
            return 202, {"random_2": random_2, "time_2": int(time_2)}

        if path.endswith("/commands.json"):
            if not self.session:
                log("<- commands.json", peer=peer, note="no session yet")
                return 404, None
            data = self.pending.pop(0) if self.pending else {}
            sent = "monitor_read" if "cmds" in data else "property_set" if "properties" in data else "empty"
            log("<- commands.json", peer=peer, method=method, sent=sent)
            return 200, {**self.session.seal(data), "seq": self.session.seq - 1}

        if path.endswith("/datapoint.json") or path.endswith("/ack.json"):
            if not self.session:
                log("<- " + path.rsplit("/", 1)[-1], peer=peer, note="no session")
                return 404, None
            try:
                msg, sign_ok = self.session.open(json.loads(body))
            except (ValueError, KeyError) as exc:
                log("<- datapoint", peer=peer, error=exc)
                return 400, None
            data = (msg or {}).get("data", {})
            name, value = data.get("name"), data.get("value")
            log("<- datapoint", peer=peer, sign_ok=sign_ok, name=name,
                value=(str(value)[:40] + "…") if value else None, decoded=msg is not None)
            if name == self.monitor_prop and value:
                self.got_monitor.set()
            return 200, {}

        log("<- unknown", peer=peer, method=method, path=path, body=body[:120])
        return 404, None


def read_dsn(device_ip: str) -> str:
    """The module answers regtoken.json unauthenticated; host_symname is the DSN."""
    with urllib.request.urlopen(f"http://{device_ip}/regtoken.json", timeout=5) as r:
        return json.load(r)["host_symname"]


def load_key(path: Path) -> dict | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if not data.get("lan_key"):
        return None
    return data


async def start_server(probe: Probe, port: int) -> asyncio.base_events.Server:
    return await asyncio.start_server(probe.handle, "0.0.0.0", port)


async def run_reg(args, method: str, style: str, port: int) -> dict:
    key = load_key(args.key_file)
    probe = Probe(args.device, key, args.monitor_prop, args.verbose)
    my_ip = args.advertise or local_ip_towards(args.device)
    server = await start_server(probe, port)
    try:
        code, line = await send_local_reg(args.device, method, style, my_ip, port, notify=1)
        log("-> local_reg", method=method, style=style, port=port, status=code, line=repr(line))
        t0 = time.time()
        callback = monitor = None
        try:
            await asyncio.wait_for(probe.got_callback.wait(), args.timeout)
            callback = round(time.time() - t0, 2)
            if key:
                await asyncio.wait_for(probe.got_monitor.wait(), args.timeout)
                monitor = round(time.time() - t0, 2)
        except asyncio.TimeoutError:
            pass
        return {"method": method, "style": style, "status": code, "callback_s": callback, "monitor_s": monitor}
    finally:
        server.close()
        await server.wait_closed()


async def cmd_reg(args) -> None:
    result = await run_reg(args, args.method, args.style, args.port)
    print(json.dumps(result))


async def cmd_matrix(args) -> None:
    results = []
    for i, (method, style) in enumerate([("POST", "minimal"), ("PUT", "minimal"), ("POST", "httpx"), ("PUT", "httpx")]):
        if i:
            log("pause", seconds=args.pause)
            await asyncio.sleep(args.pause)
        # A fresh port per run so a late callback cannot be attributed to the wrong attempt.
        results.append(await run_reg(args, method, style, args.port + i))
    print()
    print(f"{'method':<6} {'style':<8} {'status':<7} {'callback_s':<11} monitor_s")
    for r in results:
        print(f"{r['method']:<6} {r['style']:<8} {str(r['status']):<7} {str(r['callback_s']):<11} {r['monitor_s']}")


async def cmd_rekey(args) -> None:
    """One long-lived server keeps the session served; every --gap seconds a follow-up
    local_reg (variant in random order) asks for a fresh key exchange, like cremalink's
    periodic rekey. Records whether the machine actually re-keys."""
    import random

    key = load_key(args.key_file)
    if not key:
        sys.exit(f"rekey needs a key file at {args.key_file}")
    probe = Probe(args.device, key, args.monitor_prop, args.verbose)
    my_ip = args.advertise or local_ip_towards(args.device)
    server = await start_server(probe, args.port)
    variants = [("POST", "minimal"), ("PUT", "minimal"), ("POST", "httpx"), ("PUT", "httpx")] * args.rounds
    random.shuffle(variants)
    tally: dict[str, list[int]] = {}
    try:
        code, _ = await send_local_reg(args.device, "POST", "minimal", my_ip, args.port, notify=1)
        await asyncio.wait_for(probe.got_key_exchange.wait(), 10)
        log("initial session up", status=code)
        for method, style in variants:
            await asyncio.sleep(args.gap)
            probe.got_key_exchange.clear()
            code, _ = await send_local_reg(args.device, method, style, my_ip, args.port, notify=1)
            try:
                await asyncio.wait_for(probe.got_key_exchange.wait(), 10)
                ok = 1
            except asyncio.TimeoutError:
                ok = 0
                # Recover with a POST so every trial starts from a live session.
                await send_local_reg(args.device, "POST", "minimal", my_ip, args.port, notify=1)
                try:
                    await asyncio.wait_for(probe.got_key_exchange.wait(), 10)
                except asyncio.TimeoutError:
                    log("   recovery POST also failed")
            t = tally.setdefault(f"{method}/{style}", [0, 0])
            t[0] += 1
            t[1] += ok
            log("== trial", variant=f"{method}/{style}", status=code, rekeyed=bool(ok))
    finally:
        server.close()
    print()
    for k, (n, ok) in sorted(tally.items()):
        print(f"{k:<14} rekeyed {ok}/{n}")


async def cmd_send(args) -> None:
    """Fresh session, send one ECAM frame via data_request, then follow the monitor."""
    key = load_key(args.key_file)
    if not key:
        sys.exit(f"send needs a key file at {args.key_file}")
    dsn = key.get("dsn") or await asyncio.to_thread(read_dsn, args.device)
    probe = Probe(args.device, key, args.monitor_prop, args.verbose)
    my_ip = args.advertise or local_ip_towards(args.device)
    server = await start_server(probe, args.port)
    try:
        await send_local_reg(args.device, "POST", "minimal", my_ip, args.port, notify=1)
        await asyncio.wait_for(probe.got_monitor.wait(), 10)
        # Same encoding as cremalink: frame bytes + 4-byte unix timestamp, base64, newline.
        frame = bytes.fromhex(args.hex) + int(time.time()).to_bytes(4, "big")
        probe.pending.append({"properties": [{"property": {
            "base_type": "string", "dsn": dsn, "name": "data_request",
            "value": base64.b64encode(frame).decode() + "\n",
        }}]})
        log("-> queue command", hex=args.hex)
        await send_local_reg(args.device, "PUT", "minimal", my_ip, args.port, notify=1)
        t_end = time.time() + args.follow
        while time.time() < t_end:
            await asyncio.sleep(5)
            probe.queue_monitor_read()
            await send_local_reg(args.device, "PUT", "minimal", my_ip, args.port, notify=1)
    finally:
        server.close()


async def cmd_watch(args) -> None:
    key = load_key(args.key_file)
    if not key:
        sys.exit(f"watch needs a key file at {args.key_file}")
    probe = Probe(args.device, key, args.monitor_prop, args.verbose)
    my_ip = args.advertise or local_ip_towards(args.device)
    server = await start_server(probe, args.port)
    log("watch start", advertise=f"{my_ip}:{args.port}", keepalive=args.keepalive, monitor_every=args.monitor_every)
    last_monitor = 0.0
    try:
        while True:
            now = time.time()
            notify = 0
            if now - last_monitor >= args.monitor_every:
                if probe.session:
                    probe.queue_monitor_read()
                notify = 1
                last_monitor = now
            idle = round(now - probe.last_activity, 1) if probe.last_activity else None
            # PUT only refreshes a live session; once the machine went quiet, start over with POST.
            method = "POST" if idle is None or idle > args.recover_after else args.method
            if method == "POST":
                log("   (re)registering with POST", idle_s=idle)
            code, line = await send_local_reg(args.device, method, args.style, my_ip, args.port, notify=notify)
            log("-> local_reg", status=code, notify=notify, idle_s=idle, session=bool(probe.session))
            if code is None or code >= 400:
                log("   REG FAILED", line=repr(line))
            await asyncio.sleep(args.keepalive)
    finally:
        server.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--device", required=True, help="machine IP")
    p.add_argument("--advertise", help="IP to announce to the machine (default: auto)")
    p.add_argument("--port", type=int, default=10290)
    p.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE)
    p.add_argument("--monitor-prop", default="d302_monitor")
    p.add_argument("--timeout", type=float, default=20)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reg")
    r.add_argument("--method", choices=["POST", "PUT"], default="POST")
    r.add_argument("--style", choices=["minimal", "httpx"], default="minimal")
    m = sub.add_parser("matrix")
    m.add_argument("--pause", type=float, default=15)
    k = sub.add_parser("rekey")
    k.add_argument("--rounds", type=int, default=5)
    k.add_argument("--gap", type=float, default=20)
    s = sub.add_parser("send")
    s.add_argument("--hex", required=True, help="ECAM frame without timestamp, e.g. 0d07840f02015512 (wakeup)")
    s.add_argument("--follow", type=float, default=90)
    w = sub.add_parser("watch")
    w.add_argument("--method", choices=["POST", "PUT"], default="PUT")
    w.add_argument("--style", choices=["minimal", "httpx"], default="minimal")
    w.add_argument("--keepalive", type=float, default=25)
    w.add_argument("--monitor-every", type=float, default=60)
    w.add_argument("--recover-after", type=float, default=30)
    args = p.parse_args()
    asyncio.run({"reg": cmd_reg, "matrix": cmd_matrix, "rekey": cmd_rekey, "send": cmd_send, "watch": cmd_watch}[args.cmd](args))


if __name__ == "__main__":
    main()
