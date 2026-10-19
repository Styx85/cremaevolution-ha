"""The manual reader uses only GETs on the existing link and is bounded."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from aioecam.ecam.frame import RESPONSE, Frame
from aioecam.ecam.value import encode_value
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cremalink_ha import recipe_reader
from custom_components.cremalink_ha.const import DOMAIN
from custom_components.cremalink_ha.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.conftest import V2_DATA
from tests.fakes.fake_machine import FakeMachine

def reader_commands(machine: FakeMachine) -> list:
    """ECAM frames sent to the machine, without the coordinator's monitor requests."""
    return [f for f in machine.received if not (f.opcode == 0x75 and not f.payload)]


NAMES = (
    "d001_rec_espresso",
    "d039_1_rec_espresso",
    "d261_1_rec_priority",
    "software_version",
)


async def setup(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, title="Soul", data=V2_DATA, unique_id=V2_DATA["dsn"]
    )
    entry.add_to_hass(hass)
    with (
        patch("custom_components.cremalink_ha.statistics_supported", return_value=False),
        # these tests assert that no ECAM command is sent; settings polling would read 0x95
        patch("custom_components.cremalink_ha.settings_supported", return_value=False),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_button_opt_in_and_reads_without_writing(
    hass: HomeAssistant, machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(recipe_reader, "SOUL_PROPERTIES", NAMES)
    payloads = (
        (0xB0, "01 01 0014 0028 00b4"),
        (0xA6, "01 01 01 0028"),
        (0xA8, "01 01 10"),
    )
    for name, (opcode, payload) in zip(NAMES, payloads, strict=False):
        machine.device.properties[name] = encode_value(
            Frame(RESPONSE, opcode, 0xF0, bytes.fromhex(payload))
        )
    machine.device.properties["software_version"] = "test firmware"
    entry = await setup(hass)
    reader = entry.runtime_data.recipe_reader
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_recipe_read"
    )
    assert (
        registry.async_get(entity_id).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )
    assert reader.result == "not_started" and not reader_commands(machine)
    registry.async_update_entity(entity_id, disabled_by=None)
    # Reload with the registry preference retained, then press the actual HA button.
    with (
        patch("custom_components.cremalink_ha.statistics_supported", return_value=False),
        # these tests assert that no ECAM command is sent; settings polling would read 0x95
        patch("custom_components.cremalink_ha.settings_supported", return_value=False),
    ):
        await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    reader = entry.runtime_data.recipe_reader
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    assert reader.running
    assert reader.start(hass, entry) is False  # no second queued batch
    await reader.task
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["recipe_read"]["result"] == "completed"
    assert diag["recipe_read"]["received"] == 4
    assert diag["recipe_read"]["valid_catalog_entries"] == 3
    assert diag["recipe_catalog"]["property_count"] == 3
    assert diag["identity"]["firmware"] == "test firmware"
    assert not reader_commands(machine)  # GETs, not ECAM/session writes


async def test_missing_properties_stop_after_three_failures(
    hass: HomeAssistant, machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
):
    from aioecam.exceptions import CommandTimeout

    monkeypatch.setattr(recipe_reader, "SOUL_PROPERTIES", NAMES)
    entry = await setup(hass)
    device = entry.runtime_data.device
    original = device.link.read
    attempted = []

    async def fail(name, **kwargs):
        if name in NAMES:
            attempted.append(name)
            raise CommandTimeout("private error text")
        return await original(name, **kwargs)

    monkeypatch.setattr(device.link, "read", fail)
    reader = entry.runtime_data.recipe_reader
    reader.start(hass, entry)
    await reader.task
    assert attempted == list(NAMES[:3])
    assert (
        reader.result == "partial" and reader.stop_reason == "consecutive_read_failures"
    )
    assert "private error text" not in str(reader.snapshot())
    assert not reader.running and reader.finished_at


async def test_reader_cancelled_on_entry_unload(
    hass: HomeAssistant, machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(recipe_reader, "SOUL_PROPERTIES", NAMES)
    entry = await setup(hass)
    device = entry.runtime_data.device
    original = device.link.read
    started = asyncio.Event()

    async def block(name, **kwargs):
        if name in NAMES:
            started.set()
            await asyncio.Event().wait()
        return await original(name, **kwargs)

    monkeypatch.setattr(device.link, "read", block)
    reader = entry.runtime_data.recipe_reader
    reader.start(hass, entry)
    await asyncio.wait_for(started.wait(), 2)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert reader.result == "cancelled" and not reader.running
    assert not reader_commands(machine)


async def test_total_duration_is_bounded(
    hass: HomeAssistant, machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(recipe_reader, "SOUL_PROPERTIES", NAMES)
    monkeypatch.setattr(recipe_reader, "MAX_DURATION", 0.02)
    entry = await setup(hass)
    original = entry.runtime_data.device.link.read

    async def slow(name, **kwargs):
        if name in NAMES:
            await asyncio.sleep(1)
        return await original(name, **kwargs)

    monkeypatch.setattr(entry.runtime_data.device.link, "read", slow)
    reader = entry.runtime_data.recipe_reader
    reader.start(hass, entry)
    await asyncio.wait_for(reader.task, 1)
    assert reader.stop_reason == "time_limit" and reader.attempted == 1
    assert reader.result == "partial" and not reader.running


async def test_slow_valid_response_is_not_a_false_timeout(
    hass: HomeAssistant,
    machine: FakeMachine,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(recipe_reader, "SOUL_PROPERTIES", NAMES[:1])
    monkeypatch.setattr(recipe_reader, "MAX_DURATION", 5)
    machine.device.properties[NAMES[0]] = encode_value(
        Frame(RESPONSE, 0xB0, 0xF0, bytes.fromhex("01 01 0014 0028 00b4"))
    )
    entry = await setup(hass)
    original = entry.runtime_data.device.link.read
    calls = []

    async def delayed(name, **kwargs):
        if name == NAMES[0]:
            calls.append(name)
            await asyncio.sleep(2.1)  # live responses exceeded the old 2s limit
        return await original(name, **kwargs)

    monkeypatch.setattr(entry.runtime_data.device.link, "read", delayed)
    reader = entry.runtime_data.recipe_reader
    reader.start(hass, entry)
    await asyncio.wait_for(reader.task, 6)
    assert reader.result == "completed" and reader.received == 1
    assert not reader.errors and calls == [NAMES[0]]  # no retry/duplicate read
    assert not reader_commands(machine)


PASS_NAMES = tuple(f"d{i:03d}_rec_test" for i in range(1, 7))
DESCRIPTOR = encode_value(Frame(RESPONSE, 0xB0, 0xF0, bytes.fromhex("01 01 0014 0028 00b4")))


async def _pass_setup(hass, machine, monkeypatch):
    monkeypatch.setattr(recipe_reader, "SOUL_PROPERTIES", PASS_NAMES)
    for name in PASS_NAMES:
        machine.device.properties[name] = DESCRIPTOR
    entry = await setup(hass)
    device = entry.runtime_data.device
    original = device.link.read
    calls: list[str] = []

    async def counting(name, **kwargs):
        if name in PASS_NAMES:
            calls.append(name)
        return await original(name, **kwargs)

    monkeypatch.setattr(device.link, "read", counting)
    return entry, entry.runtime_data.recipe_reader, calls


async def test_next_press_resumes_the_pass(
    hass: HomeAssistant, machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
):
    entry, reader, calls = await _pass_setup(hass, machine, monkeypatch)
    device = entry.runtime_data.device
    busy = False
    monkeypatch.setattr(device, "is_busy", lambda *a: busy)
    read = device.link.read

    async def busy_after_two(name, **kwargs):
        nonlocal busy
        result = await read(name, **kwargs)
        busy = len(calls) >= 2  # e.g. someone starts a drink
        return result

    monkeypatch.setattr(device.link, "read", busy_after_two)
    reader.start(hass, entry)
    await reader.task
    first = reader.snapshot()
    assert first["result"] == "partial" and first["stop_reason"] == "machine_unavailable_or_busy"
    progress = {k: first["pass"][k] for k in ("complete", "parts", "confirmed", "pending")}
    assert progress == {"complete": False, "parts": 1, "confirmed": 2, "pending": 4}
    assert first["pass"]["next_property"] == PASS_NAMES[2]

    busy = False
    monkeypatch.setattr(device.link, "read", read)
    reader.start(hass, entry)
    await reader.task
    second = reader.snapshot()
    assert calls == list(PASS_NAMES)  # no property read twice
    assert second["result"] == "completed" and second["attempted"] == 4
    assert second["pass"]["complete"] and second["pass"]["parts"] == 2
    assert second["pass"]["started_at"] == first["pass"]["started_at"]
    assert second["pass"]["finished_at"]

    # a complete pass is not resumed: the next press deliberately starts over
    reader.start(hass, entry)
    await reader.task
    third = reader.snapshot()
    assert calls[len(PASS_NAMES) :] == list(PASS_NAMES)
    assert third["pass"]["parts"] == 1 and third["pass"]["started_at"] != first["pass"]["started_at"]
    assert not reader_commands(machine)


async def test_failed_property_is_retried_last_in_the_next_part(
    hass: HomeAssistant, machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
):
    from aioecam.exceptions import CommandTimeout

    entry, reader, calls = await _pass_setup(hass, machine, monkeypatch)
    read = entry.runtime_data.device.link.read
    failed_once = set()

    async def flaky(name, **kwargs):
        if name in (PASS_NAMES[0], PASS_NAMES[3]) and name not in failed_once:
            failed_once.add(name)
            calls.append(name)
            raise CommandTimeout("no answer")
        return await read(name, **kwargs)

    monkeypatch.setattr(entry.runtime_data.device.link, "read", flaky)
    reader.start(hass, entry)
    await reader.task
    snap = reader.snapshot()
    assert snap["result"] == "partial" and snap["pass"]["confirmed"] == 4
    assert snap["pass"]["failed_pending"] == [PASS_NAMES[0], PASS_NAMES[3]]

    reader.start(hass, entry)
    await reader.task
    snap = reader.snapshot()
    assert calls[len(PASS_NAMES) :] == [PASS_NAMES[0], PASS_NAMES[3]]
    assert snap["result"] == "completed" and snap["pass"]["complete"]
    assert snap["pass"]["failed_pending"] == [] and snap["pass"]["late_answers"] == []


async def test_late_answer_confirms_a_timed_out_property(
    hass: HomeAssistant, machine: FakeMachine, monkeypatch: pytest.MonkeyPatch
):
    from aioecam.exceptions import CommandTimeout

    entry, reader, calls = await _pass_setup(hass, machine, monkeypatch)
    read = entry.runtime_data.device.link.read
    late = PASS_NAMES[-1]

    async def times_out(name, **kwargs):
        if name == late:
            calls.append(name)
            raise CommandTimeout("answer only after the deadline")
        return await read(name, **kwargs)

    monkeypatch.setattr(entry.runtime_data.device.link, "read", times_out)
    reader.start(hass, entry)
    await reader.task
    assert reader.snapshot()["pass"]["failed_pending"] == [late]

    await machine.device.push(late, DESCRIPTOR)  # the machine answers after all
    async with asyncio.timeout(3):
        while not reader.snapshot()["pass"]["complete"]:
            await asyncio.sleep(0.05)
    snap = reader.snapshot()
    assert snap["pass"]["late_answers"] == [late] and snap["pass"]["pending"] == 0
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["recipe_read"]["pass"]["complete"]
    assert not reader_commands(machine)
