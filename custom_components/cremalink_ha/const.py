"""Constants for the Cremalink Home Assistant integration."""

DOMAIN = "cremalink_ha"

# Config entry v2 (aioecam, no add-on)
CONF_DSN = "dsn"
CONF_DEVICE_IP = "device_ip"
CONF_LAN_KEY = "lan_key"
CONF_KEY_ID = "key_id"
CONF_PROFILE = "profile"
CONF_REFRESH_TOKEN = "refresh_token"  # optional: cloud wake fallback and service properties

# Options
CONF_LAN_PORT = "lan_port"
CONF_ADVERTISE_IP = "advertise_ip"
DEFAULT_LAN_PORT = 10280

# Config flow fields
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_MANUAL_SETUP = "manual_setup"

# Config entry v1 (cremalink + add-on), only read by the migration
LEGACY_CONF_ADDON_URL = "addon_url"
LEGACY_CONF_DEVICE_MAP = "device_map"
LEGACY_CONF_CONNECTION_TYPE = "connection_type"
LEGACY_CONF_TOKEN_FILE = "token_file"
LEGACY_DEVICE_MAP_TO_PROFILE = {
    "ECAM610": "ecam61x",
    "ECAM612": "ecam61x",
    "ECAM452": "ecam45x",
}

DEFAULT_PROFILE = "ecam61x"
MANUFACTURER = "De'Longhi"
