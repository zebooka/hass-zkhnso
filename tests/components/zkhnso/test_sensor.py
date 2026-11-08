"""Tests for ZKHNSO sensor entities."""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfEnergy, UnitOfVolume

import pytest

from custom_components.zkhnso.sensor import (
    ZKHCombinedTariffSensor,
    ZKHCombinedColdWaterTariffSensor,
    ZKHCombinedHotWaterTariffSensor,
    ZKHMeterSensor,
    ZKHTariffSensor,
    async_setup_entry,
)


def _coordinator(data: dict) -> SimpleNamespace:
    """Create the coordinator shape used by entity unit tests."""
    return SimpleNamespace(
        data=data,
        entry=SimpleNamespace(entry_id="entry", data={"account_id": 42, "username": "user"}),
    )


def test_meter_uses_current_coordinator_data_and_clears_removed_meter() -> None:
    """Meters do not retain stale readings after a successful refresh."""
    data = {"meters": {"1": {"name": "Вода", "unit": "куб.м.", "value": 12.5}}}
    coordinator = _coordinator(data)
    sensor = ZKHMeterSensor(coordinator, "1", data["meters"]["1"])

    assert sensor.native_value == 12.5
    assert sensor.device_class == SensorDeviceClass.WATER
    assert sensor.native_unit_of_measurement == UnitOfVolume.CUBIC_METERS

    coordinator.data = {"meters": {}}
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_meter_device_classes() -> None:
    """Gas, electricity, heat, and generic volumes use correct device classes."""
    cases = [
        ("Газ", "куб.м.", SensorDeviceClass.GAS, UnitOfVolume.CUBIC_METERS),
        ("Электричество", "кВтч", SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
        ("Тепло", "Гкал", SensorDeviceClass.ENERGY, UnitOfEnergy.GIGA_CALORIE),
    ]
    for index, (name, unit, device_class, native_unit) in enumerate(cases):
        meter = {"name": name, "unit": unit, "value": 1}
        sensor = ZKHMeterSensor(_coordinator({"meters": {str(index): meter}}), str(index), meter)
        assert sensor.device_class == device_class
        assert sensor.native_unit_of_measurement == native_unit


def test_tariff_uses_current_data_and_clears_removed_tariff() -> None:
    """Tariffs use `rate_per_unit` and clear stale state when removed."""
    tariff = {"name": "Water", "unit": "куб.м.", "rate_per_unit": 50.25, "norm": 3, "from_date": "2026-01-01"}
    coordinator = _coordinator({"tariffs": {"Water": tariff}})
    sensor = ZKHTariffSensor(coordinator, "Water", tariff)

    assert sensor.native_value == 50.25
    assert sensor.device_class is None
    assert sensor.native_unit_of_measurement == "RUB/m³"

    coordinator.data = {"tariffs": {}}
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_tariff_price_units_match_resource_units() -> None:
    """Tariffs expose Energy Dashboard-compatible prices per resource unit."""
    cases = [
        ("куб.м.", "RUB/m³"),
        ("кВтч", "RUB/kWh"),
        ("Гкал", "RUB/Gcal"),
        ("кв.м.", "RUB/m²"),
    ]
    for index, (unit, expected_unit) in enumerate(cases):
        tariff = {"name": f"Tariff {index}", "unit": unit, "rate_per_unit": 1}
        sensor = ZKHTariffSensor(
            _coordinator({"tariffs": {str(index): tariff}}),
            str(index),
            tariff,
        )
        assert sensor.device_class is None
        assert sensor.native_unit_of_measurement == expected_unit


@pytest.mark.parametrize("sensor_type", [ZKHCombinedColdWaterTariffSensor, ZKHCombinedHotWaterTariffSensor])
def test_combined_tariff_search_uses_name_and_priority(sensor_type) -> None:
    """Select the first available name, not the first dictionary entry or key."""
    first = {"name": "ОТВЕД. СТОЧ. ВОД СОИ", "rate_per_unit": 12.5, "unit": "куб.м."}
    second = {"name": "ВОДООТВЕДЕНИЕ", "rate_per_unit": 8.0, "unit": "куб.м."}
    coordinator = _coordinator({"tariffs": {"ОТВЕД. СТОЧ. ВОД СОИ": second, "other": first}})
    sensor = sensor_type(coordinator)
    names = ["ОТВЕД. СТОЧ. ВОД СОИ", "ВОДООТВЕДЕНИЕ"]

    assert sensor._get_first_available_tariff(names) is first
    assert sensor._get_first_available_tariff(list(reversed(names))) is second
    assert sensor._get_first_available_tariff(["absent"]) is None
    assert sensor._get_first_available_tariff([]) is None

    first["rate_per_unit"] = None
    assert sensor._get_first_available_tariff([names[0]]) is None
    assert sensor._get_first_available_tariff(names) is second
    second["rate_per_unit"] = 0
    assert sensor._get_first_available_tariff(names) is second

    coordinator.data = {"tariffs": {"new": {"name": names[0], "rate_per_unit": 15, "unit": "куб.м."}}}
    assert sensor._get_first_available_tariff(names) == coordinator.data["tariffs"]["new"]
    assert sensor._get_first_available_tariff([names[1]]) is None


@pytest.mark.parametrize("data", [None, {}, {"tariffs": None}, {"tariffs": []}, {"tariffs": {"x": None, "y": {"name": "X"}}}])
def test_combined_tariff_search_handles_missing_data(data) -> None:
    """Missing or incomplete data does not make a tariff available."""
    sensor = ZKHCombinedColdWaterTariffSensor(_coordinator(data))
    assert sensor._get_first_available_tariff(["X"]) is None


def test_combined_tariff_search_skips_unavailable_duplicate() -> None:
    """A later tariff with the same name can supply the missing value."""
    available = {"name": "X", "rate_per_unit": 0, "unit": "куб.м."}
    sensor = ZKHCombinedColdWaterTariffSensor(_coordinator({
        "tariffs": {"first": {"name": "X", "rate_per_unit": None}, "second": available}
    }))
    assert sensor._get_first_available_tariff(["X"]) is available


def test_combined_tariff_sensor_properties() -> None:
    sensor = ZKHCombinedTariffSensor(_coordinator({}), "Test")
    assert sensor.unique_id == "entry_combined_tariff_Test"
    assert sensor.translation_key == "combined_tariff"
    assert sensor.translation_placeholders == {"name": "Test", "account_id": "42"}
    assert sensor.native_unit_of_measurement == "RUB/m³"
    assert sensor.icon == "mdi:sigma"


@pytest.mark.parametrize("unit", ["куб.м.", "куб.м", "м³", "m³"])
def test_combined_tariff_search_accepts_volume_units(unit) -> None:
    tariff = {"name": "X", "rate_per_unit": 10, "unit": unit}
    sensor = ZKHCombinedTariffSensor(_coordinator({"tariffs": {"key": tariff}}), "Test")
    assert sensor._get_first_available_tariff(["X"]) is tariff


@pytest.mark.parametrize("unit", [None, "Гкал", "кВтч", "кв.м."])
def test_combined_tariff_search_rejects_non_volume_units(unit) -> None:
    tariff = {"name": "X", "rate_per_unit": 10, "unit": unit}
    sensor = ZKHCombinedTariffSensor(_coordinator({"tariffs": {"key": tariff}}), "Test")
    assert sensor._get_first_available_tariff(["X"]) is None


def _tariff(name, rate, date=None, unit="куб.м."):
    return {"name": name, "rate_per_unit": rate, "unit": unit, "from_date": date}


@pytest.mark.parametrize("waste_name", ["ВОДООТВЕДЕНИЕ", "ОТВЕД. СТОЧ. ВОД СОИ"])
@pytest.mark.parametrize("cold_name", ["ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", "ХВ НА СОД-Е ОБЩ.ИМУЩ."])
def test_combined_cold_water_calculates_rate_and_attributes(waste_name, cold_name) -> None:
    waste = _tariff(waste_name, 12.5, "2026-02-01")
    cold = _tariff(cold_name, 30.25, "2026-03-01")
    sensor = ZKHCombinedColdWaterTariffSensor(_coordinator({"tariffs": {"waste": waste, "cold": cold}}))
    assert sensor.native_value == 42.75
    assert sensor.extra_state_attributes == {"names": [waste_name, cold_name], "from_date": "2026-03-01"}


@pytest.mark.parametrize("dates,expected", [
    (("2026-03-01", "2026-02-01"), "2026-03-01"),
    ((None, "2026-02-01"), "2026-02-01"),
    (("2026-03-01", None), "2026-03-01"),
    ((None, None), None),
])
def test_combined_cold_water_latest_date_and_zero_rate(dates, expected) -> None:
    sensor = ZKHCombinedColdWaterTariffSensor(_coordinator({"tariffs": {
        "waste": _tariff("ВОДООТВЕДЕНИЕ", 0, dates[0]),
        "cold": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10, dates[1]),
    }}))
    assert sensor.native_value == 10
    assert sensor.extra_state_attributes["from_date"] == expected


