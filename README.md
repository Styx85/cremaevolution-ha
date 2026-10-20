# <img src="icon.png" alt="CremaEvolution logo" height="45" style="vertical-align: middle; margin-right: 10px;"> CremaEvolution

**Local-first Home Assistant integration for De'Longhi Coffee Link machines (PrimaDonna Soul, Eletta Explore, …).**

**CremaEvolution** grew out of a fork of
[cremalink-ha](https://github.com/miditkl/cremalink-ha) and was known as
"Cremalink for Home Assistant" until 0.3.0b12. After a ground-up rewrite on
its own library, [aioecam](https://github.com/Styx85/aioecam), it continues
as its own project, maintained by **[Styx85](https://github.com/Styx85)**.
The original authors are acknowledged in [Credits](#-credits).

> [!NOTE]
> **Renamed, nothing to do.** The integration domain is still `cremalink_ha`,
> so entities, entity ids, history, automations and the
> `cremalink_ha.brew_custom` action keep working. The GitHub repository moved
> to `Styx85/cremaevolution-ha`; old links redirect. A later release will move
> to its own domain with an automatic migration and will say so clearly.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?category=integration&repository=cremaevolution-ha&owner=Styx85)
[![License](https://img.shields.io/github/license/Styx85/cremaevolution-ha?style=for-the-badge&color=success)](LICENSE)

> [!IMPORTANT]
> **Version 0.3 is a ground-up rewrite: no add-on any more.**
>
> Home Assistant talks to the machine directly over the local network (Ayla LAN
> mode) through the new async library **aioecam**. The De'Longhi cloud is only
> used to fetch the LAN key during setup and, optionally, as a fallback to wake
> the machine. Existing installations are migrated automatically: entities,
> entity ids, history and dashboards stay. You can uninstall the Cremalink
> Server add-on afterwards.

---

## Why a rewrite

Measurements on a PrimaDonna Soul (ECAM610.75) in October 2026 traced the
unreliable local connection of the add-on design to the client, not to the
machine:

| Finding | Effect today | In the rewrite |
|---|---|---|
| With a live session the machine ignores `PUT local_reg` for re-keying (0/10) but always re-keys on `POST` (10/10) | The add-on re-keys every 60 s via PUT and ends up with a "hollow" session: no status, commands fail with 502 | New sessions only via POST, PUT only as keep-alive, no forced re-key |
| The machine pushes unrelated properties (app heartbeats, command echoes) | They overwrite the machine status, which then shows as unknown or stuck | Datapoints are routed by property name |
| Sessions, status and power on/off keep working without internet | — | Local is the primary path, the cloud is optional |
| After a restart of the Wi-Fi module without internet, it refuses LAN mode until it has reached the cloud again | Silent endless retries | Detected and reported clearly |
| An extra server process is needed | Add-on, or a hand-built container on Docker installs | Runs inside Home Assistant, no add-on |

All protocol findings (LAN registration, session crypto, monitor behaviour,
module states, A2 over LAN) are written up for reuse in
**[docs/ecam-lan-protocol.md](docs/ecam-lan-protocol.md)**. The architecture
and milestones are in [docs/aioecam-plan.md](docs/aioecam-plan.md), and the
probe tools used for the measurements are in [tools/](tools/).

### Rewrite status

| Milestone | Status |
|---|---|
| Protocol codec, monitor parsing, model profiles | ✅ done |
| Local LAN transport (verified live on an ECAM610.75) | ✅ done |
| Device API with confirmed commands, A2 statistics (verified live: on, espresso, off, full A2 table) | ✅ done |
| Cloud onboarding and wake fallback (verified with a real account) | ✅ done |
| Home Assistant integration without add-on, migration | ✅ done (tested against a real HA core) |
| Beta releases | ✅ available; 0.3.3b1 uses aioecam 0.1.0b9 |
| 24 h soak test | ⏳ planned |

### Hardware validation and beta scope

Live protocol tests have been performed on a **PrimaDonna Soul ECAM610.75**.
Other model variants, including Eletta Explore, have only codec, captured-frame
and simulated-device coverage; their behaviour on real hardware needs
confirmation by owners of those machines. Eletta write sessions are not
implemented. Integration tests run against a real Home Assistant core with a
simulated coffee machine, and do not establish hardware compatibility for
other models.

Version **0.3.0b3** pins **aioecam 0.1.0b4**, including its improved answer
routing and handling of retained profile/recipe answers. Since **0.3.0b7**
the library's settings, profile selection and custom beverage parameters are
exposed in Home Assistant (see below). The final recipe-polling
fix has simulated-device coverage rather than a repeat hardware test; whether
monitor changes require polling also remains open. See the library's
[hardware results and limitations](https://github.com/Styx85/aioecam/blob/main/docs/m7-protocol.md).

The hot-water counter now reads A2 ID `3019`, confirmed by the ECAM610.75
hot-water test and the operator's observed increment. The previous "Tea"
label was incorrect; that unsupported entity is removed. The existing
hot-water entity keeps its unique ID. ID `3018` remains available as an
unidentified raw statistic rather than being given a guessed meaning.

---

## Installation

1. **HACS:** click the HACS badge above, or add this repository to HACS as a
   custom repository: `https://github.com/Styx85/cremaevolution-ha` (category:
   Integration). Check that the repository is `Styx85/cremaevolution-ha`, install
   it and restart Home Assistant. If HACS still lists the original author
   after switching forks, refresh its repository information; `codeowners`
   in this fork identifies `@Styx85`.
2. **Manual:** copy `custom_components/cremalink_ha` to your
   `config/custom_components/` directory and restart Home Assistant.

**Network requirement:** in LAN mode the machine connects *to* Home Assistant.
It must be able to reach Home Assistant on TCP port **10280** (changeable in
the integration options). With Docker, use host networking or publish the port
and set the announced address in the options.

## Configuration

1. Go to **Settings** > **Devices & Services** > **Add Integration** and search
   for **CremaEvolution**.
2. Sign in with your De'Longhi Coffee Link account. The integration finds your
   machine, its model, its IP address and its local key. Only a refresh token
   is stored, never your password.
3. Already know the IP address and LAN key? Tick **Manual setup** instead.

Coming from the add-on version: nothing to do. After the update the existing
entry is migrated on the next start. Entries that used the cloud connection
ask you to sign in once, to fetch the local key.

### Good to know

- After a restart of its Wi-Fi module (e.g. a power cut) the machine accepts
  local connections only after it has reached the De'Longhi cloud once. The
  integration retries and reports this in the log.
- The "water tank empty" sensor is not created for the PrimaDonna Soul: its
  bit does not reflect the tank on this machine.
- During a beverage the machine reports status "ready"; "Busy" is derived
  from its activity instead.
- The raw descale-load sensor is a disabled-by-default diagnostic entity;
  its unit and weighting are not established. Enable it manually if needed
  for investigation. Existing enabled entities retain their current setting.

### Additions in this fork (since 0.2)

- Native **A2 statistics** read from ECAM610-class machines: beverage totals
  and per-beverage counters, maintenance counts, operating water, water since
  the last filter change, and a raw descale-load value. Unknown A2 IDs stay
  available in a disabled-by-default diagnostics entity rather than being
  given guessed meanings. They refresh more slowly than the live status
  because a full table read takes longer.
- **English and German** entity and command names.
- The last valid status is **retained across short connection drops**
  instead of flapping to unknown.

---

## 🙏 Credits

CremaEvolution is **heavily based on [cremalink-ha](https://github.com/miditkl/cremalink-ha)
and [cremalink](https://github.com/miditkl/cremalink) by
[Midian Tekle Elfu (@miditkl)](https://github.com/miditkl)**, including the
cloud-assisted onboarding contributed by [@lodzen](https://github.com/lodzen).
The original integration, device maps and protocol work made this project
possible. Improvements found here are offered upstream as pull requests where
they fit.

Not affiliated with De'Longhi or Ayla Networks.

## 📄 License

Distributed under the **AGPL-3.0-or-later** license, like the upstream
projects. See [LICENSE](LICENSE).

### Passive catalogue and freshness diagnostics (0.3.0b4)

The HA diagnostic download includes `identity`, `freshness` and `recipe_catalog`.
The configured protocol family is separate from the OEM identifier stored during
onboarding and observed model/firmware properties. Older entries may have unknown
metadata; diagnostics never contact the cloud to fill it in.

Freshness reports the last datapoint and valid monitor receipt, payload timestamp,
last monitor-read outcome/error class and consecutive failures. Equal valid frames
count as new receipts; cached standby replies are not proof of a fresh physical
state. Monitor `received_at` reflects latest valid receipt; `view_created_at` is
the construction time of the HA state view. Diagnostics do not add recorder churn.

The catalogue decodes only incoming B0/A6/A8 and name-metadata frames. It includes
numeric parameter tags, reported ranges, profile recipes, priority order, source
properties and validation results. It creates no entities, sends no additional
reads/writes and does not infer brewability from a descriptor or priority list.
It may remain empty until recipe properties arrive. The bounded in-memory cache
resets on reload. Household names are redacted; serial/PIN/settings frames are
excluded. This requires aioecam 0.1.0b5 and was introduced in 0.3.0b4.

### Read recipe data once (0.3.0b5)

On the Soul family, enable the diagnostic button **Read recipe data once** in the
entity settings. It is disabled by default and never runs at setup or on a timer.
Press it once, allow up to two minutes, then download the integration diagnostics.
`recipe_read` reports progress, completion/partial result and per-property errors;
`recipe_catalog` contains the validated responses. The button becomes available
again when the run ends.

The reader uses only property GETs on the existing HA LAN link: no wake, session
announcement, settings write or brew command. Its fixed inventory comprises 173
recipe/descriptor/priority properties observed on our ECAM610.75 plus four
model/software properties. No Cloud login or discovery is needed. Other Soul
firmwares may expose different names; they are not assumed compatible just
because they use the same family profile. Other families have no reader button.

Reads are sequential, paced at least 250 ms apart, with a 25-second limit per property, a two-minute total
budget and a stop after three consecutive read failures. Busy/disconnected
machines stop the run, repeated presses do not queue runs, and HA unload cancels
it. Valid results from a partial run remain in the catalogue. CRC/payload errors
remain diagnostic errors, not drink availability claims. No new beverage controls
are created. Keep competing Coffee Link LAN servers disabled.


### Recipe-read timing and descriptor defaults (0.3.0b6)

The first live read returned valid recipes after the old two-second deadline,
including one response about 20 seconds later. The reader now waits up to 25
seconds per property and pauses 250 ms between reads; the overall 120-second
budget and three-failure stop still apply. There are no automatic read retries.
Descriptor defaults outside their reported min/max are preserved as raw values
with `default_in_range: false`, rather than losing the entire descriptor. This
flag is diagnostic and does not authorize those values for brewing.



### Settings, profile and custom amounts (0.3.0b7)

- **Auto-off** and **water hardness** selects (configuration category). They
  are read every 15 minutes and also work in standby; a write is confirmed by
  reading the value back. Only auto-off values every source agrees on are
  offered (15 min, 30 min, 1 h). A machine set to another value shows as
  unknown, with the raw value as an attribute.
- **User profile** select. The machine does not report the active profile, so
  the select shows the last profile chosen in Home Assistant and acknowledged
  by the machine. Profile names load once the machine is awake; changing the
  profile is refused while the machine is in standby.
- Service **`cremalink_ha.brew_custom`**: brews a stored beverage with the
  machine's own recipe for the chosen profile and changes only the amounts
  you pass (`coffee_ml`, `water_ml`, `milk`, `intensity`).
  Parameters the beverage does not have are refused rather than invented.
  Example:

  ```yaml
  action: cremalink_ha.brew_custom
  data:
    device_id: <your machine>
    beverage: hot_water
    water_ml: 100
  ```

Verified on the ECAM610.75 through the library (auto-off write and restore,
profile 1 → 2 → 1, 100 ml hot water); the Home Assistant entities and the
service themselves are tested against the simulated machine.

### Recipe reads continue where they stopped (0.3.0b8)

A full read of the 177 properties takes longer than the 120-second budget of
one press (the first complete live run reached 128). A press now reads one
bounded *part* of a *pass*; the next press continues with the properties not
yet confirmed, never-tried ones first and failed ones last, so the priority
lists and identity properties at the end are reached. Budget, per-property
deadline, pacing and the three-failure stop are unchanged, and there are still
no background retries.

- A property counts as confirmed only after a successful read with a valid
  catalogue value, or when a valid answer arrives late (after its read timed
  out but within the same pass).
- `recipe_read` in the diagnostics keeps the counters of the last part and adds
  `pass`: start/end, number of parts, confirmed and pending properties, late
  answers, failed properties still pending and the next property.
- Once the pass is complete, the next press deliberately starts a fresh pass.
- The pass is kept in memory only: restarting Home Assistant or reloading the
  integration starts a new pass from the first property.

### Correction: no temperature field (0.3.0b9)

0.3.0b7 and 0.3.0b8 offered a `temperature` field in `cremalink_ha.brew_custom`
that wrote recipe tag `0x1B`. A static analysis of the Coffee Link app 4.9.6
shows that this tag is `INDEX_LENGTH` (a size index), not a temperature; the
app's temperatures are tags `0x00` and `0x0D`. The field is removed. Calls that
still pass `temperature` are rejected. `intensity` is the app's `TASTE` step.

### Milk and strength values (0.3.0b10)

From a closer look at the Coffee Link app 4.9.6:

- `milk` in `cremalink_ha.brew_custom` is a **duration in tenths of a second**
  (the app shows 210 as 21 s), not millilitres. Allowed: 30–1080, the range
  of the Soul's own recipe descriptors.
- `intensity` is the app's TASTE step 1 (extra mild) to 5 (extra strong).
  `0` means pre-ground coffee in the app and is no longer accepted.
- The last byte of a brew command is the profile plus an operation mode
  (2 = prepare); earlier notes called it an accessory code. Custom amounts
  always use "prepare", so they never change a stored recipe.

### Status without internet (0.3.0b12)

Tested on the ECAM610.75 with its internet access blocked: switching on and
off works locally within about two seconds. The Wi-Fi module does not report
state changes by itself, though, so a plain read kept showing "standby"
after the machine had woken up. The integration now asks the machine for a
fresh status on every poll (monitor request) and waits for the new frame,
so the state follows without internet as well. Requires aioecam 0.1.0b9.

### Firmware version in the device info (0.3.2b1)

The device page in Home Assistant now shows the Wi-Fi module's firmware
string (the `software_version` property; on the tested ECAM610.75
"Millcore_demo 2.0.0 Jun 18 2020"). It is read once after setup, also in
standby. This is the firmware of the Wi-Fi module; the machine controller
does not report its own version over LAN (`d560`–`d562` read `0`).

### Auto-off 3 h and faster settings (0.3.3b1)

- Auto-off now offers **3 h**: setting 3 h at the machine's display read back
  as raw value 3, matching the Coffee Link app.
- Settings are polled every 60 s while the machine is on (changes at the
  display show up within a minute) and every 15 min in standby. A read that
  fails while the machine is busy, e.g. rinsing, is retried in the next round.
- The diagnostics export includes the settings values.
