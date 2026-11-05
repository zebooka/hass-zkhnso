"""Sensor platform for ZKHNSO integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfArea, UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ACCOUNT_ID, CONF_WATER_HEATING_RATE
from .coordinator import ZKHDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

SQUARE_UNITS = {"кв.м.", "кв.м", "м²", "m²"}
VOLUME_UNITS = {"куб.м.", "куб.м", "м³", "m³"}
ELECTRIC_UNITS = {"кВтч", "кВт·ч", "кВт⋅ч", "kWh"}
HEAT_UNITS = {"Гкал", "Gcal"}
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ZKH sensor platform."""
    coordinator: ZKHDataUpdateCoordinator = entry.runtime_data

    # Create sensors dynamically based on meters and tariffs data
    entities = []

    # Create meter sensors
    if coordinator.data and isinstance(coordinator.data, dict) and "meters" in coordinator.data:
        meters = coordinator.data["meters"]
        if meters and isinstance(meters, dict) and len(meters) > 0:
            meter_entities = [
                ZKHMeterSensor(coordinator, meter_key, meter_data)
                for meter_key, meter_data in meters.items()
            ]
            entities.extend(meter_entities)
            _LOGGER.info("Created %d meter sensors", len(meter_entities))
        else:
            _LOGGER.warning(
                "Meters data is empty or invalid. Meters: %s (type: %s, len: %s)",
                meters,
                type(meters),
                len(meters) if isinstance(meters, dict) else "N/A",
            )

    # Create tariff sensors
    if coordinator.data and isinstance(coordinator.data, dict) and "tariffs" in coordinator.data:
        tariffs = coordinator.data["tariffs"]
        if tariffs and isinstance(tariffs, dict) and len(tariffs) > 0:
            tariff_entities = [
                ZKHTariffSensor(coordinator, key, tariff)
                for key, tariff in tariffs.items()
            ]
            _LOGGER.info("Configured water heating rate: %s Gcal/m³", entry.options.get(CONF_WATER_HEATING_RATE))
            entities.extend(tariff_entities)
            _LOGGER.info("Created %d tariff sensors", len(tariff_entities))
        else:
            _LOGGER.warning(
                "Tariffs data is empty or invalid. Tariffs: %s (type: %s, len: %s)",
                tariffs,
                type(tariffs),
                len(tariffs) if isinstance(tariffs, dict) else "N/A",
            )

    if entities:
        async_add_entities(entities, update_before_add=False)
        _LOGGER.info("Total sensors created: %d", len(entities))
    else:
        _LOGGER.error(
            "No sensors created. Coordinator data: %s (type: %s). "
            "This might indicate an API issue. Check logs for errors.",
            coordinator.data,
            type(coordinator.data) if coordinator.data else None,
        )


class ZKHMeterSensor(CoordinatorEntity, SensorEntity):
    """Representation of a ZKH meter sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ZKHDataUpdateCoordinator,
        key: str,
        meter: dict,
    ) -> None:
        """Initialize the meter sensor."""
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{self.coordinator.entry.entry_id}_meter_{key}"
        self._attr_translation_key = "meter"
        self._attr_translation_placeholders = {
            "name": str(meter.get("name", key)),
            "serial_number": str(meter.get("serial_number")),
            "account_id": str(self.coordinator.entry.data.get(CONF_ACCOUNT_ID)),
        }
        self._attr_icon = "mdi:counter"
        self._attr_state_class = SensorStateClass.TOTAL

        meter_name_lower = str(meter.get("name")).casefold()
        unit = meter.get("unit")
        if unit in VOLUME_UNITS:
            if "вода" in meter_name_lower:
                self._attr_device_class = SensorDeviceClass.WATER
            elif "газ" in meter_name_lower:
                self._attr_device_class = SensorDeviceClass.GAS
            else:
                self._attr_device_class = SensorDeviceClass.VOLUME
            self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
        elif unit in ELECTRIC_UNITS:
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        elif unit in HEAT_UNITS:
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.GIGA_CALORIE

    @property
    def meter(self) -> dict | None:
        """Return current meter data from the coordinator."""
        if not self.coordinator.data:
            return None

        meters = self.coordinator.data.get("meters", {})
        return meters.get(self.key) if isinstance(meters, dict) else None

    @property
    def native_value(self) -> float | None:
        """Return the current meter value."""
        meter = self.meter
        return meter.get("value") if meter is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return current meter attributes."""
        meter = self.meter
        if meter is None:
            return {}

        return {
            "name": meter.get("name"),
            "serial_number": meter.get("serial_number"),
            "unit": meter.get("unit"),
            "date": meter.get("date"),
            "next_verification_date": meter.get("next_verification_date"),
        }


class ZKHTariffSensor(CoordinatorEntity, SensorEntity):
    """Representation of a ZKH tariff sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ZKHDataUpdateCoordinator,
        key: str,
        tariff: dict,
    ) -> None:
        """Initialize the tariff sensor."""
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{self.coordinator.entry.entry_id}_tariff_{key}"
        self._attr_translation_key = "tariff"
        self._attr_translation_placeholders = {
            "name": str(tariff.get("name", key)),
            "account_id": str(self.coordinator.entry.data.get(CONF_ACCOUNT_ID)),
        }
        self._attr_native_unit_of_measurement = self._normalized_unit(tariff.get("unit"))
        self._attr_icon = "mdi:currency-rub"

    @property
    def tariff(self) -> dict | None:
        """Return current tariff data from the coordinator."""
        if not self.coordinator.data:
            return None

        tariffs = self.coordinator.data.get("tariffs", {})
        return tariffs.get(self.key) if isinstance(tariffs, dict) else None

    @property
    def native_value(self) -> float | None:
        """Return the current tariff value."""
        tariff = self.tariff
        return tariff.get("rate_per_unit") if tariff is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return current tariff attributes."""
        tariff = self.tariff
        if tariff is None:
            return {}

        return {
            "name": tariff.get("name"),
            "norm": tariff.get("norm"),
            "unit": tariff.get("unit"),
            "from_date": tariff.get("from_date"),
        }

    @staticmethod
    def _normalized_unit(unit: str | None) -> str:
        if unit in SQUARE_UNITS:
            return f"RUB/{UnitOfArea.SQUARE_METERS}"
        if unit in VOLUME_UNITS:
            return f"RUB/{UnitOfVolume.CUBIC_METERS}"
        if unit in ELECTRIC_UNITS:
            return f"RUB/{UnitOfEnergy.KILO_WATT_HOUR}"
        if unit in HEAT_UNITS:
            return f"RUB/{UnitOfEnergy.GIGA_CALORIE}"
        return f"RUB/{unit}" if unit else "RUB"