@pytest.mark.parametrize("tariffs", [
    {},
    {"cold": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10)},
    {"waste": _tariff("ВОДООТВЕДЕНИЕ", 5)},
    {"waste": _tariff("ВОДООТВЕДЕНИЕ", None), "cold": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10)},
    {"waste": _tariff("ВОДООТВЕДЕНИЕ", 5), "cold": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10, unit="Гкал")},
])
def test_combined_cold_water_requires_both_available_tariffs(tariffs) -> None:
    sensor = ZKHCombinedColdWaterTariffSensor(_coordinator({"tariffs": tariffs}))
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_combined_cold_water_priority_and_coordinator_updates() -> None:
    coordinator = _coordinator({"tariffs": {
        "waste_backup": _tariff("ОТВЕД. СТОЧ. ВОД СОИ", 6),
        "waste_primary": _tariff("ВОДООТВЕДЕНИЕ", 5),
        "cold_backup": _tariff("ХВ НА СОД-Е ОБЩ.ИМУЩ.", 11),
        "cold_primary": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10),
    }})
    sensor = ZKHCombinedColdWaterTariffSensor(coordinator)
    assert sensor.native_value == 15
    assert sensor.extra_state_attributes["names"] == ["ВОДООТВЕДЕНИЕ", "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ"]
    coordinator.data["tariffs"]["waste_primary"]["rate_per_unit"] = None
    coordinator.data["tariffs"]["cold_primary"]["rate_per_unit"] = None
    assert sensor.native_value == 17
    assert sensor.extra_state_attributes["names"] == ["ОТВЕД. СТОЧ. ВОД СОИ", "ХВ НА СОД-Е ОБЩ.ИМУЩ."]
    coordinator.data = {"tariffs": {}}
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_setup_adds_combined_cold_water_tariff_sensor() -> None:
    coordinator = _coordinator({"tariffs": {"water": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10)}})
    entry = SimpleNamespace(runtime_data=coordinator, options={})
    add_entities = MagicMock()
    asyncio.run(async_setup_entry(None, entry, add_entities))
    add_entities.assert_called_once()
    entities = add_entities.call_args.args[0]
    assert sum(isinstance(sensor, ZKHTariffSensor) for sensor in entities) == 1
    assert sum(isinstance(sensor, ZKHCombinedColdWaterTariffSensor) for sensor in entities) == 1
