"""Send persistent, once-per-verification-date meter alerts."""
from __future__ import annotations

import asyncio
from datetime import date
import json
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .sensor import ZKHMeterSensor

EVENT_METER_VERIFICATION_ALERT = "zkhnso_meter_verification_alert"


class MeterVerificationAlerts:
    """Remember sent alerts across reloads and Home Assistant restarts."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.store = Store(hass, 1, f"zkhnso_verification_alerts_{entry_id}")
        self._sent: set[str] = set()
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Load previously sent alerts before creating meter sensors."""
        data = await self.store.async_load() or {}
        self._sent = set(data.get("sent", []))

    async def async_check(self, sensor: ZKHMeterSensor) -> None:
        """Send an alert if this active meter is due within 30 days or overdue."""
        async with self._lock:
            attributes = sensor.extra_state_attributes
            if not attributes or attributes.get("is_archived"):
                return

            try:
                verification_date = date.fromisoformat(attributes["next_verification_date"])
            except (KeyError, TypeError, ValueError):
                return

            days_left = (verification_date - dt_util.now().date()).days
            if days_left > 30:
                return

            key = json.dumps([sensor.unique_id, verification_date.isoformat()], ensure_ascii=False)
            if key in self._sent:
                return

            updated = self._sent | {key}
            await self.store.async_save({"sent": sorted(updated)})
            self._sent = updated
            self.hass.bus.async_fire(
                EVENT_METER_VERIFICATION_ALERT,
                {
                    "entry_id": self.entry_id,
                    "entity_id": sensor.entity_id,
                    "account_id": sensor._account_id,
                    "meter_id": sensor._meter_key,
                    "serial_number": attributes.get("serial_number"),
                    "name": attributes.get("meter_name"),
                    "next_verification_date": verification_date.isoformat(),
                    "days_left": days_left,
                },
            )
