# De'Longhi ECAM over Ayla LAN mode: protocol notes

Practical notes for talking to De'Longhi Coffee Link machines (Ayla Wi-Fi
module) **locally**, without the cloud in the loop. Written so that anyone
building a client can reuse them.

- **Hardware basis:** PrimaDonna Soul ECAM610.75.MB, Ayla module reporting
  `software_version` "Millcore_demo 2.0.0 Jun 18 2020", `mcu_host_version`
  2.0.0, `oem_host_version` v1.4.
- **Date:** October 2026.
- **Tools:** [`tools/lan_probe.py`](../tools/lan_probe.py) (raw HTTP on both
  sides), plus the async library *aioecam* built on these notes.
- **Confidence labels:** **[measured]** = observed on the machine above with
  the numbers given; **[reported]** = from other projects, not reproduced
  here; **[open]** = unresolved.

A2 statistics are covered in depth in
[ecam-a2-statistics.md](https://github.com/Styx85/cremalink/blob/ha-compat/2026.9/docs/ecam-a2-statistics.md);
section 7 below only adds what is specific to LAN mode.

---

## 1. Topology: the machine is the HTTP client

In LAN mode the roles are inverted. Your software runs an **HTTP server**. You
tell the machine where it is, and the machine then **calls you**.

```
you  ──PUT/POST http://<machine>:80/local_reg.json──▶ machine
you  ◀─POST /local_lan/key_exchange.json────────────── machine
you  ◀─GET  /local_lan/commands.json────────────────── machine   (after each notify)
you  ◀─POST /local_lan/property/datapoint.json──────── machine   (property values)
```

Consequences:

- The machine must be able to reach your IP and port. This is the most common
  reason for "nothing happens": firewalls, VLANs, Docker bridge networking.
- Every session message is encrypted and signed (section 4). Only
  `local_reg.json` and `regtoken.json` on the machine are plain HTTP.

## 2. Endpoints on the machine (port 80, plain HTTP)

### 2.1 `GET /regtoken.json` (no authentication)

```json
{"regtoken":"f3af0e","registered":1,"registration_type":"AP-Mode","host_symname":"AC000W0XXXXXXXX"}
```

`host_symname` is the **DSN**. **[measured]** After a restart of the Wi-Fi
module **without internet access** the module answers:

```json
{"regtoken":null,"registered":1,"registration_type":"","host_symname":""}
```

In that state it **refuses LAN mode**: see 2.2. `regtoken.json` is therefore a
cheap health check: an empty `host_symname` means "LAN mode will not work".

### 2.2 `local_reg.json`

Body (both methods):

```json
{"local_reg":{"ip":"192.168.1.10","notify":1,"port":10280,"uri":"/local_lan"}}
```

Answers `202 Accepted`. Request headers do not matter **[measured]**: a
minimal curl-like request and an httpx-style request with `Accept`,
`Accept-Encoding`, `Connection: keep-alive` and `User-Agent` behaved the
same, and aiohttp works too. (OpenCoffeeLink **[reported]** got HTTP 400 for
Node `fetch` default headers; not reproduced here.)

**POST vs PUT is the critical detail [measured]:**

| Situation | `POST local_reg` | `PUT local_reg` |
|---|---|---|
| A session is live | new key exchange **10/10** | **no** key exchange, **0/10** |
| No live session | new key exchange | new key exchange |
| `notify: 1` with a live session | — | machine polls `commands.json` within about 0.05–1.3 s |

Rules that follow:

- Start a session with **POST**. Use **PUT** only for keep-alive and to notify
  the machine about pending commands.
- **Never discard your session keys and expect a PUT to give you new ones.**
  The machine keeps using the old session, and you can no longer decrypt or
  encrypt anything. (cremalink's server re-keys every 60 s via PUT. This
  matches the "hollow session" reports: key exchange looks fine, then only
  empty polls, no monitor data, commands fail with 502.)

**Module not LAN-ready [measured]:** with an empty `host_symname` the module
returns **404** to both methods. After internet access was restored it took
**152 s** until `host_symname` was back, and a little longer (under two
minutes) until `local_reg` returned 202 again. A running session, in
contrast, survives without internet (section 6).

## 3. Callback endpoints (on your server, prefix = `uri` from `local_reg`)

| Request | Body | Your answer |
|---|---|---|
| `POST /key_exchange.json` | `{"key_exchange":{"ver":1,"proto":1,"key_id":<int>,"random_1":"<str>","time_1":<int>}}` | `202` `{"random_2":"<str>","time_2":<int>}` |
| `GET /commands.json` (POST seen too) | — | `200` `{"enc":"…","sign":"…","seq":<n>}`; an empty command is fine |
| `POST /property/datapoint.json` | `{"enc":"…","sign":"…"}` | `200` `{}` |
| `POST /property/datapoint/ack.json` | `{"enc":"…","sign":"…"}` | `200` `{}` |

- `key_id` identifies the LAN key (Ayla `lanip_key_id`). Checking it is a
  cheap way to reject a machine configured with another key.
- `random_2`: any random string. 16 base64 characters work; cremalink uses
  base64 of 12 random bytes. `time_2`: milliseconds work.
- Plain, simple HTTP/1.1 responses with `Content-Length` work. aiohttp's
  `json_response` is accepted **[measured]**. FiveElements **[reported]**
  that the module's HTTP client rejected Next.js response framing.
- Requests should only be accepted from the machine's IP address. The
  callback server is unauthenticated apart from the session crypto.

## 4. Session crypto

All values are strings exactly as exchanged (`time_1`/`time_2` as decimal
text). `L` = the LAN key **as the ASCII bytes of its base64 text, not
decoded**. `H(k, m)` = HMAC-SHA256.

```
seed_app = random_1 ‖ random_2 ‖ time_1 ‖ time_2
seed_dev = random_2 ‖ random_1 ‖ time_2 ‖ time_1
K(seed, c) = H(L, H(L, seed ‖ c) ‖ seed ‖ c)

app_sign  = K(seed_app, "0")    dev_sign  = K(seed_dev, "0")
app_key   = K(seed_app, "1")    dev_key   = K(seed_dev, "1")
app_iv    = K(seed_app, "2")[:16]  dev_iv = K(seed_dev, "2")[:16]
```

- app → machine: AES-256-CBC with `app_key`/`app_iv`, signed with
  `app_sign`. Machine → app: `dev_*`.
- **Each direction is one CBC stream chained across all messages** of the
  session. Equivalently, the next IV is the last ciphertext block. Skipping or
  losing a message desynchronises that direction until the next key exchange,
  so decrypt every message, even one whose signature fails.
- Plaintext: `{"seq_no":"<n>","data":{…}}`, zero padded to a multiple of 16
  with **at least one** zero byte. `seq_no` as a string works.
- `sign` = base64(HMAC-SHA256(sign key, **plaintext without padding**)).
- Known-answer vector (cremalink and aioecam agree): `L =
  "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdA=="`, `random_1 = "abcdefghijklmnop"`,
  `random_2 = "QUJDREVGR0hJSktM"`, `time_1 = "123456"`, `time_2 =
  "1700000000000"`; sealing `{"seq_no":"0","data":{}}` gives `enc =
  "GBSVEjtqqhaSPywljdlOB/s8TquMcJkrv2WYqQ2QKMQ="`, `sign =
  "whBhog+jdTd+qhw3LcC6cB0XQrOGydieS9H/wqARKuQ="`.

## 5. Messages inside a session (`data`)

**Write a property (app → machine):**

```json
{"properties":[{"property":{"base_type":"string","dsn":"AC000W…","name":"data_request","value":"<base64>\n"}}]}
```

String values carry a trailing newline, as the official app sends them.

**Read a property (app → machine):** the machine then POSTs a datapoint.

```json
{"cmds":[{"cmd":{"cmd_id":1,"data":"","method":"GET","resource":"property.json?name=d302_monitor","uri":"/local_lan/property/datapoint.json"}}]}
```

**Datapoint (machine → app):** `{"name":"d302_monitor","value":"…","base_type":"string"}`

**The machine also sends datapoints you did not ask for [measured]**, so
always route by `name`:

- `app_device_connected` (a Unix timestamp written by the official app via the
  cloud) and echoes of `data_request` written by the app (e.g. `84 0F 03 02` +
  timestamp + 4-byte app id `20 40 35 EF`). Cloud-side property changes reach
  LAN clients.
- Right after a module restart: `oem_host_version`, `software_version`,
  `mcu_host_version` and others.

cremalink treats *every* datapoint as the monitor value. That is why its
status sometimes shows a timestamp or command echo and the HA entities go
unknown or look stuck.

## 6. Session behaviour [measured]

- Between notifications the machine is silent. Liveness can be checked
  cheaply: `PUT local_reg` with `notify: 1` must be followed by a
  `commands.json` poll within a second or two. If no poll comes, register
  again with POST.
- A keep-alive every 20–25 s held one session for 15 min with the machine's
  internet access blocked: 16/16 fresh POST sessions also succeeded, 20
  monitor reads, no failures. A 90 s read-only run through aiohttp: 1
  registration, 1 key exchange, 0 liveness failures.
- Using the official app via the cloud (it woke the machine) did **not**
  disturb a live LAN session.
- **[reported]** (FiveElements) The module keeps a single `local_reg`
  registration. A second LAN client takes it over. Not reproduced here, but
  it is consistent with "only one integration at a time".
- **[open]** Monitor pushes: in raw-probe sessions the monitor changed about
  every second while the machine was waking, more often than it was read. In
  aioecam sessions every monitor datapoint was the answer to a read (4 reads →
  4 datapoints) and nothing changed between reads. Until this is understood,
  **poll the monitor** while the machine is awake (every 2–3 s) instead of
  relying on pushes.

## 7. ECAM frames

ECAM frames travel base64-encoded in string properties: `data_request`
(app → machine), `d302_monitor` (machine status) and `data_response`
(replies such as A2). Eletta-class machines (ECAM45x) use `app_data_request`,
`d302_monitor_machine` and an app id **[reported, cremalink device maps]**.

### 7.1 Frame layout

```
dir | len | opcode | flags | payload … | crc_hi | crc_lo
```

- `dir`: `0x0D` app → machine, `0xD0` machine → app.
- `len`: bytes after `dir`, including `len` itself and the CRC.
- CRC-16/CCITT, polynomial 0x1021, **init 0x1D0F**, over everything before
  it. Verified on all 61 stored commands of the cremalink device maps.
- **Property value** = base64(frame ‖ 4-byte big-endian Unix timestamp ‖
  [4-byte app id on Eletta]). The machine's values sometimes lack base64
  padding.

### 7.2 Power and housekeeping (opcode `0x84`, same on all families)

| Frame | Meaning |
|---|---|
| `0D 07 84 0F 02 01 55 12` | wake up |
| `0D 07 84 0F 01 01 00 41` | standby |
| `0D 07 84 0F 03 02 56 40` | refresh app id (official app) |

A response with opcode `0xA9` (`D0 07 A9 F0 01 00 …`) appeared while the app
was active **[open]**.

### 7.3 Monitor (opcode `0x75`, 13-byte payload)

```
0 accessory | 1-2 switches | 3-4 alarms | 5 status | 6 action | 7 progress | 8-9 alarms | 10-12 unused
```

Observed sequences **[measured]**:

| Situation | status | action → | progress |
|---|---|---|---|
| Standby | 0 | stays non-zero (2, 5, …) | 100 or 0 |
| Wake-up (about 30–40 s to ready) | 1 | 1 → 2 → 5 (heating) → 6 | 0 → 100 during action 5 |
| Ready, idle | 7 | 0 | 0 |
| **Espresso** | **7 (stays "ready")** | 4 → 6 → 8 → 9 → 11 → 0 | rises during 8–11 |
| Going to standby (about 10–20 s) | 2 → 0 | 4 → 7 → 5 → 1 → 2 | 50 → 100 |

Consequences:

- "Busy" cannot be derived from `status` alone: during a beverage the status
  stays 7. And in standby `action` is non-zero, so it cannot be derived from
  `action` alone either. What worked: *busy = status ∉ {0, 7}, or status = 7
  and action ≠ 0*.
- **While the machine sleeps, the module returns the last monitor frame byte
  for byte, timestamp included.** An unchanged monitor does not mean a dead
  connection.
- `alarms[0]` bit 0 was **set with a freshly filled water tank**. On this
  machine it is not "water tank empty" (also cremalink#249).
- Accessory values seen: `0` none, `4` milk carafe in its clean position.

### 7.4 Beverages (opcode `0x83`)

The stored beverage frames from the cremalink device maps work as they are.
The meaning of the ingredient bytes is not fully established **[open]**, so
parametric beverages are not documented here.

## 8. A2 statistics over LAN [measured]

Request and response layout are in the
[A2 document](https://github.com/Styx85/cremalink/blob/ha-compat/2026.9/docs/ecam-a2-statistics.md).
Specific to LAN mode:

- Write the A2 frame to `data_request`. The reply arrives as a pushed
  `data_response` datapoint, so there is no cloud polling and no timestamp
  filtering against stale cloud values.
- The complete table (62 values, 7 pages of up to 10) was read in **7 s**,
  without timeouts, **also while the machine was in standby**.

## 9. Without internet [measured, small sample]

| Action | WAN blocked | Notes |
|---|---|---|
| Keep and re-create sessions, read monitor | ✅ 15 min | |
| Standby command | ✅ | |
| Wake command | ❌ once, ✅ once | The failure came after a long standby with an empty water tank; the success after 2 min of standby with a full tank. Not conclusive. |
| New sessions after a **module restart** | ❌ | Module refuses LAN mode until it reaches the cloud (2.1, 2.2) |

Practical advice: verify every command by its effect on the monitor, and
offer a cloud fallback for waking if available. Blocking the machine's
internet access is possible, but the module then cannot recover LAN mode by
itself after a power cut.

## 10. Open questions

- What makes the machine push monitor changes in some sessions and not in
  others (section 6)?
- Does waking after a *long* standby really require the cloud?
- Meaning of the `0xA9` response and of the beverage ingredient bytes.
- Is `local_reg` really limited to one client on this firmware?

## 11. Related work

- [cremalink](https://github.com/miditkl/cremalink) and
  [cremalink-ha](https://github.com/miditkl/cremalink-ha) by @miditkl: the
  original local implementation, device maps and crypto.
- [OpenCoffeeLink](https://github.com/maximevince/OpenCoffeeLink): ECAM frame
  and LAN-mode notes from the Android app.
- [delonghi-coffeelink-local](https://github.com/FiveElements/delonghi-coffeelink-local):
  Node.js LAN server, single-slot and HTTP framing observations.
- Discussions in cremalink#212 and cremalink-ha#65 (wake-up and cloud
  dependency reports).
