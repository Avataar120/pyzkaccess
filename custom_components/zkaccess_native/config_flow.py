"""Config and options flows."""
from __future__ import annotations

from typing import Any

import ipaddress

import voluptuous as vol
from homeassistant.components import network
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.selector import (SelectOptionDict, SelectSelector, SelectSelectorConfig,
                                            SelectSelectorMode)
from pyzkaccess_native import DeviceError, DiscoveredDevice, ZKAccess, ZKError, discover, scan

from .const import (CONF_OPEN_SECONDS, CONF_POLL_INTERVAL, CONF_SYNC_CLOCK, DEFAULT_OPEN_SECONDS,
                    DEFAULT_POLL_INTERVAL, DEFAULT_PORT, DEFAULT_SYNC_CLOCK, DOMAIN, POLL_INTERVAL_MAX,
                    POLL_INTERVAL_MIN)


CONF_NETWORKS = "networks"
CONF_DEVICE = "device"
MANUAL = "manual"
#: Own networks wider than this are scanned as the /24 around Home Assistant's address.
OWN_NETWORK_MIN_PREFIX = 22


def _probe(host: str, port: int, password: str):
    with ZKAccess(host, port, password, timeout=5) as dev:
        return dev.info()


def _search(networks: list[str], port: int) -> list[DiscoveredDevice]:
    found: dict[str, DiscoveredDevice] = {}
    try:
        for dev in discover(timeout=2.0):              # same subnet, UDP broadcast
            if dev.ip:
                found[dev.ip] = dev
    except OSError:
        pass
    if networks:                                       # any subnet, TCP on the device port
        for dev in scan(networks, port=port):
            found.setdefault(dev.ip, dev)
    return sorted(found.values(), key=lambda d: tuple(int(x) for x in (d.ip or "0.0.0.0").split(".")))


async def _own_networks(hass) -> list[str]:
    """Home Assistant's IPv4 networks (a /24 around its address when wider than /22)."""
    out: list[str] = []
    try:
        adapters = await network.async_get_adapters(hass)
    except Exception:  # noqa: BLE001 - search still works with typed networks
        return out
    for adapter in adapters:
        if not adapter.get("enabled"):
            continue
        for ip in adapter.get("ipv4", []):
            prefix = int(ip["network_prefix"])
            if prefix < OWN_NETWORK_MIN_PREFIX:
                prefix = 24
            net = str(ipaddress.ip_network(f"{ip['address']}/{prefix}", strict=False))
            if not net.startswith("127.") and net not in out:
                out.append(net)
    return out


def _label(dev: DiscoveredDevice) -> str:
    name = (dev.device_name or "").strip()
    if dev.raw.get("auth") == "required":
        return f"{dev.ip} - password required"
    parts = [p for p in (name, dev.serial_number, dev.firmware) if p]
    return f"{dev.ip} - {' · '.join(parts)}" if parts else str(dev.ip)


class ZKAccessConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._found: list[DiscoveredDevice] = []
        self._host: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Search: UDP broadcast on Home Assistant's subnet, and a TCP scan of its
        networks plus the ones typed (devices on another subnet / VLAN)."""
        errors: dict[str, str] = {}
        own = await _own_networks(self.hass)
        if user_input is not None:
            typed = [n.strip() for n in user_input.get(CONF_NETWORKS, "").replace(";", ",").split(",") if n.strip()]
            try:
                for net in typed:
                    ipaddress.ip_network(net, strict=False)
                networks = own + [n for n in typed if n not in own]
                self._found = await self.hass.async_add_executor_job(_search, networks, DEFAULT_PORT)
            except ValueError:
                errors[CONF_NETWORKS] = "invalid_network"
            else:
                return await self.async_step_select()
        schema = vol.Schema({vol.Optional(CONF_NETWORKS, default=""): str})
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(
            schema, user_input or {}), errors=errors,
            description_placeholders={"own": ", ".join(own) or "-"})

    async def async_step_select(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        configured = {e.unique_id for e in self._async_current_entries()}
        if user_input is not None:
            choice = user_input[CONF_DEVICE]
            dev = next((d for d in self._found if d.ip == choice), None)
            if dev and dev.serial_number in configured:
                return self.async_abort(reason="already_configured")
            self._host = None if choice == MANUAL else choice
            return await self.async_step_connect()
        # Devices already set up stay listed (marked): the search visibly found them.
        options = [SelectOptionDict(value=d.ip, label=_label(d) + (" (already configured)"
                                                                   if d.serial_number in configured else ""))
                   for d in self._found if d.ip]
        options.append(SelectOptionDict(value=MANUAL, label=MANUAL))
        return self.async_show_form(step_id="select", data_schema=vol.Schema({
            vol.Required(CONF_DEVICE, default=options[0]["value"]): SelectSelector(SelectSelectorConfig(
                options=options, mode=SelectSelectorMode.LIST, translation_key=CONF_DEVICE))}),
            description_placeholders={"count": str(len(self._found))})

    async def async_step_connect(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
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
                title = (info.device_name or "").strip() or f"ZKTeco {serial}"
                return self.async_create_entry(title=title, data=user_input)
        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
            vol.Optional(CONF_PASSWORD, default=""): str,
        })
        suggested = user_input or ({CONF_HOST: self._host} if self._host else {})
        return self.async_show_form(step_id="connect", data_schema=self.add_suggested_values_to_schema(
            schema, suggested), errors=errors)

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
                vol.All(vol.Coerce(float), vol.Range(min=POLL_INTERVAL_MIN, max=POLL_INTERVAL_MAX)),
            vol.Required(CONF_OPEN_SECONDS, default=opts.get(CONF_OPEN_SECONDS, DEFAULT_OPEN_SECONDS)):
                vol.All(int, vol.Range(min=1, max=254)),
            vol.Required(CONF_SYNC_CLOCK, default=opts.get(CONF_SYNC_CLOCK, DEFAULT_SYNC_CLOCK)): bool,
        }))
