# De'Longhi ECAM over Ayla LAN mode: protocol notes

Practical notes for talking to De'Longhi Coffee Link machines (Ayla Wi-Fi
module) **locally**, without the cloud in the loop. Written so that anyone
building a client can reuse them.

- **Hardware basis:** PrimaDonna Soul ECAM610.75.MB, Ayla module reporting
  `software_version` "Millcore_demo 2.0.0 Jun 18 2020", `mcu_host_version`
  2.0.0, `oem_host_version` v1.4.
- **Date:** October 2026 (updated 2026-10-19 with the M7 findings: settings,
  profiles, recipes, custom beverages, monitor freshness).
- **Tools:** [`tools/lan_probe.py`](../tools/lan_probe.py) (raw HTTP on both
  sides), plus the async library [aioecam](https://github.com/Styx85/aioecam)
  built on these notes. Raw measurement logs for the M7 checks are in its
  [`docs/m7-protocol.md`](https://github.com/Styx85/aioecam/blob/main/docs/m7-protocol.md).
- **Confidence labels:** **[measured]** = observed on the machine above with
  the numbers given; **[reported]** = from other projects, not reproduced
  here; **[app]** = from a static analysis of the Coffee Link app 4.9.6
  (what the app does, not proof of firmware behaviour); **[open]** = unresolved.

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

**Answers do not all come back on `data_response` [measured].** The machine
answers some requests by republishing a property of its own:

| Request | Opcode | Answer arrives as |
|---|---|---|
| Statistics page | `0xA2` | `data_response` |
| Read a setting | `0x95` | the setting's property, e.g. `d282_mchn_sett_aoff` |
| Read a recipe | `0xA6` | the recipe's property, e.g. `d039_1_rec_espresso` |
| Select a profile | `0xA9` | `data_response`, but sometimes only retrievable by reading it |

So decode every frame-carrying datapoint, keep only verified `0xD0` answers
and match them by opcode plus their identifiers (parameter id, profile slot,
beverage id), never by property name. Two quirks seen on the Soul:

- After selecting slot 2, the first read of `data_response` returned the
  *previous* acknowledgement (`d007a9f001003b3c`) before `d007a9f002006e6f`.
  Ignore acknowledgements for other slots instead of treating them as a
  refusal.
- A second identical recipe request got no new publication. Remember which
  property carried the answer and read it after about 0.5 s without an
  answer. Cache the property name, not the value; why the second publication
  was missing is **[open]**.

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
- **Polling sees physical changes while the machine is awake [measured].**
  Alternating plain monitor reads and explicit monitor requests (`0D 05 75
  0F`, section 7.3) every 2–3 s showed water-tank removal/replacement and
  milk-carafe attach/detach within seconds (table in 7.3).
- **In standby nothing is fresh [measured].** Both a plain read and a read
  after a monitor request returned the same frozen frame, timestamp included.
  Its tank and accessory bits describe some earlier moment, not the present.
  Settings and recipes, in contrast, *are* readable in standby.
- **Without internet the module publishes no changes [measured,
  2026-10-19].** With the machine's internet blocked, plain reads kept
  returning the frame from before a wake-up or switch-off; only a `0x75`
  monitor request made the module publish the current state, about 1–2 s
  after it fetched the request (details in section 9). **So poll with
  monitor requests and wait for a newer payload timestamp**, not with plain
  reads. **[open]** whether the module pushes changes by itself when it can
  reach the cloud (raw-probe sessions on 2026-10-18 saw the monitor change
  about every second while waking).
- **[app]** Over Wi-Fi the Coffee Link app sends no monitor requests: it
  reads `d302_monitor` and reacts to the Ayla SDK's property-change
  notifications (locally or via the cloud), with a 60 s monitor timer. The
  monitor-mode frames (`0x60`, `0x70`, `0x75`) are only built in its
  Bluetooth path. Offline, the app would therefore not see changes either.

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

The `0xA9` answer `D0 07 A9 F0 01 00 3B 3C` seen while the official app woke
the machine is the **profile-select acknowledgement** for profile 1 (section
7.7): the app selects a profile when it connects. *(Resolved; previously
listed as open.)*

Standby `84 0F 01 01` worked on the Soul. Arbuzov's BLE integration
**[reported]** found that it does not work on every model (Dinamica Plus
ECAM370.95) and only enables it for known product codes.

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
- **Correction:** an earlier version of this document said `alarms[0]` bit 0
  was "set with a freshly filled water tank" and therefore not a tank signal.
  That frame was the **frozen standby frame** (section 6). While awake the
  tank bits behave as expected (table below). Treat any tank or accessory
  value from a sleeping machine as stale.
- Ask for a fresh frame with **`0D 05 75 0F DA 25`** (opcode `0x75`, flag
  `0x0F`, no payload). Also documented by longshot and DlghIoT (§4.1)
  **[reported]**; used here while awake **[measured]**.

Physical changes while awake, ECAM610.75 **[measured]** (switch word = payload
bytes 1–2, alarm word = payload bytes 3–4 and 8–9, both little-endian; bit
names from longshot/Arbuzov **[reported]**):

| Action | accessory | switches | alarms | Decoded |
|---|---|---|---|---|
| Frozen standby frame | `4` | `4002` | `01000000` | stale (water level low, tank empty, milk clean) |
| Water tank removed | `0` | `1000` | `00200000` | switch 4 *WaterTankAbsent*, alarm 13 *TankInPosition* |
| Water tank put back | `0` | `0000` | `00000000` | cleared |
| Milk carafe attached | `2` | `0001` | `00000000` | accessory *latte_crema_hot*, switch 8 *IfdCaraffe* |
| Milk carafe removed | `0` | `0000` | `00000000` | cleared |

Reported names for the remaining bits (longshot, Arbuzov; not all checked
here). Switches: 0 water spout, 1 motor up, 2 motor down, 3 coffee waste
container, 5 knob, 6 water level low, 7 coffee jug, 9 chocolate tank, 10 clean
knob, 13 door opened, 14 pre-ground door. Alarms: 0 empty water tank, 1 waste
container full, 2 descale, 3 replace water filter, 4 ground too fine, 5 beans
empty, 6 service, 7 heater probe, 8 too much coffee, 9 infuser motor, 10
steamer probe, 11 empty drip tray, 12 hydraulic circuit, 14 clean knob, 16
tank too full, 17 bean hopper absent, 18 drip grid, 19 infuser not in place,
20 not enough coffee, 21–27 module/grinder/fan/board faults. Eletta switch
bits 7–15 differ (DlghIoT).

Status values reported elsewhere: 3 heating, 4 descaling, 5 steam, 6
recovery/brewing, 8 rinsing, 10 milk, 11 hot water, 12 milk cleaning, 14
changing filter, 16 chocolate. **[measured]** here: 0, 1, 2, 7 and 11 (a
custom hot-water command dispensed with status 11). The `action` codes
(1, 2, 4–9, 11) are named by no source **[open]**.

Accessory values: `0` none, `2` hot milk carafe, `4` milk carafe in its
clean position **[measured]**; 1 hot-water spout, 3 chocolate, 6 cold milk,
7 cold milk clean **[reported]**. On the Soul hot water comes from the central
outlet; accessory `0` must not block it.

### 7.4 Beverages (opcode `0x83`)

```
0D LEN 83 F0 <beverage> <action> <recipe TLV…> <(profile_slot << 2) | operation> <crc>
```

- `action`: `01` start, `02` stop (longshot also lists next step, stop
  program, advanced/programming modes **[reported]**).
- Recipe parameters are TLVs: tag, then a value of **2 bytes** for tags
  `0x01` coffee ml, `0x09` milk, `0x0F` water ml, otherwise 1 byte.
- Tag names from the parameter enum of the Coffee Link app 4.9.6 (static
  analysis, 2026-10-19) **[app]**:

  | Tag | App name | Tag | App name |
  |---|---|---|---|
  | `0x00` | TEMP | `0x0D` | THE_TEMP (tea) |
  | `0x01` | COFFEE | `0x0F` | HOT_WATER |
  | `0x02` | TASTE (0 pre-ground, 1–5 extra mild … extra strong) | `0x18` | PROGRAMABLE |
  | `0x04` | BLEND | `0x19` | VISIBLE |
  | `0x08` | DUExPER (double) | `0x1A` | VISIBLE_IN_PROGRAMMING |
  | `0x09` | MILK (tenths of a second) | `0x1B` | INDEX_LENGTH |
  | `0x0A` | MILK_TEMP | `0x1C` | ACCESSORIO |

  **Correction:** earlier versions of this document and aioecam ≤ 0.1.0b6
  called `0x1B` *temperature*. The app names it `INDEX_LENGTH`, a size index
  (its size enum knows SMALL 0, MID 1, LARGE 2, NOT_SET 255; live values such
  as 4 do not fit it, so the exact meaning is open). Raw values reported
  below are correct; only the name was wrong.
- Units **[app]**: coffee (`0x01`) and hot water (`0x0F`) are millilitres in
  the current parameter lists; milk (`0x09`) is a duration in tenths of a
  second (Soul profile 1 cappuccino `210` = 21 s). Which tags are 2 bytes wide
  comes from a server-side parameter table in the app; "`0x01`, `0x09`, `0x0F`
  are wide" matches every Soul frame but is not a rule for all models.
- The app sends only tags `< 23` plus `27, 28, 31, 33, 38, 39`, and drops
  `0x08` (APK rule `E0`, via lodzen **[reported]**). The explicit `0x08` drop
  is in app versions 4.7.2 and 4.9.6 but not in 4.2.3 **[app]**; aioecam
  follows the newer builder.
- **Keep the machine's tag order.** The Soul's own frames are not sorted
  (espresso: `01, 1B, 02, 04`). With these rules aioecam rebuilds every one of
  the 61 stored beverage frames byte for byte **[measured, offline]**.
- The trailer's low two bits are the **operation** **[app]**: 0 delete,
  1 save, 2 prepare, 3 prepare and save (the app's inversion variants 5–7 are
  not modelled). A normal brew uses 2, and every stored Soul frame does.
  Earlier versions of this document called these bits accessory bits; the
  accessory is recipe tag `0x1C`.
- **Custom amounts work [measured]:** reading the hot-water recipe (profile
  1: water 250 ml, tag `0x1B` = 1) and sending it with water 100 ml,
  `0d0d83f010010f00641b0106e5e1`, dispensed about 100 ml (status 11, back to
  ready after 43 s). The saved 250 ml recipe was unchanged afterwards.

### 7.5 Write session (`device_connected`)

Writes that change machine settings (`0x90`) or the profile (`0xA9`) only
commit inside an announced session. On the Soul the announce is a write of the
**bare Unix timestamp** as a string to `device_connected`, without the
newline that `data_request` values carry. lodzen **[reported]** a lifetime of
about 300 s; refresh after 240 s like the app. Lifetime and renewal were not
measured here. Eletta machines instead use the `0xE8` handshake
(`0D 06 E8 F0 00 ED 7C`) plus app-id registration on `app_device_connected`
(ts + app id; DlghIoT **[reported]**); not implemented or tested.

Power and beverage commands did not need the announce **[measured]**.

### 7.6 Settings (`0x95` read, `0x90` write)

```
read:  0D 07 95 F0 <param:2>                → answer 95 F0 <param:2> <u32> [descriptor tail]
write: 0D 0B 90 <flag> <param:2> <value:4>  → answer 90 <flag> <param:2> <status: 00 = ok>
```

The write flag is `0x0F` for parameters below 1000, else `0xF0` (app rule).
Confirm a write by the acknowledgement **and** by reading the value back.

| Parameter | Meaning | Values |
|---|---|---|
| `0x003E` | auto-off | 0 = 15 min, 1 = 30 min, 3 = 3 h **[measured]** (3 h set at the display read back as 3, 2026-10-20); 2 = 1 h **[app]**. The app's Wi-Fi branch maps 1 h → 2, 3 h → 3 (same as lodzen); its non-Wi-Fi branch uses 3 = 2 h, 4 = 3 h (same as Arbuzov), which explains the conflicting sources |
| `0x0032` | water hardness | 0–3 = displayed level 1–4 (read 2 = level 3) |
| `0x003F` | bit mask | 0x04 sounds, 0x08 cup light, 0x10 energy saving, 0x20 cup warmer; read-modify-write **[reported]** |
| `0x003D` | water temperature | **[reported]** |

Measured: auto-off read `0`, written `1` and read back, restored to `0`. Both
reads also worked in standby.

### 7.7 User profiles (`0xA4` names, `0xA9` select)

```
names:  0D 07 A4 F0 <first> <count>   → A4 F0 <first> <last> <entries…>
select: 0D 06 A9 F0 <slot>            → D0 07 A9 F0 <slot> <status: 00 = ok>
```

Name entries are 20 bytes UTF-16BE plus an icon byte (22 bytes on Eletta,
with a mug byte). Empty slots are padding. The Soul reported three profiles;
names are only answered **while awake** (Arbuzov **[reported]**, consistent
here). The name list does not say which profile is active. Selecting
`1 → 2 → 1` was acknowledged and confirmed on the display **[measured]**.

### 7.8 Recipes (`0xA6`)

```
0D 07 A6 F0 <slot> <beverage>   → A6 F0 <slot> <beverage> <TLV…>
```

Recipes are scoped to a profile slot. The answer comes back as the recipe's
own property (`d039_1_rec_espresso` = profile 1 espresso), also in standby.
Measured on the Soul, profile 1: espresso `08=0, 01=40 ml, 1B=1, 02=4, 04=0,
19=1`; hot water `19=1, 0F=250 ml, 1B=1`. The factory descriptor `0xB0`
(`0D 06 B0 F0 <beverage>`) carries min/default/max triples per tag; real Soul
data has raw defaults outside the reported range (0 or 255) for travel mug,
over-ice and custom slots, so keep them instead of rejecting the descriptor.

Beverage ids are **not globally unique** **[app]**: the app resolves some
numbers by model and protocol, for example 22 = tea or an old custom slot,
25 = long black or rinsing, 26 = travel mug or descaling. Beverage ids, A2
statistic ids and cloud property names are separate namespaces. The tag
names, beverage enum, operation modes and model table (`MachinesModels.json`,
template 1.510, 117 entries) are identical in app versions 4.2.3, 4.7.2 and
4.9.6.

**Complete read of the Soul [measured, 2026-10-19]:** 28 descriptors, 28
recipes in each of the 5 profile slots, 5 priority lists, no invalid frame.
The 28 beverage ids are exactly the ids of the app's `PD_SOUL` model template
(ECAM610.75: 5 profiles, 28 recipe definitions) **[app]**: 21 standard
beverages, bean system `200` and custom `230`–`235`.

- **`0x19` VISIBLE [app]:** the app hides a recipe with `0` and, for normal
  beverages, shows an info page instead of a brew button with `2`. On the
  Soul, tea (22), coffee pot (23), mug to go (26) and brew over ice (27) carry
  `2`. This is app behaviour, not a proven firmware lock; `1` does not prove
  that the beverage can be made with the installed hardware either.
- **Priority lists** (`d261_1_rec_priority` … `d265_5_rec_priority`, opcode
  `0xA8`): one list of 23 beverage ids per profile. The app uses the position
  to order its beverage list **[app]**. Profile 1 had a user-specific order,
  profiles 2–5 an identical (probably factory) order. Being in the list does
  not make a beverage brewable.
- **Identity:** `d560_versione_sw`, `d561_machine_model` and
  `d562_versione_sw_ui` all read `0` on this machine. `software_version`
  reads `Millcore_demo 2.0.0 Jun 18 2020`; "Millcore" is De'Longhi's platform
  name for the Soul (cloud `oem_model` `DL-millcore` **[reported]**), so this
  is most likely the machine-side software string, not the Ayla module's
  firmware. Another Soul's cloud metadata shows the same software string and
  the Wi-Fi module separately: model `AY008ESP1`, firmware
  `ADA 1.5.3 esp-idf-v3.3.1 2020-04-13` **[reported, ioBroker/AdapterRequests#664]**.
  An Eletta Explore in the same thread reports `Striker_cb_demo 1.1.0 Oct 18
  2022`, so the `_demo` suffix appears to be De'Longhi's normal naming, not a
  sign of a defective unit. None of these properties identifies the model.

## 8. A2 statistics over LAN [measured]

Request and response layout are in the
[A2 document](https://github.com/Styx85/cremalink/blob/ha-compat/2026.9/docs/ecam-a2-statistics.md).
Specific to LAN mode:

- Write the A2 frame to `data_request`. The reply arrives as a pushed
  `data_response` datapoint, so there is no cloud polling and no timestamp
  filtering against stale cloud values.
- The complete table (62 values, 7 pages of up to 10) was read in **7 s**,
  without timeouts, **also while the machine was in standby**.
- **Hot water is A2 id 3019 on the Soul [measured]**, not 3018. A 100 ml
  hot-water command raised 3019 from 2 to 3 while 3018 stayed 0. The inherited
  mapping (3018 hot water, 3019 tea) was wrong for this machine, which has no
  tea function. 3018 stays unknown/raw.
- The app labels A2 id **3025** as total tea and shows id 106 (water) as
  raw × 0.5 ml **[app]**. 3025 is not confirmed on this machine; the
  integration keeps it raw. The app's cloud path also calls `d719_id22_tea`
  "tea", which does not contradict the local 3019 measurement.

## 9. Without internet [measured, small sample]

| Action | WAN blocked | Notes |
|---|---|---|
| Keep and re-create sessions, read monitor | ✅ 15 min | |
| Standby command | ✅ | |
| Wake / standby command | ✅ | 2026-10-19: three wakes and one off, each reacted within ~2 s on the display, with plain, `device_connected`-first and `?dsn=` sessions alike. The earlier "❌ once" was most likely a missed confirmation (see below). |
| Monitor updates | ⚠️ only on request | The module published **no** state change by itself; reading `d302_monitor` returned the frame from before the wake. A `0x75` request publishes the current state ~1–2 s after the module fetched it. |
| New sessions after a **module restart** | ❌ | Module refuses LAN mode until it reaches the cloud (2.1, 2.2) |

What the app does **[app]**: the Coffee Link app wakes the Soul with the
same `0x84 02 01` frame as aioecam. It hands the frame to the Ayla SDK,
which sends it over LAN when the LAN session is active, otherwise over BLE or
the cloud. No separate cloud wake-up command and no "LAN failed, retry via
cloud" step was found in the power path. So a wake that works in the app may
silently have gone through the cloud. One difference from aioecam: when the
app connects to a Soul it writes `device_connected` (Unix time, as for the
write session in 7.5); aioecam's `power_on()` does not announce a session
first. Tested 2026-10-19 with the internet blocked: the announce is **not
needed**; plain, announce-first and `?dsn=` sessions all woke the machine
within about 2 s.

The Ayla SDK can acknowledge a datapoint per command id (ACK/NAK) **[app]**.
Such an acknowledgement only shows that the module took the command, not that
the machine woke up or brewed; confirm by a monitor change.

So without internet, confirm a command by sending `0x75` and waiting for a
frame with a newer payload timestamp, not by re-reading the property
(aioecam ≥ 0.1.0b9 does this; the integration polls this way since 0.3.0b12).
Whether the module publishes changes itself when it reaches the cloud is
**[open]**, and so is an offline wake after an overnight standby.

Practical advice: verify every command by its effect on the monitor, and
offer a cloud fallback for waking if available. Blocking the machine's
internet access is possible, but the module then cannot recover LAN mode by
itself after a power cut.

## 10. Open questions

- Does the module push monitor changes by itself when it can reach the
  cloud? (Without internet it does not; section 6.)
- Offline wake after an overnight standby (tested so far: 5–13 min), after an
  HA restart during standby, and after a module restart without internet
  (expected to fail at registration, 2.2).
- Meaning of the `action` codes, and the exact meaning of `0x1B` INDEX_LENGTH values such as 4.
- Auto-off 2 = 1 h on the Soul: app-backed (7.6), not yet read back after setting it on the display (3 = 3 h is measured).
- Write-session lifetime and renewal; why a repeated recipe request is not
  republished (5).
- Is `local_reg` really limited to one client on this firmware?
- Everything beyond the Soul: Eletta sessions, other model families.

## 11. Related work

- [cremalink](https://github.com/miditkl/cremalink) and
  [cremalink-ha](https://github.com/miditkl/cremalink-ha) by @miditkl: the
  original local implementation, device maps and crypto.
- [OpenCoffeeLink](https://github.com/maximevince/OpenCoffeeLink): ECAM frame
  and LAN-mode notes from the Android app.
- [delonghi-coffeelink-local](https://github.com/FiveElements/delonghi-coffeelink-local):
  Node.js LAN server, single-slot and HTTP framing observations.
- [lodzen/cremalink](https://github.com/lodzen/cremalink) (AGPL-3.0): ECAM
  command layer reverse-engineered from the Coffee Link app plus Soul
  captures; source of the profile, settings, recipe and session details
  ported into aioecam. Discussion of the POST/PUT finding:
  [lodzen/cremalink#4](https://github.com/lodzen/cremalink/issues/4).
- [longshot](https://github.com/mmastrac/longshot) (Apache-2.0/MIT, BLE):
  request ids, monitor layout and bit names, ingredient ids, beverage ids.
- [home_assistant_delonghi_primadonna](https://github.com/Arbuzov/home_assistant_delonghi_primadonna)
  (Apache-2.0, BLE): 32-bit alarms, settings parameters, profile select.
- [DlghIoT](https://framagit.org/mattgk/dlghiot) (Apache-2.0, cloud, Eletta):
  monitor refresh request, Eletta session and switch bits.
- [ECAMpy](https://github.com/duckwc/ECAMpy) and
  [delonghi-coffee-link-python](https://github.com/otto-dev/delonghi-coffee-link-python)
  (no license; facts only).
- Discussions in cremalink#212 and cremalink-ha#65 (wake-up and cloud
  dependency reports).
