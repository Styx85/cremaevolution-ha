# <img src="icon.png" alt="Cremalink logo" height="45" style="vertical-align: middle; margin-right: 10px;"> Cremalink for Home Assistant

**Local-first Home Assistant integration for De'Longhi Coffee Link machines (PrimaDonna Soul, Eletta Explore, …).**

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?category=integration&repository=cremalink-ha&owner=Styx85)
[![License](https://img.shields.io/github/license/Styx85/cremalink-ha?style=for-the-badge&color=success)](LICENSE)

> [!IMPORTANT]
> **This fork is being rewritten from the ground up.**
>
> The rewrite removes the Cremalink Server add-on: Home Assistant talks to the
> machine directly over the local network (Ayla LAN mode) through the new async
> library **aioecam**. The De'Longhi cloud is only used to fetch the LAN key
> during setup and, optionally, as a fallback to wake the machine.
>
> Until the rewrite is released, `main` still contains the add-on based
> integration documented under [Current version](#current-version-add-on-based).
> Entities, history and dashboards will be migrated automatically.

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
| Cloud onboarding and wake fallback | ⏭️ next |
| Home Assistant integration without add-on, migration | ⏳ planned |
| 24 h soak test and release | ⏳ planned |

---

## Current version (add-on based)

### Installation

1. **Add-on (local connection only):** install and configure the Cremalink
   Server add-on, which bridges the machine and Home Assistant. See the
   [add-on documentation](addons/cremalink-server/README.md).
2. **Integration via HACS:** click the HACS badge above, or add this repository
   to HACS as a custom repository. Search for "Cremalink", install it and
   restart Home Assistant.
3. **Manual installation:** copy `custom_components/cremalink_ha` to your
   `config/custom_components/` directory and restart Home Assistant.

### Configuration

1. Go to **Settings** > **Devices & Services** > **Add Integration** and search
   for **Cremalink**.
2. Sign in with your De'Longhi Coffee Link account. The integration discovers
   your machines, detects the model and connects locally through the add-on
   when LAN details are available, otherwise through the cloud.
3. Without a cloud account, tick **Advanced setup** on the first screen to
   enter the device map, DSN, LAN key and IP (local) or a refresh token (cloud)
   by hand.

### Additions in this fork

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
