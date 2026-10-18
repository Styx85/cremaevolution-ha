"""Config flow: cloud-assisted onboarding by default, manual LAN setup as fallback.

Only the Ayla refresh token is stored, never the account password.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from aioecam.cloud.onboarding import OnboardedMachine, onboard
from aioecam.discovery import probe_module
from aioecam.ecam.profile import available_families
from aioecam.exceptions import AuthError, EcamError
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_ADVERTISE_IP,
    CONF_DEVICE_IP,
    CONF_DSN,
    CONF_EMAIL,
    CONF_KEY_ID,
    CONF_LAN_KEY,
    CONF_LAN_PORT,
    CONF_MANUAL_SETUP,
    CONF_PASSWORD,
    CONF_PROFILE,
    CONF_REFRESH_TOKEN,
    DEFAULT_LAN_PORT,
    DEFAULT_PROFILE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_EMAIL = TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username"))

LOGIN_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_EMAIL): _EMAIL,
        vol.Optional(CONF_PASSWORD): _PASSWORD,
        vol.Optional(CONF_MANUAL_SETUP, default=False): bool,
    }
)
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_EMAIL): _EMAIL, vol.Required(CONF_PASSWORD): _PASSWORD})


def _profile_selector() -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(options=available_families(), mode=SelectSelectorMode.DROPDOWN)
    )


def _entry_data(machine: OnboardedMachine, refresh_token: str) -> dict[str, Any]:
    return {
        CONF_DSN: machine.device.dsn,
        CONF_DEVICE_IP: machine.device.lan_ip,
        CONF_LAN_KEY: machine.lan_key,
        CONF_KEY_ID: machine.key_id,
        CONF_PROFILE: machine.profile_family or DEFAULT_PROFILE,
        CONF_REFRESH_TOKEN: refresh_token,
    }


class CremalinkConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self) -> None:
        self._machines: dict[str, OnboardedMachine] = {}
        self._refresh_token: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CremalinkOptionsFlow:
        return CremalinkOptionsFlow()

    async def _login(self, email: str, password: str) -> dict[str, str]:
        """Run onboarding; returns form errors (empty on success)."""
        try:
            result = await onboard(async_get_clientsession(self.hass), email, password)
        except AuthError:
            return {"base": "invalid_auth"}
        except (aiohttp.ClientError, TimeoutError, EcamError) as err:
            _LOGGER.debug("onboarding failed: %s", err)
            return {"base": "cannot_connect"}
        self._refresh_token = result.refresh_token
        self._machines = {m.device.dsn: m for m in result.machines}
        if not self._machines:
            return {"base": "no_machines"}
        return {}

    # --- cloud-assisted setup ---------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_MANUAL_SETUP):
                return await self.async_step_manual()
            if not user_input.get(CONF_EMAIL) or not user_input.get(CONF_PASSWORD):
                errors["base"] = "missing_credentials"
            else:
                errors = await self._login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
                if not errors:
                    if len(self._machines) == 1:
                        return await self._create_from(next(iter(self._machines)))
                    return await self.async_step_select()
        return self.async_show_form(step_id="user", data_schema=LOGIN_SCHEMA, errors=errors)

    async def async_step_select(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self._create_from(user_input[CONF_DSN])
        options = {
            dsn: f"{m.device.name or dsn} ({dsn})" for dsn, m in self._machines.items()
        }
        return self.async_show_form(
            step_id="select", data_schema=vol.Schema({vol.Required(CONF_DSN): vol.In(options)})
        )

    async def _create_from(self, dsn: str) -> ConfigFlowResult:
        machine = self._machines[dsn]
        await self.async_set_unique_id(dsn)
        self._abort_if_unique_id_configured()
        if not machine.lan_key or not machine.device.lan_ip:
            return self.async_abort(reason="no_lan")
        assert self._refresh_token
        return self.async_create_entry(
            title=machine.device.name or dsn, data=_entry_data(machine, self._refresh_token)
        )

    # --- manual setup --------------------------------------------------------------

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            ip = user_input[CONF_DEVICE_IP].strip()
            try:
                module = await probe_module(async_get_clientsession(self.hass), ip)
            except (aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                if not module.lan_ready or not module.dsn:
                    errors["base"] = "module_not_ready"
                else:
                    await self.async_set_unique_id(module.dsn)
                    self._abort_if_unique_id_configured(updates={CONF_DEVICE_IP: ip})
                    return self.async_create_entry(
                        title=user_input.get("name") or module.dsn,
                        data={
                            CONF_DSN: module.dsn,
                            CONF_DEVICE_IP: ip,
                            CONF_LAN_KEY: user_input[CONF_LAN_KEY].strip(),
                            CONF_PROFILE: user_input[CONF_PROFILE],
                        },
                    )
        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_IP): str,
                vol.Required(CONF_LAN_KEY): _PASSWORD,
                vol.Required(CONF_PROFILE, default=DEFAULT_PROFILE): _profile_selector(),
                vol.Optional("name"): str,
            }
        )
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)

    # --- reauth (also completes migrated cloud-only entries) ----------------------

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            errors = await self._login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
            if not errors:
                machine = self._machines.get(entry.data[CONF_DSN])
                if machine is None:
                    errors["base"] = "machine_not_on_account"
                elif not machine.lan_key or not machine.device.lan_ip:
                    return self.async_abort(reason="no_lan")
                else:
                    assert self._refresh_token
                    return self.async_update_reload_and_abort(
                        entry, data={**entry.data, **_entry_data(machine, self._refresh_token)}
                    )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    # --- reconfigure: new IP address ---------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            ip = user_input[CONF_DEVICE_IP].strip()
            try:
                module = await probe_module(async_get_clientsession(self.hass), ip)
            except (aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                if module.dsn != entry.data[CONF_DSN]:
                    errors["base"] = "wrong_machine"
                else:
                    return self.async_update_reload_and_abort(
                        entry, data={**entry.data, CONF_DEVICE_IP: ip}
                    )
        schema = vol.Schema(
            {vol.Required(CONF_DEVICE_IP, default=entry.data.get(CONF_DEVICE_IP) or ""): str}
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)


class CremalinkOptionsFlow(OptionsFlowWithReload):
    """Callback server port and the address announced to the machine.

    Reloads the entry only when options change; data updates (e.g. a rotated
    refresh token) never reload it.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_LAN_PORT, default=opts.get(CONF_LAN_PORT, DEFAULT_LAN_PORT)
                ): vol.All(int, vol.Range(min=1024, max=65535)),
                vol.Optional(
                    CONF_ADVERTISE_IP, description={"suggested_value": opts.get(CONF_ADVERTISE_IP)}
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
