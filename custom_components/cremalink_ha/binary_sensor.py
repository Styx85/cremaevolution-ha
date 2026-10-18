"""Binary sensor platform for the Cremalink integration."""
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER

BINARY_SENSORS = [
    ("is_busy", "Busy", None, BinarySensorDeviceClass.RUNNING),
    ("is_idle", "Idle", "mdi:sleep", None),
    ("is_watertank_open", "Water Tank Open", "mdi:water-boiler-alert", BinarySensorDeviceClass.DOOR),
    ("is_watertank_empty", "Water Tank Empty", "mdi:water-off", BinarySensorDeviceClass.PROBLEM),
    ("is_waste_container_full", "Waste Container Full", "mdi:delete-alert", BinarySensorDeviceClass.PROBLEM),
    ("is_waste_container_missing", "Waste Container Missing", "mdi:delete-alert", BinarySensorDeviceClass.PROBLEM),
]


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the binary sensor platform.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry.
        async_add_entities: Function to add entities.
    """
    data = entry.runtime_data
    coordinator = data.coordinator

    # Only flags this machine's profile can report; e.g. the PrimaDonna Soul has
    # no reliable "water tank empty" bit, so that entity is not created.
    supported = set(coordinator.data.available_fields)
    # Remove entities of flags an earlier version created but this machine
    # cannot report (they would stay "unavailable" forever).
    registry = er.async_get(hass)
    for key, *_ in BINARY_SENSORS:
        if key not in supported and (
            stale := registry.async_get_entity_id("binary_sensor", DOMAIN, f"{entry.entry_id}_{key}")
        ):
            registry.async_remove(stale)
    entities = [
        CremalinkBinarySensor(coordinator, entry, key, name, icon, dev_class)
        for key, name, icon, dev_class in BINARY_SENSORS
        if key in supported
    ]

    async_add_entities(entities)


class CremalinkBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of a Cremalink binary sensor."""
    def __init__(self, coordinator, entry, key, name, icon, dev_class):
        """Initialize the binary sensor.

        Args:
            coordinator: The data update coordinator.
            entry: The config entry.
            key: The key to identify the sensor data.
            name: The name of the sensor.
            icon: The icon for the sensor.
            dev_class: The device class of the sensor.
        """
        super().__init__(coordinator)
        self._key = key
        self._attr_has_entity_name = True
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_icon = icon
        self._attr_device_class = dev_class
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
        )

    @property
    def available(self):
        """Return True if entity is available."""
        if not self.coordinator.data:
            return False
        return super().available

    @property
    def is_on(self):
        """Return True if the binary sensor is on."""
        return getattr(self.coordinator.data, self._key, None)
