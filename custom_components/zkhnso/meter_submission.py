"""Validate meter readings received through the Home Assistant event bus."""
from __future__ import annotations

from datetime import date
import logging
import re
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback

from .api_client import ZKHAPIClient
from .const import CONF_PASSWORD, CONF_USERNAME, DOMAIN
from .coordinator import ZKHDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)
EVENT_SEND_METER = "zkhnso_send_meters"
_DATA_KEY = f"{DOMAIN}_meter_submission"


class MeterSubmission:
    """Route one event to the appropriate configured accounts."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.coordinators: dict[str, ZKHDataUpdateCoordinator] = {}
        self.unsubscribe = hass.bus.async_listen(EVENT_SEND_METER, self.async_handle_event)

    async def async_handle_event(self, event: Event) -> None:
        account_id = event.data.get("account_id")
        readings = event.data.get("meters")
        if not isinstance(account_id, str) or not account_id.strip() or not isinstance(readings, list) or not readings:
            _LOGGER.error("Meters submission requires account_id and meters in event data")
            return

        owners = [
            (entry_id, coordinator)
            for entry_id, coordinator in self.coordinators.items()
            if account_id in (coordinator.data or {}).get("accounts", {})
        ]
        if not owners:
            _LOGGER.error("Account %s not found for meter submission", account_id)
            return
        if len(owners) > 1:
            _LOGGER.warning("Account %s belongs to multiple integration entries; submitting only for the first one", account_id)

        entry_id, coordinator = owners[0]
        meters = coordinator.data["accounts"][account_id].get("meters", {})
        payload = {}
        i = 0
        for reading in readings:
            try:
                if not isinstance(reading, dict) or not "serial_number" in reading or not "value" in reading:
                    raise KeyError("Meter submission has insufficient data: {reading}", reading)
                serial_number = reading["serial_number"]
                value = float(reading["value"])
                if (meter := meters.get(reading["serial_number"])) is None:
                    raise LookupError(f"Meter {serial_number} not found in account {account_id}")
                if meter.get("is_archived"):
                    raise ValueError(f"Meter {serial_number} in account {account_id} is inactive")
                if not meter.get("zkhnso_id"):
                    raise ValueError(f"Meter {serial_number} in account {account_id} has readings submission blocked")
                old_value = float(meter.get("value", 0))
                diff =  value - old_value
                if diff < 0:
                    raise ValueError(f"Meter {serial_number} in account {account_id} has value {old_value} larger than submitted {value}")

                payload.update({
                    f"counterCode[{i}]": meters[reading["serial_number"]]["zkhnso_id"],
                    f"counterDate[{i}]": (date.fromisoformat(reading["date"]) if "date" in reading else date.today()).strftime("%d.%m.%Y"),
                    f"counterValue[{i}]": value,
                    f"counterDiff[{i}]": diff,
                })
                i += 1
            except (KeyError, LookupError, ValueError) as e:
                _LOGGER.warning(e)

        if not payload or self.coordinators.get(entry_id) is not coordinator:
            _LOGGER.error("Nothing to send or no coordinator for entry %s found", entry_id)
            return

        try:
            async with ZKHAPIClient(coordinator.entry.data[CONF_USERNAME], coordinator.entry.data[CONF_PASSWORD]) as client:
                if not await client.preflight() or not await client.login():
                    _LOGGER.error("Unable to authenticate for meter submission to account %s", account_id)
                    return
                if account_id not in client.accounts:
                    _LOGGER.error("Account %s is no longer available for meter submission", account_id)
                    return
                if client.current_account_id != account_id:
                    await client.change_account(account_id)
                await client.post_meters(payload)
        except Exception:
            _LOGGER.exception("Unable to submit meter readings for account %s", account_id)


@callback
def async_register_meter_submission(hass: HomeAssistant, coordinator: ZKHDataUpdateCoordinator) -> None:
    """Register a shared listener and remove it after the last entry unloads."""
    manager = hass.data.get(_DATA_KEY)
    if manager is None:
        manager = hass.data[_DATA_KEY] = MeterSubmission(hass)
    entry_id = coordinator.entry.entry_id
    manager.coordinators[entry_id] = coordinator

    @callback
    def remove() -> None:
        manager.coordinators.pop(entry_id, None)
        if not manager.coordinators:
            manager.unsubscribe()
            hass.data.pop(_DATA_KEY, None)

    coordinator.entry.async_on_unload(remove)
