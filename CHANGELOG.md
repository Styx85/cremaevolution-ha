# Changelog

Newest first. Release notes with downloads are on the
[releases page](https://github.com/Styx85/cremaevolution-ha/releases); versions
before 0.3 are listed there only.

## 0.3.7b1: Cloud mode for machines without a local connection

Uses aioecam 0.1.4b1.

- Machines the De'Longhi cloud gives no local key (issue #27, an Eletta
  Explore) are no longer rejected. Setup explains the limits and creates a
  **cloud entry**: commands and status go through the De'Longhi cloud,
  slower than locally. Settings, statistics and the recipe reader are left
  out in cloud mode to stay within the cloud's request limit.
- New option **Connection**: Automatic (local first, default), Local only,
  De'Longhi cloud only.
- Signing in again for an entry without a local key now continues in cloud
  mode instead of failing.
- Diagnostics show which connection is in use.
- Cloud-mode findings from [delonghi-ha](https://github.com/sk7n4k3d/delonghi-ha)
  (MIT): the Eletta request signature, a keep-alive every 4 minutes and the
  request limit.

## 0.3.6b1: Robustness round

From a code review, each fix covered by a regression test:

- A failed setup (e.g. the first status read during the wake-up rinse) now
  releases the LAN link and server; the retry starts clean.
- Settings: a poll can no longer overwrite a value you just set, and the
  fast 60 s polling starts as soon as the machine wakes up.
- The profile name request is cancelled when the integration reloads.
- With aioecam 0.1.2b1: commands queued during a keep-alive no longer wait
  up to 20 s (the cause of "machine did not fetch … within 10 s" errors),
  cancelled commands are never sent later, monitor requests respect their
  timeout, and delayed older status frames are ignored.

## 0.3.5b1: Quieter log

When the machine's Wi-Fi module drops a connection partway through (a short Wi-Fi hiccup), the log no longer shows a `ConnectionResetError` traceback; the module retries on its own. Uses aioecam 0.1.1b1.

## 0.3.4b1: Smaller database

The two disabled-by-default diagnostic sensors (monitor diagnostics, A2
statistics diagnostics) wrote their large attributes into the recorder on
every update, tens of MB if enabled. Their attributes are now excluded from
recording; they stay visible live and in the diagnostics download. The
monitor diagnostics state is now the frame's own timestamp, so it only
changes when the machine publishes a new frame instead of on every poll.

Data recorded before the update is removed by the recorder's normal purge
(default 10 days), or right away with the `recorder.purge_entities` action
for these two entities.

## 0.3.3b1: Auto-off 3 h and faster settings

- Auto-off now offers **3 h**: setting 3 h at the machine's display read back
  as raw value 3, matching the Coffee Link app.
- Settings are polled every 60 s while the machine is on (changes at the
  display show up within a minute) and every 15 min in standby. A read that
  fails while the machine is busy, e.g. rinsing, is retried in the next round.
- The diagnostics export includes the settings values.

## 0.3.2b1: Firmware version in the device info

The device page in Home Assistant now shows the software string the machine
reports in its `software_version` property (on the tested ECAM610.75
"Millcore_demo 2.0.0 Jun 18 2020"). It is read once after setup, also in
standby. "Millcore" is De'Longhi's platform name for the PrimaDonna Soul
(`DL-millcore`), so this is most likely the machine-side software, not the
Wi-Fi module's own firmware (other Souls report that as `ADA 1.5.3` via the
cloud). `d560`–`d562` read `0` on this machine.

## 0.3.1b1: Renamed to CremaEvolution

The project continues as **CremaEvolution** in `Styx85/cremaevolution-ha`; old links redirect. The domain stays `cremalink_ha`, so entities, history, automations and `cremalink_ha.brew_custom` keep working.

## 0.3.0b12: Status without internet

Tested on the ECAM610.75 with its internet access blocked: switching on and
off works locally within about two seconds. The Wi-Fi module does not report
state changes by itself, though, so a plain read kept showing "standby"
after the machine had woken up. The integration now asks the machine for a
fresh status on every poll (monitor request) and waits for the new frame,
so the state follows without internet as well. Requires aioecam 0.1.0b9.

## 0.3.0b11: aioecam 0.1.0b8

Recipe tags named after the Coffee Link app; brew commands always use the operation mode "prepare". No change to entities or `brew_custom`.

## 0.3.0b10: Milk and strength values

From a closer look at the Coffee Link app 4.9.6:

- `milk` in `cremalink_ha.brew_custom` is a **duration in tenths of a second**
  (the app shows 210 as 21 s), not millilitres. Allowed: 30–1080, the range
  of the Soul's own recipe descriptors.
- `intensity` is the app's TASTE step 1 (extra mild) to 5 (extra strong).
  `0` means pre-ground coffee in the app and is no longer accepted.
- The last byte of a brew command is the profile plus an operation mode
  (2 = prepare); earlier notes called it an accessory code. Custom amounts
  always use "prepare", so they never change a stored recipe.

## 0.3.0b9: Correction: no temperature field

0.3.0b7 and 0.3.0b8 offered a `temperature` field in `cremalink_ha.brew_custom`
that wrote recipe tag `0x1B`. A static analysis of the Coffee Link app 4.9.6
shows that this tag is `INDEX_LENGTH` (a size index), not a temperature; the
app's temperatures are tags `0x00` and `0x0D`. The field is removed. Calls that
still pass `temperature` are rejected. `intensity` is the app's `TASTE` step.

## 0.3.0b8: Recipe reads continue where they stopped

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

## 0.3.0b7: Settings, profile and custom amounts

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

## 0.3.0b6: Recipe-read timing and descriptor defaults

The first live read returned valid recipes after the old two-second deadline,
including one response about 20 seconds later. The reader now waits up to 25
seconds per property and pauses 250 ms between reads; the overall 120-second
budget and three-failure stop still apply. There are no automatic read retries.
Descriptor defaults outside their reported min/max are preserved as raw values
with `default_in_range: false`, rather than losing the entire descriptor. This
flag is diagnostic and does not authorize those values for brewing.

## 0.3.0b5: Read recipe data once

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

## 0.3.0b4: Passive catalogue and freshness diagnostics

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

## 0.3.0b3: Descale load as diagnostic

Pins aioecam 0.1.0b4 (improved answer routing, retained profile/recipe answers). **Descale load (raw)** is now a diagnostic sensor disabled by default; existing enabled entities keep their setting. The hot-water counter reads A2 ID `3019`; the incorrect "Tea" entity is removed, ID `3018` stays an unidentified raw statistic.

## 0.3.0b2: Fixes from the first real install

Entities no longer stay unavailable after a cloud token rotation (the entry reloads only when its options change), and the "Detected blocking call" warnings are gone (aioecam 0.1.0b2).

## 0.3.0b1: Ground-up rewrite

Home Assistant talks to the machine directly over the local network through [aioecam](https://pypi.org/project/aioecam/); the Cremalink Server add-on is no longer needed. Existing entries are migrated automatically (config entry version 2: make a backup first, older versions cannot load it).
