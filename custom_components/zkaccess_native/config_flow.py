"""Config and options flows."""
from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components import network
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
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
        self._empty_search: list[str] | None = None

    def _known_networks(self) -> list[str]:
        """The /24 of every device already configured (found again without typing it)."""
        out: list[str] = []
        for entry in self._async_current_entries():
            try:
                net = str(ipaddress.ip_network(f"{entry.data[CONF_HOST]}/24", strict=False))
            except (KeyError, ValueError):
                continue
            if net not in out:
                out.append(net)
        return out

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Search: UDP broadcast on Home Assistant's subnet, and a TCP scan of its
        networks, of the networks of the devices already set up, and of the ones typed
        (devices on another subnet / VLAN). When nothing is found the form comes back;
        submitting it again unchanged goes to the manual entry."""
        errors: dict[str, str] = {}
        own = await _own_networks(self.hass)
        default = own + [n for n in self._known_networks() if n not in own]
        searched = default
        if user_input is not None:
            typed = [n.strip() for n in user_input.get(CONF_NETWORKS, "").replace(";", ",").split(",") if n.strip()]
            networks = default + [n for n in typed if n not in default]
            try:
                for net in typed:
                    ipaddress.ip_network(net, strict=False)
                if self._empty_search == networks:      # same search, nothing again: by hand
                    self._host = None
                    return await self.async_step_connect()
                self._found = await self.hass.async_add_executor_job(_search, networks, DEFAULT_PORT)
            except ValueError:
                errors[CONF_NETWORKS] = "invalid_network"
            else:
                if self._found:
                    return await self.async_step_select()
                self._empty_search = networks
                searched = networks
                errors["base"] = "no_devices"
        schema = vol.Schema({vol.Optional(CONF_NETWORKS, default=""): str})
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(
            schema, user_input or {}), errors=errors,
            description_placeholders={"own": ", ".join(searched) or "-"})

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

    # ------------------------------------------------------------ DHCP discovery
    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """A ZKTeco device seen on the network (MAC prefix 00:17:61). A device already set
        up gets its new address; a new one is offered for setup."""
        host = discovery_info.ip
        try:
            info = await self.hass.async_add_executor_job(_probe, host, DEFAULT_PORT, "")
        except DeviceError as ex:
            if ex.code != -14:
                return self.async_abort(reason="cannot_connect")
            info = None                                  # password protected: identified later
        except (ZKError, OSError):
            return self.async_abort(reason="cannot_connect")
        if info and info.serial_number:
            await self.async_set_unique_id(info.serial_number)
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        self._host = host
        name = ((info.device_name or "").strip() if info else "") or (info.serial_number if info else None) or host
        self.context["title_placeholders"] = {"name": name}
        return await self.async_step_dhcp_confirm()

    async def async_step_dhcp_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_connect()
        self._set_confirm_only()
        return self.async_show_form(step_id="dhcp_confirm",
                                    description_placeholders={"host": self._host or ""})

    # ------------------------------------------------- new address, new password
    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the address, port or password of a device already set up (everything else,
        people, phones, history, is kept)."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            errors, info = await self._check(data)
            if not errors:
                if info.serial_number and entry.unique_id and info.serial_number != entry.unique_id:
                    return self.async_abort(reason="wrong_device")
                return self.async_update_reload_and_abort(entry, data=data)
        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT): int,
            vol.Optional(CONF_PASSWORD, default=""): str,
        })
        return self.async_show_form(step_id="reconfigure", data_schema=self.add_suggested_values_to_schema(
            schema, user_input or dict(entry.data)), errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The device refuses the communication password: ask for the new one."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input.get(CONF_PASSWORD, "")}
            errors, _info = await self._check(data)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({
            vol.Optional(CONF_PASSWORD, default=""): str}), errors=errors,
            description_placeholders={"host": entry.data.get(CONF_HOST, "")})

    async def _check(self, data: Mapping[str, Any]):
        try:
            info = await self.hass.async_add_executor_job(
                _probe, data[CONF_HOST], data[CONF_PORT], data.get(CONF_PASSWORD, ""))
        except DeviceError as ex:
            return {"base": "invalid_auth" if ex.code == -14 else "cannot_connect"}, None
        except (ZKError, OSError):
            return {"base": "cannot_connect"}, None
        return {}, info

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
