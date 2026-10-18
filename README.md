# <img src="icon.png" alt="Cremalink logo" height="45" style="vertical-align: middle; margin-right: 10px;"> Cremalink for Home Assistant

**Local-first Home Assistant integration for De'Longhi Coffee Link machines (PrimaDonna Soul, Eletta Explore, …).**

This is the fork maintained by **[Styx85](https://github.com/Styx85)**.
The original authors are acknowledged in [Credits](#-credits).

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?category=integration&repository=cremalink-ha&owner=Styx85)
[![License](https://img.shields.io/github/license/Styx85/cremalink-ha?style=for-the-badge&color=success)](LICENSE)

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
| Beta releases | ✅ available; 0.3.0b3 uses aioecam 0.1.0b4 |
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
routing and handling of retained profile/recipe answers. The library's new
APIs for settings, profile selection and custom beverage parameters are not
yet exposed as Home Assistant entities or services. The final recipe-polling
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
   custom repository: `https://github.com/Styx85/cremalink-ha` (category:
   Integration). Check that the repository is `Styx85/cremalink-ha`, install
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
   for **Cremalink**.
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

This fork is **heavily based on [cremalink-ha](https://github.com/miditkl/cremalink-ha)
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
