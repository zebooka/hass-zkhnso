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
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ACCOUNT_ID, CONF_ADDRESS, CONF_WATER_HEATING_RATE, DOMAIN
from .coordinator import ZKHDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

SQUARE_UNITS = {"кв.м.", "кв.м", "м²", "m²"}
VOLUME_UNITS = {"куб.м.", "куб.м", "м³", "m³"}
ELECTRIC_UNITS = {"кВтч", "кВт·ч", "кВт⋅ч", "kWh"}
HEAT_UNITS = {"Гкал", "Gcal"}
WASTE_WATER_TARIFFS = ["ВОДООТВЕДЕНИЕ", "ОТВЕД. СТОЧ. ВОД СОИ"]
COLD_WATER_TARIFFS = ["ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", "ХВ НА СОД-Е ОБЩ.ИМУЩ."]
HOT_WATER_TARIFFS = ["ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ", "ГВ НА СОД-Е ОБЩ.ИМУЩ."]
HOT_WATER_HEATING_TARIFFS = ["ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ (ПОДОГРЕВ)", "ГВ НА СОИ (ПОДОГРЕВ)", "ОТОПЛЕНИЕ"]
HOT_WATER_FLUID_TARIFFS = ["ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ (ТЕПЛОНОСИТ)", "ГВ НА СОИ (ТЕПЛОНОСИТ)", "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", "ХВ НА СОД-Е ОБЩ.ИМУЩ."]
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
            tariff_entities.append(ZKHCombinedColdWaterTariffSensor(coordinator))
            _LOGGER.info("Configured water heating rate: %s Gcal/m³", entry.options.get(CONF_WATER_HEATING_RATE))
            tariff_entities.append(ZKHCombinedHotWaterTariffSensor(coordinator, entry.options.get(CONF_WATER_HEATING_RATE)))
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


class ZKHSensor(CoordinatorEntity, SensorEntity):
    """Base class for sensors belonging to the same integration entry."""

    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        """Group sensors of this entry under a single device."""
        data = self.coordinator.entry.data
        account_id = data.get(CONF_ACCOUNT_ID)
        address = data.get(CONF_ADDRESS)

        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.entry.entry_id)},
            name=f" №{account_id} {address}",
        )


class ZKHMeterSensor(ZKHSensor):
    """Representation of a ZKH meter sensor."""

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


class ZKHTariffSensor(ZKHSensor):
    """Representation of a ZKH tariff sensor."""

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

class ZKHCombinedTariffSensor(ZKHSensor):
    """Representation of a ZKH combined tariff sensor."""

    def __init__(
        self,
        coordinator: ZKHDataUpdateCoordinator,
        name: str,
    ) -> None:
        """Initialize the tariff sensor."""
        super().__init__(coordinator)
        self.key = type
        self._attr_unique_id = f"{self.coordinator.entry.entry_id}_combined_tariff_{name}"
        self._attr_translation_key = "combined_tariff"
        self._attr_translation_placeholders = {
            "name": str(name),
            "account_id": str(self.coordinator.entry.data.get(CONF_ACCOUNT_ID)),
        }
        self._attr_native_unit_of_measurement = f"RUB/{UnitOfVolume.CUBIC_METERS}"
        self._attr_icon = "mdi:sigma"

    @property
    def _waste_water(self) -> dict[str, Any] | None:
        return self._get_first_available_tariff(WASTE_WATER_TARIFFS, VOLUME_UNITS)

    def _get_first_available_tariff(self, names: list[str], units: set[str]) -> dict[str, Any] | None:
        """Return the first tariff with a value, in the given name order."""
        data = self.coordinator.data
        tariffs = data.get("tariffs") if isinstance(data, dict) else None
        if not isinstance(tariffs, dict):
            return None

        for name in names:
            for tariff in tariffs.values():
                if (
                    isinstance(tariff, dict)
                    and tariff.get("name") == name
                    and tariff.get("rate_per_unit") is not None
                    and tariff.get("unit") in units
                ):
                    return tariff

        return None

