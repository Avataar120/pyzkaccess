"""Config and options flows."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import callback
from pyzkaccess_native import DeviceError, ZKAccess, ZKError

from .const import (CONF_OPEN_SECONDS, CONF_POLL_INTERVAL, CONF_SYNC_CLOCK, DEFAULT_OPEN_SECONDS,
                    DEFAULT_POLL_INTERVAL, DEFAULT_PORT, DEFAULT_SYNC_CLOCK, DOMAIN)


def _probe(host: str, port: int, password: str):
    with ZKAccess(host, port, password, timeout=5) as dev:
        return dev.info()


class ZKAccessConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await self.hass.async_add_executor_job(
                    _probe, user_input[CONF_HOST], user_input[CONF_PORT],
                    user_input.get(CONF_PASSWORD, ""))
            except DeviceError as ex:
                errors["base"] = "invalid_auth" if ex.code == -14 else "cannot_connect"
            except (ZKError, OSError):
                errors["base"] = "cannot_connect"
            else:
                serial = info.serial_number or f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured(updates=user_input)
                return self.async_create_entry(title=info.device_name or f"ZKTeco {serial}",
                                               data=user_input)
        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
            vol.Optional(CONF_PASSWORD, default=""): str,
        })
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(
            schema, user_input or {}), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ZKAccessOptionsFlow()


class ZKAccessOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required(CONF_POLL_INTERVAL, default=opts.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)):
                vol.All(vol.Coerce(float), vol.Range(min=0.2, max=60)),
            vol.Required(CONF_OPEN_SECONDS, default=opts.get(CONF_OPEN_SECONDS, DEFAULT_OPEN_SECONDS)):
                vol.All(int, vol.Range(min=1, max=254)),
            vol.Required(CONF_SYNC_CLOCK, default=opts.get(CONF_SYNC_CLOCK, DEFAULT_SYNC_CLOCK)): bool,
        }))
