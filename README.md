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
| Beta releases | ✅ available, see [CHANGELOG.md](CHANGELOG.md) |
| 24 h soak test | ⏳ planned |

### Hardware validation and beta scope

Live protocol tests have been performed on a **PrimaDonna Soul ECAM610.75**.
Other model variants, including Eletta Explore, have only codec, captured-frame
and simulated-device coverage; their behaviour on real hardware needs
confirmation by owners of those machines. Eletta write sessions are not
implemented. Integration tests run against a real Home Assistant core with a
simulated coffee machine, and do not establish hardware compatibility for
other models. See the library's
[hardware results and limitations](https://github.com/Styx85/aioecam/blob/main/docs/m7-protocol.md).

What changed in which version: **[CHANGELOG.md](CHANGELOG.md)**.

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

- Without internet, power on/off and status work locally. The Wi-Fi module
  can still drop LAN mode after a Wi-Fi reconnect or overnight and only
  accepts local connections again once it reaches the cloud, so offline
  operation covers short outages, not permanent blocking.

## Features

- **Status and controls:** power, beverages, live status, busy state. The
  last valid status is retained across short connection drops.
- **A2 statistics** (ECAM610 class): beverage totals and per-beverage
  counters, maintenance counts, operating water, water since the last filter
  change. Unknown IDs (e.g. `3018`) stay raw in a disabled-by-default
  diagnostics entity rather than getting guessed meanings.
- **Settings:** auto-off (15 min, 30 min, 1 h, 3 h) and water hardness as
  selects, also in standby. Polled every 60 s while the machine is on and
  every 15 min in standby; writes are confirmed by reading the value back.
  An unsupported value shows as unknown with the raw value as an attribute.
- **User profile:** the machine does not report the active profile, so the
  select shows the last profile chosen in Home Assistant and acknowledged by
  the machine. Changing it is refused in standby.
- **Firmware** in the device info: the machine's `software_version` string.
- **English and German** entity and command names.

### Custom amounts: `cremalink_ha.brew_custom`

Brews a stored beverage with the machine's own recipe for the chosen profile
and changes only the values you pass. Parameters the beverage does not have
are refused. The stored recipe is never changed.

| Field | Meaning |
|---|---|
| `coffee_ml`, `water_ml` | amounts in ml |
| `milk` | milk duration in tenths of a second (210 = 21 s), 30–1080 |
| `intensity` | strength step 1 (extra mild) to 5 (extra strong) |

```yaml
action: cremalink_ha.brew_custom
data:
  device_id: <your machine>
  beverage: hot_water
  water_ml: 100
```

### Diagnostics

The diagnostics download contains `identity`, `freshness` (last datapoint,
monitor receipt and read errors), the settings values and a passive
`recipe_catalog`. Household names are redacted; serial, PIN and keys are
excluded. The two large diagnostic sensors are kept out of the recorder.

On the Soul family, the disabled-by-default button **Read recipe data once**
reads the machine's recipe, descriptor and priority properties for the
catalogue (only reads, no brewing or writes). One press reads for up to two
minutes; press again to continue where it stopped until `recipe_read.pass`
reports `complete`. Progress is kept in memory only.

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
