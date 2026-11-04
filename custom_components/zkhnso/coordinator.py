"""Data update coordinator for the ZKHNSO integration."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import ZKHAPIClient, ZKHAPIConnectionError
from .const import CONF_PASSWORD, CONF_USERNAME, DOMAIN

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(hours=1)


class ZKHDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch meter and tariff data from the ZKHNSO personal account."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=SCAN_INTERVAL,
        )
        self.entry = entry

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data, translating service failures into Home Assistant errors."""
        async with ZKHAPIClient(self.entry.data[CONF_USERNAME], self.entry.data[CONF_PASSWORD]) as client:
            if not await client.preflight():
                raise UpdateFailed("Failed to initialize ZKHNSO session")

            try:
                logged_in = await client.login()
            except ZKHAPIConnectionError as err:
                raise UpdateFailed("Failed to connect to ZKHNSO during login") from err
            if not logged_in:
                raise ConfigEntryAuthFailed("Invalid ZKHNSO credentials")

            meters = await client.get_meters()
            if meters is None:
                raise UpdateFailed("Failed to fetch ZKHNSO meters data")

            tariffs = await client.get_tariffs()
            if tariffs is None:
                raise UpdateFailed("Failed to fetch ZKHNSO tariffs data")

        return {
            "meters": meters,
            "tariffs": tariffs,
        }
