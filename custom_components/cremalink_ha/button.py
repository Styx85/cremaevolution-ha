"""Button platform for the Cremalink integration."""
from aioecam.exceptions import EcamError
from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, MANUFACTURER


# Translation keys for commands currently known on supported ECAM profiles.
#
# Unknown future commands deliberately fall back to a generated English
# name rather than disappearing from Home Assistant.
COMMAND_TRANSLATION_KEYS = {
    "americano": "brew_americano",
    "caffe_latte": "brew_caffe_latte",
    "cappuccino": "brew_cappuccino",
    "cappuccino_mix": "brew_cappuccino_mix",
    "cappuccino_plus": "brew_cappuccino_plus",
    "coffee": "brew_coffee",
    "cortado": "brew_cortado",
    "doppio_plus": "brew_doppio_plus",
    "double_espresso": "brew_double_espresso",
    "espresso": "brew_espresso",
    "espresso_macchiato": "brew_espresso_macchiato",
    "espresso_soul": "brew_espresso_soul",
    "flat_white": "brew_flat_white",
    "hot_milk": "brew_hot_milk",
    "hot_water": "brew_hot_water",
    "latte_macchiato": "brew_latte_macchiato",
    "long_coffee": "brew_long_coffee",
    "stop": "stop_brewing",
}


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the button platform.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry.
        async_add_entities: Function to add entities.
    """
    data = entry.runtime_data
    coordinator = data.coordinator
    statistics_coordinator = data.statistics_coordinator
    device = data.device

    cmds = sorted(device.profile.commands)

    entities = []
    for cmd in cmds:
        # Filter out power commands as they might be handled elsewhere
        if cmd.lower() not in ["wakeup", "standby", "refresh"]:
            entities.append(CremalinkButton(coordinator, device, cmd, entry))

    if statistics_coordinator is not None:
        entities.append(
            CremalinkStatisticsRefreshButton(
                statistics_coordinator,
                entry,
            )
        )

    if data.recipe_reader is not None:
        entities.append(CremalinkRecipeReadButton(coordinator, data.recipe_reader, entry))

    async_add_entities(entities)


class CremalinkButton(CoordinatorEntity, ButtonEntity):
    """Representation of a Cremalink button."""

    def __init__(self, coordinator, device, cmd, entry):
        """Initialize the button.

        Args:
            coordinator: The data update coordinator.
            device: The Cremalink device instance.
            cmd: The command associated with this button.
            entry: The config entry.
        """
        super().__init__(coordinator)
        self.device = device
        self._cmd = cmd
        self._title = cmd.replace("_", " ").title()

        translation_key = COMMAND_TRANSLATION_KEYS.get(cmd)

        if translation_key is not None:
            self._attr_has_entity_name = True
            self._attr_translation_key = translation_key
        else:
            # Preserve access to commands introduced by newer device maps
            # even before a translation has been added.
            self._attr_name = (
                f"Brew {self._title}"
            )

        self._attr_unique_id = f"{entry.entry_id}_cmd_{cmd}"
        self._attr_icon = "mdi:coffee"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
        )

    @property
    def available(self):
        """Return True if entity is available."""
        if self._cmd == "stop":
            return super().available and self.coordinator.data.is_busy
        return super().available and not self.coordinator.data.is_busy

    async def async_press(self):
        """Start the beverage (confirmed by the machine) or stop the current one."""
        try:
            if self._cmd == "stop":
                await self.device.stop()
            else:
                await self.device.brew(self._cmd)
        except EcamError as err:
            raise HomeAssistantError(f"{self._title}: {err}") from err
        await self.coordinator.async_request_refresh()


class CremalinkStatisticsRefreshButton(CoordinatorEntity, ButtonEntity):
    """Button that triggers one complete A2 statistics refresh."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "refresh_a2_statistics"
    _attr_icon = "mdi:database-refresh"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, entry):
        """Initialize the manual statistics refresh button."""
        super().__init__(coordinator)

        self._attr_unique_id = (
            f"{entry.entry_id}_statistics_refresh"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
        )

    @property
    def available(self):
        """Disable the button while a full A2 refresh is running."""
        return not self.coordinator.refresh_in_progress

    async def async_press(self):
        """Run one complete A2 statistics refresh."""
        await self.coordinator.async_force_refresh()


class CremalinkRecipeReadButton(CoordinatorEntity, ButtonEntity):
    """Read catalogue properties once; disabled until explicitly enabled."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_has_entity_name = True
    _attr_translation_key = "read_recipe_data"
    _attr_icon = "mdi:book-search-outline"

    def __init__(self, coordinator, reader, entry):
        super().__init__(coordinator)
        self.reader = reader
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_recipe_read"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title, manufacturer=MANUFACTURER,
        )

    @property
    def available(self):
        return (self.reader.device.available and not self.reader.device.is_busy()
                and not self.reader.running)

    async def async_press(self):
        try:
            if not self.reader.start(self.hass, self.entry):
                raise HomeAssistantError("Recipe read already running")
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
