"""Diagnostics support for ZKHNSO."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .const import CONF_PASSWORD, CONF_USERNAME, SESSION_JSESSIONID, SESSION_FORM_TOKEN

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    SESSION_JSESSIONID,
    SESSION_FORM_TOKEN,
    "loginToken",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics with account and session secrets removed."""
    return async_redact_data(
        {
            "entry": {
                "data": dict(entry.data),
                "options": dict(entry.options),
                "version": entry.version,
            }
        },
        TO_REDACT,
    )
