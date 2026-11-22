"""Sidebar panel ("Access control"), served from the integration's frontend folder."""
from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PANEL_URL, STATIC_URL

FRONTEND_DIR = Path(__file__).parent / "frontend"
_REGISTERED = f"{DOMAIN}_panel_registered"
_STATIC = f"{DOMAIN}_static_registered"
DEFAULT_PANEL_TITLE = "Access control"


async def async_register_panel(hass: HomeAssistant, version: str, title: str | None = None) -> None:
    if hass.data.get(_REGISTERED) or "frontend" not in hass.config.components:
        return
    hass.data[_REGISTERED] = {"version": version}
    if getattr(hass, "http", None) is not None and not hass.data.get(_STATIC):
        hass.data[_STATIC] = True
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False)])
    await _register(hass, version, title)


async def async_rename_panel(hass: HomeAssistant, title: str | None) -> None:
    """Change the sidebar title at once (the panel is registered again)."""
    registered = hass.data.get(_REGISTERED)
    if not registered:
        return
    frontend.async_remove_panel(hass, PANEL_URL)
    await _register(hass, registered["version"], title)


async def _register(hass: HomeAssistant, version: str, title: str | None) -> None:
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name="zkaccess-native-panel",
        module_url=f"{STATIC_URL}/zkaccess-panel.js?v={version}",
        sidebar_title=(title or "").strip() or DEFAULT_PANEL_TITLE,
        sidebar_icon="mdi:fingerprint",
        require_admin=True,
        config={},
    )


def async_unregister_panel(hass: HomeAssistant) -> None:
    if hass.data.pop(_REGISTERED, None):
        frontend.async_remove_panel(hass, PANEL_URL)
