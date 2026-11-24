"""Readable logbook entries for access events (English, or French when Home Assistant is)."""
from __future__ import annotations

from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_ACCESS

TEXTS = {
    "en": {"fingerprint": "fingerprint", "card": "card", "code": "keypad code", "remote": "remote",
           "phone": "phone", "second": "second presentation", "denied": "denied",
           "unknown_card": "Unknown card", "user_id": "UserID {n}", "device": "Access control"},
    "fr": {"fingerprint": "doigt", "card": "carte", "code": "code clavier", "remote": "à distance",
           "phone": "téléphone", "second": "deuxième présentation", "denied": "refusé",
           "unknown_card": "Carte inconnue", "user_id": "UserID {n}", "device": "Contrôle d'accès"},
}
#: Device event names (as reported by the library) shown for refusals, in French.
EVENT_NAMES_FR = {
    "unregistered card": "carte inconnue",
    "unregistered fingerprint": "doigt inconnu",
    "access denied": "accès refusé",
    "phone refused": "téléphone refusé",
    "illegal time zone": "hors plage horaire",
    "anti passback": "anti-passback",
    "remote opening": "ouverture à distance",
    "cancel alarm": "alarme annulée",
}


@callback
def async_describe_events(hass: HomeAssistant, async_describe_event) -> None:
    lang = "fr" if (hass.config.language or "").lower().startswith("fr") else "en"
    txt = TEXTS[lang]

    @callback
    def describe(event: Event) -> dict[str, str]:
        d = event.data
        who = d.get("person_name") or (txt["user_id"].format(n=d["user_id"]) if d.get("user_id") else None)
        if not who:
            who = txt["unknown_card"] if d.get("method") == "card" else txt["device"]
        method = txt.get(d.get("method") or "", d.get("method") or "")
        event_name = (d.get("event_name") or "").lower()
        if lang == "fr":
            event_name = EVENT_NAMES_FR.get(event_name, event_name)
        if d.get("result") in ("granted", "action"):
            what = d.get("category") or d.get("event_name")
            message = f"{what} ({method})" if method else str(what)
            if d.get("gesture") == "double":
                message += f", {txt['second']}"
            if d.get("phone"):
                message += f" — {d['phone']}"
        elif d.get("result") == "denied":
            message = f"{txt['denied']} : {event_name}" if lang == "fr" else f"{txt['denied']}: {event_name}"
        else:
            message = event_name if lang == "fr" else d.get("event_name", "")
        return {"name": who, "message": message}

    async_describe_event(DOMAIN, EVENT_ACCESS, describe)
