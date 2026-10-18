"""Switch platform for the Cremalink integration."""
from aioecam.exceptions import EcamError
from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, MANUFACTURER


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the switch platform.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry.
        async_add_entities: Function to add entities.
    """
    data = entry.runtime_data
    coordinator = data.coordinator
    device = data.device
    async_add_entities([CremalinkPowerSwitch(coordinator, device, entry)])


class CremalinkPowerSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of a Cremalink power switch."""

    def __init__(self, coordinator, device, entry):
        """Initialize the switch.

        Args:
            coordinator: The data update coordinator.
            device: The Cremalink device instance.
            entry: The config entry.
        """
        super().__init__(coordinator)
        self.device = device
        self._attr_has_entity_name = True
        self._attr_translation_key = "power"
        self._attr_unique_id = f"{entry.entry_id}_power"
        self._attr_icon = "mdi:power"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
        )

    @property
    def is_on(self):
        """Return true if the switch is on."""
        if not self.coordinator.data or not self.coordinator.data.status_name:
            return None
        # Check if the status indicates the device is not in standby
        return self.coordinator.data.status_name.lower() not in ["standby", "in_standby"]

    @property
    def available(self):
        """Return True if entity is available."""
        if not self.coordinator.data:
            return False
        return super().available

    async def async_turn_on(self, **kwargs):
        """Wake the machine; returns once the machine confirms it is waking up."""
        try:
            await self.device.power_on()
        except EcamError as err:
            raise HomeAssistantError(f"Could not switch the machine on: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        """Put the machine into standby; returns once it confirms."""
        try:
            await self.device.power_off()
        except EcamError as err:
            raise HomeAssistantError(f"Could not switch the machine off: {err}") from err
        await self.coordinator.async_request_refresh()
