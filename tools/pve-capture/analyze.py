#!/usr/bin/env python3
"""Summarize the module's traffic from tcpdump text output on stdin.

Usage: tcpdump -nn -tttt -r ecam-*.pcap | python3 analyze.py <module IP>
"""

import collections
import ipaddress
import re
import sys
from datetime import datetime

if len(sys.argv) != 2:
    sys.exit(__doc__)
MOD = sys.argv[1]
LINE = re.compile(r"^(\S+ \S+) (?:IP|IP6) (\S+) > (\S+): (.*)$")
DNS_Q = re.compile(r"\b(A|AAAA|SRV|PTR)\? (\S+)")

def _local(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def split(addr: str) -> tuple[str, str]:
    host, _, port = addr.rstrip(":").rpartition(".")
    return host, port

flows = collections.Counter()
flow_bytes = collections.Counter()
syns = []  # (time, peer, port) outbound connection attempts
dns_q = collections.Counter()
udp_out = collections.defaultdict(list)
other = collections.Counter()
first = last = None

for raw in sys.stdin:
    m = LINE.match(raw.strip())
    if not m:
        kind = raw.split()[2] if len(raw.split()) > 2 else "?"
        other[kind] += 1
        continue
    ts, src, dst, rest = m.groups()
    first = first or ts
    last = ts
    (sh, sp), (dh, dp) = split(src), split(dst)
    outbound = sh == MOD
    peer, pport = (dh, dp) if outbound else (sh, sp)
    proto = "udp" if "UDP" in rest or re.search(r"\b(A|AAAA)\?|NTP|bootp|BOOTP", rest) else "tcp"
    length = int(re.search(r"length (\d+)", rest).group(1)) if "length" in rest else 0
    flows[(peer, pport, proto)] += 1
    flow_bytes[(peer, pport, proto)] += length
    if outbound and "Flags [S]" in rest and not _local(dh):
        syns.append((ts, dh, dp))
    if outbound and proto == "udp" and not _local(dh):
        udp_out[(dh, dp)].append(ts)
    q = DNS_Q.search(rest)
    if q and outbound:
        dns_q[q.group(2)] += 1

print(f"span: {first} -> {last}")
print("\n== peers by packets (peer, port, proto: packets, payload bytes)")
for (peer, port, proto), n in flows.most_common(25):
    print(f"  {peer:>16} {port:>6} {proto}: {n:7d} pkts {flow_bytes[(peer, port, proto)]:9d} B")
print("\n== DNS queries by the module")
for name, n in dns_q.most_common(20):
    print(f"  {n:5d} {name}")
print("\n== outbound TCP connection attempts to the internet, per destination")
by_dest = collections.Counter((d, p) for _, d, p in syns)
for (d, p), n in by_dest.most_common(15):
    times = [t[11:16] for t, dd, pp in syns if (dd, pp) == (d, p)]
    print(f"  {d}:{p}  {n} times  first {times[0]} last {times[-1]}")
print("\n== outbound UDP to the internet (dest: count, first, last, typical gap s)")
for (d, p), times in sorted(udp_out.items(), key=lambda kv: -len(kv[1]))[:10]:
    stamps = [datetime.fromisoformat(t) for t in times]
    gaps = sorted((b - a).total_seconds() for a, b in zip(stamps, stamps[1:]))
    med = gaps[len(gaps) // 2] if gaps else None
    print(f"  {d}:{p}  {len(times)}  {times[0][11:19]}  {times[-1][11:19]}  median gap {med}")
print("\n== non-IP frames", dict(other.most_common(8)))
