"""Sensor platform for ZKHNSO integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfArea, UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo, async_get as async_get_device_registry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ACCOUNT_OPTIONS, CONF_COLD_WATER_TARIFF, CONF_HOT_WATER_TARIFF,
    CONF_HOT_WATER_HEATING_TARIFF, CONF_WASTE_WATER_TARIFF, CONF_WATER_HEATING_RATE,
    DOMAIN, HOT_WATER_TARIFFS, HOT_WATER_FLUID_TARIFFS,
)
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
    coordinator: ZKHDataUpdateCoordinator = entry.runtime_data

    known: set[tuple[str, str, str]] = set()

    device_registry = async_get_device_registry(hass)

    def add_new_sensors() -> None:
        accounts = coordinator.data.get("accounts", {}) if isinstance(coordinator.data, dict) else {}
        entities = []
        for account_id, account in accounts.items():
            device = device_registry.async_get_device(
                identifiers={(DOMAIN, f"{entry.entry_id}_account_{account_id}")}
            )
            company = account.get("company_name")
            if device is not None and company is not None and device.manufacturer != company:
                device_registry.async_update_device(device.id, manufacturer=company)

            for kind, cls in (("meters", ZKHMeterSensor), ("tariffs", ZKHTariffSensor)):
                items = account.get(kind)
                if not isinstance(items, dict):
                    continue
                for key, value in items.items():
                    identity = (account_id, kind, key)
                    if identity not in known and isinstance(value, dict):
                        entities.append(cls(coordinator, key, value, account_id))
                        known.add(identity)

            if account.get("tariffs") and (account_id, "combined", "water") not in known:
                entities.append(ZKHCombinedColdWaterTariffSensor(coordinator, account_id))
                account_options = entry.options.get(CONF_ACCOUNT_OPTIONS, {}).get(account_id, {})
                entities.append(ZKHCombinedHotWaterTariffSensor(coordinator, account_options.get(CONF_WATER_HEATING_RATE), account_id))
                known.add((account_id, "combined", "water"))

            if (account_id, "company", "") not in known:
                entities.append(ZKHCompanySensor(coordinator, account_id))
                known.add((account_id, "company", ""))
        if entities:
            async_add_entities(entities, update_before_add=False)

    add_new_sensors()
    if hasattr(coordinator, "async_add_listener"):
        entry.async_on_unload(coordinator.async_add_listener(add_new_sensors))


class ZKHSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(
            self,
            coordinator: ZKHDataUpdateCoordinator,
            account_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._account_id = account_id

    @property
    def _account(self) -> dict | None:
        if not self.coordinator.data:
            return None

        return self.coordinator.data.get("accounts", {}).get(self._account_id, {}) or None

    @property
    def _id_prefix(self) -> str:
        return f"{self.coordinator.entry.entry_id}_account_{self._account_id}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._id_prefix)},
            name=f"№{self._account_id} {(self._account or {}).get('address', '')}".strip(),
            manufacturer=(self._account or {}).get('company_name'),
            entry_type=DeviceEntryType.SERVICE,
        )


class ZKHCompanySensor(ZKHSensor):
    def __init__(
            self,
            coordinator: ZKHDataUpdateCoordinator,
            account_id: str,
    ) -> None:
        super().__init__(coordinator, account_id)
        self._attr_unique_id = f"{self._id_prefix}_company"
        self._attr_translation_key = "company"
        self._attr_icon = "mdi:briefcase"

    @property
    def native_value(self) -> str | None:
        return (self._account or {}).get("company_name")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        account = self._account
        if account is None:
            return {}

        return {
            "address": account.get("company_address"),
        }

class ZKHMeterSensor(ZKHSensor):
    def __init__(
        self,
        coordinator: ZKHDataUpdateCoordinator,
        key: str,
        meter: dict,
        account_id: str,
    ) -> None:
        super().__init__(coordinator, account_id)
        self.key = key
        self._attr_unique_id = f"{self._id_prefix}_meter_{key}"
        self._attr_translation_key = "meter"
        self._attr_translation_placeholders = {
            "name": str(meter.get("name", key)),
            "serial_number": str(meter.get("serial_number")),
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
    def _meter(self) -> dict | None:
        account = self._account
        return account.get("meters", {}).get(self.key) or None if isinstance(account, dict) else None

    @property
    def native_value(self) -> float | None:
        meter = self._meter
        return self._meter.get("value") if isinstance(meter, dict) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        meter = self._meter
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
    def __init__(
        self,
        coordinator: ZKHDataUpdateCoordinator,
        key: str,
        tariff: dict,
        account_id: str
    ) -> None:
        super().__init__(coordinator, account_id)
        self.key = key
        self._attr_unique_id = f"{self._id_prefix}_tariff_{key}"
        self._attr_translation_key = "tariff"
        self._attr_translation_placeholders = {
            "name": str(tariff.get("name", key)),
        }
        self._attr_native_unit_of_measurement = self._normalized_unit(tariff.get("unit"))
        self._attr_icon = "mdi:currency-rub"

    @property
    def _tariff(self) -> dict | None:
        account = self._account
        return account.get("tariffs", {}).get(self.key) or None if isinstance(account, dict) else None

    @property
    def native_value(self) -> float | None:
        tariff = self._tariff
        return tariff.get("rate_per_unit") if isinstance(tariff, dict) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tariff = self._tariff
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
    def __init__(
        self,
        coordinator: ZKHDataUpdateCoordinator,
        name: str,
        account_id: str
    ) -> None:
        super().__init__(coordinator, account_id)
        self.name = str(name)
        self._attr_unique_id = f"{self._id_prefix}_combined_tariff_{name}"
        self._attr_translation_key = "combined_tariff"
        self._attr_translation_placeholders = {
            "name": self.name,
        }
        self._attr_native_unit_of_measurement = f"RUB/{UnitOfVolume.CUBIC_METERS}"
        self._attr_icon = "mdi:sigma"

    @property
    def _tariff_options(self) -> dict[str, Any]:
        return self.coordinator.entry.options.get(CONF_ACCOUNT_OPTIONS, {}).get(self._account_id, {})

    def _selected_tariff(self, key: str, units: set[str]) -> dict[str, Any] | None:
        name = self._tariff_options.get(key)
        return self._get_first_available_tariff([name], units) if name else None

    @property
    def _waste_water(self) -> dict[str, Any] | None:
        return self._selected_tariff(CONF_WASTE_WATER_TARIFF, VOLUME_UNITS)

    def _get_first_available_tariff(self, names: list[str], units: set[str]) -> dict[str, Any] | None:
        tariffs = self._account.get("tariffs") if isinstance(self._account, dict) else None
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
    def __init__(
        self,
        coordinator: ZKHDataUpdateCoordinator,
        account_id: str,
    ) -> None:
        super().__init__(coordinator, "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", account_id)

    @property
    def _cold_water(self) -> dict[str, Any] | None:
        return self._selected_tariff(CONF_COLD_WATER_TARIFF, VOLUME_UNITS)

    @property
    def native_value(self) -> float | None:
        waste_water = self._waste_water
        cold_water = self._cold_water

        return waste_water.get("rate_per_unit") + cold_water.get("rate_per_unit") \
            if waste_water is not None and cold_water is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        waste_water = self._waste_water
        cold_water = self._cold_water
        if waste_water is None or cold_water is None:
            return {}

        dates = [waste_water.get("from_date"), cold_water.get("from_date")]

        return {
            "name": self.name,
            "names": [waste_water.get("name"), cold_water.get("name")],
            "formula": f"{waste_water.get('name')} + {cold_water.get('name')}",
            "from_date": max((date for date in dates if date is not None), default=None),
        }

class ZKHCombinedHotWaterTariffSensor(ZKHCombinedTariffSensor):
    def __init__(
        self,
        coordinator: ZKHDataUpdateCoordinator,
        water_heating_rate: float | None,
        account_id: str,
    ) -> None:
        super().__init__(coordinator, "ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ", account_id)
        self._water_heating_rate = water_heating_rate

    @property
    def _hot_water(self) -> dict[str, Any] | None:
        if self._tariff_options.get(CONF_HOT_WATER_TARIFF) not in HOT_WATER_TARIFFS:
            return None
        return self._selected_tariff(CONF_HOT_WATER_TARIFF, VOLUME_UNITS)

    @property
    def _hot_water_heating(self) -> dict[str, Any] | None:
        return self._selected_tariff(CONF_HOT_WATER_HEATING_TARIFF, HEAT_UNITS)

    @property
    def _hot_water_fluid(self) -> dict[str, Any] | None:
        if self._tariff_options.get(CONF_HOT_WATER_TARIFF) not in HOT_WATER_FLUID_TARIFFS:
            return None
        return self._selected_tariff(CONF_HOT_WATER_TARIFF, VOLUME_UNITS)

    @property
    def native_value(self) -> float | None:
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
        waste_water = self._waste_water
        if waste_water is None:
            return {}

        dates = [waste_water.get("from_date")]
        hot_water = self._hot_water
        if hot_water is not None:
            dates.append(hot_water.get("from_date"))
            return {
                "name": self.name,
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
                "name": self.name,
                "names": [waste_water.get("name"), hot_water_fluid.get("name"), hot_water_heating.get("name")],
                "formula": f"{waste_water.get('name')} + {hot_water_fluid.get('name')} + {hot_water_heating.get('name')} × {self._water_heating_rate}",
                "from_date": max((date for date in dates if date is not None), default=None),
            }

        return {}
