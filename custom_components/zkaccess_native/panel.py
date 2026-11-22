"""Sidebar panel ("Access control"), served from the integration's frontend folder."""
from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PANEL_URL, STATIC_URL

FRONTEND_DIR = Path(__file__).parent / "frontend"
_REGISTERED = f"{DOMAIN}_panel_registered"


async def async_register_panel(hass: HomeAssistant, version: str) -> None:
    if hass.data.get(_REGISTERED) or "frontend" not in hass.config.components:
        return
    hass.data[_REGISTERED] = True
    if getattr(hass, "http", None) is not None:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False)])
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name="zkaccess-native-panel",
        module_url=f"{STATIC_URL}/zkaccess-panel.js?v={version}",
        sidebar_title="Access control",
        sidebar_icon="mdi:fingerprint",
        require_admin=True,
        config={},
    )


def async_unregister_panel(hass: HomeAssistant) -> None:
    if hass.data.pop(_REGISTERED, None):
        frontend.async_remove_panel(hass, PANEL_URL)