class ZKHCombinedColdWaterTariffSensor(ZKHCombinedTariffSensor):
    """Representation of a ZKH combined cold water tariff sensor."""
    def __init__(
        self,
        coordinator: ZKHDataUpdateCoordinator
    ) -> None:
        """Initialize the tariff sensor."""
        super().__init__(coordinator, "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ")

    @property
    def _cold_water(self) -> dict[str, Any] | None:
        return self._get_first_available_tariff(COLD_WATER_TARIFFS, VOLUME_UNITS)

    @property
    def native_value(self) -> float | None:
        """Return the combined cold water tariff value."""
        waste_water = self._waste_water
        cold_water = self._cold_water

        return waste_water.get("rate_per_unit") + cold_water.get("rate_per_unit") \
            if waste_water is not None and cold_water is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return  the combined cold water tariff attributes."""
        waste_water = self._waste_water
        cold_water = self._cold_water
        if waste_water is None or cold_water is None:
            return {}

        dates = [waste_water.get("from_date"), cold_water.get("from_date")]

        return {
            "names": [waste_water.get("name"), cold_water.get("name")],
            "formula": f"{waste_water.get('name')} + {cold_water.get('name')}",
            "from_date": max((date for date in dates if date is not None), default=None),
        }

class ZKHCombinedHotWaterTariffSensor(ZKHCombinedTariffSensor):
    """Representation of a ZKH combined hot water tariff sensor."""
    def __init__(
        self,
        coordinator: ZKHDataUpdateCoordinator,
        water_heating_rate: float | None,
    ) -> None:
        """Initialize the tariff sensor."""
        super().__init__(coordinator, "ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ")
        self._water_heating_rate = water_heating_rate

    @property
    def _hot_water(self) -> dict[str, Any] | None:
        return self._get_first_available_tariff(HOT_WATER_TARIFFS, VOLUME_UNITS)

    @property
    def _hot_water_heating(self) -> dict[str, Any] | None:
        return self._get_first_available_tariff(HOT_WATER_HEATING_TARIFFS, HEAT_UNITS)

    @property
    def _hot_water_fluid(self) -> dict[str, Any] | None:
        return self._get_first_available_tariff(HOT_WATER_FLUID_TARIFFS, VOLUME_UNITS)

    @property
    def native_value(self) -> float | None:
        """Return the combined hot water tariff value."""
        waste_water = self._waste_water
        if waste_water is None:
            return None

        hot_water = self._hot_water
        if hot_water is not None:
            return waste_water.get("rate_per_unit") + hot_water.get("rate_per_unit")

        if self._water_heating_rate is None:
            _LOGGER.warning("Water heating rate not set for %s", self.coordinator.entry.entry_id)
            return None

        hot_water_heating = self._hot_water_heating
        hot_water_fluid = self._hot_water_fluid
        if hot_water_heating is not None and hot_water_fluid is not None:
            return round(waste_water.get("rate_per_unit") + hot_water_fluid.get("rate_per_unit")
                         + self._water_heating_rate * hot_water_heating.get("rate_per_unit"), 2)

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return  the combined hot water tariff attributes."""
        waste_water = self._waste_water
        if waste_water is None:
            return {}

        dates = [waste_water.get("from_date")]
        hot_water = self._hot_water
        if hot_water is not None:
            dates.append(hot_water.get("from_date"))
            return {
                "names": [waste_water.get("name"), hot_water.get("name")],
                "formula": f"{waste_water.get('name')} + {hot_water.get('name')}",
                "from_date": max((date for date in dates if date is not None), default=None),
            }

        if self._water_heating_rate is None:
            return {}

        hot_water_heating = self._hot_water_heating
        hot_water_fluid = self._hot_water_fluid
        if hot_water_heating is not None and hot_water_fluid is not None:
            dates.append(hot_water_fluid.get("from_date"))
            dates.append(hot_water_heating.get("from_date"))
            return {
                "names": [waste_water.get("name"), hot_water_fluid.get("name"), hot_water_heating.get("name")],
                "formula": f"{waste_water.get('name')} + {hot_water_fluid.get('name')} + {hot_water_heating.get('name')} × {self._water_heating_rate}",
                "from_date": max((date for date in dates if date is not None), default=None),
            }

        return {}
