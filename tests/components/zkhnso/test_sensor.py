"""Tests for ZKHNSO sensor entities."""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfEnergy, UnitOfVolume

import pytest

from custom_components.zkhnso.sensor import (
    HEAT_UNITS,
    VOLUME_UNITS,
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
    sensor = sensor_type(coordinator, 0.05) if sensor_type is ZKHCombinedHotWaterTariffSensor else sensor_type(coordinator)
    names = ["ОТВЕД. СТОЧ. ВОД СОИ", "ВОДООТВЕДЕНИЕ"]

    assert sensor._get_first_available_tariff(names, VOLUME_UNITS) is first
    assert sensor._get_first_available_tariff(list(reversed(names)), VOLUME_UNITS) is second
    assert sensor._get_first_available_tariff(["absent"], VOLUME_UNITS) is None
    assert sensor._get_first_available_tariff([], VOLUME_UNITS) is None

    first["rate_per_unit"] = None
    assert sensor._get_first_available_tariff([names[0]], VOLUME_UNITS) is None
    assert sensor._get_first_available_tariff(names, VOLUME_UNITS) is second
    second["rate_per_unit"] = 0
    assert sensor._get_first_available_tariff(names, VOLUME_UNITS) is second

    coordinator.data = {"tariffs": {"new": {"name": names[0], "rate_per_unit": 15, "unit": "куб.м."}}}
    assert sensor._get_first_available_tariff(names, VOLUME_UNITS) == coordinator.data["tariffs"]["new"]
    assert sensor._get_first_available_tariff([names[1]], VOLUME_UNITS) is None


@pytest.mark.parametrize("data", [None, {}, {"tariffs": None}, {"tariffs": []}, {"tariffs": {"x": None, "y": {"name": "X"}}}])
def test_combined_tariff_search_handles_missing_data(data) -> None:
    """Missing or incomplete data does not make a tariff available."""
    sensor = ZKHCombinedColdWaterTariffSensor(_coordinator(data))
    assert sensor._get_first_available_tariff(["X"], VOLUME_UNITS) is None


def test_combined_tariff_search_skips_unavailable_duplicate() -> None:
    """A later tariff with the same name can supply the missing value."""
    available = {"name": "X", "rate_per_unit": 0, "unit": "куб.м."}
    sensor = ZKHCombinedColdWaterTariffSensor(_coordinator({
        "tariffs": {"first": {"name": "X", "rate_per_unit": None}, "second": available}
    }))
    assert sensor._get_first_available_tariff(["X"], VOLUME_UNITS) is available


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
    assert sensor._get_first_available_tariff(["X"], VOLUME_UNITS) is tariff


@pytest.mark.parametrize("unit", [None, "Гкал", "кВтч", "кв.м."])
def test_combined_tariff_search_rejects_non_volume_units(unit) -> None:
    tariff = {"name": "X", "rate_per_unit": 10, "unit": unit}
    sensor = ZKHCombinedTariffSensor(_coordinator({"tariffs": {"key": tariff}}), "Test")
    assert sensor._get_first_available_tariff(["X"], VOLUME_UNITS) is None


@pytest.mark.parametrize("unit", ["Гкал", "Gcal"])
def test_combined_tariff_search_accepts_heat_units(unit) -> None:
    tariff = {"name": "X", "rate_per_unit": 10, "unit": unit}
    sensor = ZKHCombinedTariffSensor(_coordinator({"tariffs": {"key": tariff}}), "Test")
    assert sensor._get_first_available_tariff(["X"], HEAT_UNITS) is tariff
    assert sensor._get_first_available_tariff(["X"], VOLUME_UNITS) is None


def _tariff(name, rate, date=None, unit="куб.м."):
    return {"name": name, "rate_per_unit": rate, "unit": unit, "from_date": date}


@pytest.mark.parametrize("waste_name", ["ВОДООТВЕДЕНИЕ", "ОТВЕД. СТОЧ. ВОД СОИ"])
@pytest.mark.parametrize("cold_name", ["ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", "ХВ НА СОД-Е ОБЩ.ИМУЩ."])
def test_combined_cold_water_calculates_rate_and_attributes(waste_name, cold_name) -> None:
    waste = _tariff(waste_name, 12.5, "2026-02-01")
    cold = _tariff(cold_name, 30.25, "2026-03-01")
    sensor = ZKHCombinedColdWaterTariffSensor(_coordinator({"tariffs": {"waste": waste, "cold": cold}}))
    assert sensor.native_value == 42.75
    assert sensor.extra_state_attributes == {
        "names": [waste_name, cold_name],
        "formula": f"{waste_name} + {cold_name}",
        "from_date": "2026-03-01",
    }


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


def test_setup_adds_combined_water_tariff_sensors() -> None:
    coordinator = _coordinator({"tariffs": {"water": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10)}})
    entry = SimpleNamespace(runtime_data=coordinator, options={})
    add_entities = MagicMock()
    asyncio.run(async_setup_entry(None, entry, add_entities))
    add_entities.assert_called_once()
    entities = add_entities.call_args.args[0]
    assert sum(isinstance(sensor, ZKHTariffSensor) for sensor in entities) == 1
    assert sum(isinstance(sensor, ZKHCombinedColdWaterTariffSensor) for sensor in entities) == 1
    assert sum(isinstance(sensor, ZKHCombinedHotWaterTariffSensor) for sensor in entities) == 1


@pytest.mark.parametrize("hot_name", ["ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ", "ГВ НА СОД-Е ОБЩ.ИМУЩ."])
def test_combined_hot_water_direct_tariff(hot_name) -> None:
    """A direct hot-water tariff takes precedence over the heating calculation."""
    coordinator = _coordinator({"tariffs": {
        "waste": _tariff("ВОДООТВЕДЕНИЕ", 12.5, "2026-02-01"),
        "hot": _tariff(hot_name, 50, "2026-03-01"),
        "fluid": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 20),
        "heating": _tariff("ОТОПЛЕНИЕ", 1000, unit="Гкал"),
    }})
    sensor = ZKHCombinedHotWaterTariffSensor(coordinator, None)
    assert sensor.native_value == 62.5
    assert sensor.extra_state_attributes == {
        "names": ["ВОДООТВЕДЕНИЕ", hot_name],
        "formula": f"ВОДООТВЕДЕНИЕ + {hot_name}",
        "from_date": "2026-03-01",
    }


@pytest.mark.parametrize("heating_name", ["ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ (ПОДОГРЕВ)", "ГВ НА СОИ (ПОДОГРЕВ)", "ОТОПЛЕНИЕ"])
@pytest.mark.parametrize("fluid_name", ["ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ (ТЕПЛОНОСИТ)", "ГВ НА СОИ (ТЕПЛОНОСИТ)", "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", "ХВ НА СОД-Е ОБЩ.ИМУЩ."])
@pytest.mark.parametrize("heat_unit", ["Гкал", "Gcal"])
def test_combined_hot_water_heating_calculation(heating_name, fluid_name, heat_unit) -> None:
    """Calculate a per-volume tariff from heat, fluid and waste-water rates."""
    coordinator = _coordinator({"tariffs": {
        "waste": _tariff("ОТВЕД. СТОЧ. ВОД СОИ", 12.5, "2026-01-01"),
        "fluid": _tariff(fluid_name, 30.25, "2026-02-01"),
        "heating": _tariff(heating_name, 1234.56, "2026-03-01", heat_unit),
    }})
    sensor = ZKHCombinedHotWaterTariffSensor(coordinator, 0.06528)
    assert sensor.native_value == 123.34
    assert sensor.extra_state_attributes == {
        "names": ["ОТВЕД. СТОЧ. ВОД СОИ", fluid_name, heating_name],
        "formula": f"ОТВЕД. СТОЧ. ВОД СОИ + {fluid_name} + {heating_name} × 0.06528",
        "from_date": "2026-03-01",
    }


@pytest.mark.parametrize("dates,expected", [
    (("2026-04-01", "2026-02-01", "2026-03-01"), "2026-04-01"),
    ((None, "2026-02-01", None), "2026-02-01"),
    ((None, None, None), None),
])
def test_combined_hot_water_zero_rates_and_dates(dates, expected) -> None:
    coordinator = _coordinator({"tariffs": {
        "waste": _tariff("ВОДООТВЕДЕНИЕ", 0, dates[0]),
        "fluid": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 0, dates[1]),
        "heating": _tariff("ОТОПЛЕНИЕ", 0, dates[2], "Гкал"),
    }})
    sensor = ZKHCombinedHotWaterTariffSensor(coordinator, 0)
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes["from_date"] == expected


def test_combined_hot_water_without_heating_rate() -> None:
    """The component calculation needs a configured heating rate, but accepts zero."""
    coordinator = _coordinator({"tariffs": {
        "waste": _tariff("ВОДООТВЕДЕНИЕ", 5, "2026-01-01"),
        "fluid": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10, "2026-02-01"),
        "heating": _tariff("ОТОПЛЕНИЕ", 1000, "2026-03-01", "Гкал"),
    }})
    sensor = ZKHCombinedHotWaterTariffSensor(coordinator, None)
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}

    zero_rate_sensor = ZKHCombinedHotWaterTariffSensor(coordinator, 0)
    assert zero_rate_sensor.native_value == 15
    assert zero_rate_sensor.extra_state_attributes == {
        "names": ["ВОДООТВЕДЕНИЕ", "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", "ОТОПЛЕНИЕ"],
        "formula": "ВОДООТВЕДЕНИЕ + ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ + ОТОПЛЕНИЕ × 0",
        "from_date": "2026-03-01",
    }


@pytest.mark.parametrize("options", [{}, {"water_heating_rate": None}])
def test_setup_combined_hot_water_without_heating_rate(options) -> None:
    """Missing and explicitly unset options both yield an unknown value."""
    coordinator = _coordinator({"tariffs": {
        "waste": _tariff("ВОДООТВЕДЕНИЕ", 5),
        "fluid": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10),
        "heating": _tariff("ОТОПЛЕНИЕ", 1000, unit="Гкал"),
    }})
    entry = SimpleNamespace(runtime_data=coordinator, options=options)
    add_entities = MagicMock()
    asyncio.run(async_setup_entry(None, entry, add_entities))
    sensor = next(entity for entity in add_entities.call_args.args[0]
                  if isinstance(entity, ZKHCombinedHotWaterTariffSensor))
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


@pytest.mark.parametrize("missing", ["waste", "fluid", "heating"])
def test_combined_hot_water_requires_all_components(missing) -> None:
    tariffs = {
        "waste": _tariff("ВОДООТВЕДЕНИЕ", 5),
        "fluid": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10),
        "heating": _tariff("ОТОПЛЕНИЕ", 1000, unit="Гкал"),
    }
    del tariffs[missing]
    sensor = ZKHCombinedHotWaterTariffSensor(_coordinator({"tariffs": tariffs}), 0.05)
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


@pytest.mark.parametrize("component,unit", [("waste", "Гкал"), ("fluid", "Гкал"), ("heating", "куб.м.")])
def test_combined_hot_water_rejects_wrong_component_units(component, unit) -> None:
    tariffs = {
        "waste": _tariff("ВОДООТВЕДЕНИЕ", 5),
        "fluid": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10),
        "heating": _tariff("ОТОПЛЕНИЕ", 1000, unit="Гкал"),
    }
    tariffs[component]["unit"] = unit
    sensor = ZKHCombinedHotWaterTariffSensor(_coordinator({"tariffs": tariffs}), 0.05)
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_combined_hot_water_priority_and_coordinator_updates() -> None:
    coordinator = _coordinator({"tariffs": {
        "waste": _tariff("ВОДООТВЕДЕНИЕ", 5),
        "fluid_backup": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10),
        "fluid_primary": _tariff("ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ (ТЕПЛОНОСИТ)", 20),
        "heat_backup": _tariff("ОТОПЛЕНИЕ", 2000, unit="Гкал"),
        "heat_primary": _tariff("ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ (ПОДОГРЕВ)", 1000, unit="Гкал"),
    }})
    sensor = ZKHCombinedHotWaterTariffSensor(coordinator, 0.05)
    assert sensor.native_value == 75
    assert sensor.extra_state_attributes["names"] == [
        "ВОДООТВЕДЕНИЕ", "ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ (ТЕПЛОНОСИТ)", "ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ (ПОДОГРЕВ)"
    ]
    coordinator.data["tariffs"]["fluid_primary"]["rate_per_unit"] = None
    coordinator.data["tariffs"]["heat_primary"]["rate_per_unit"] = None
    assert sensor.native_value == 115
    assert sensor.extra_state_attributes["names"] == ["ВОДООТВЕДЕНИЕ", "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", "ОТОПЛЕНИЕ"]
    coordinator.data = {"tariffs": {}}
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_setup_passes_heating_rate_to_combined_hot_water_sensor() -> None:
    coordinator = _coordinator({"tariffs": {
        "waste": _tariff("ВОДООТВЕДЕНИЕ", 5),
        "fluid": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10),
        "heating": _tariff("ОТОПЛЕНИЕ", 1000, unit="Гкал"),
    }})
    entry = SimpleNamespace(runtime_data=coordinator, options={"water_heating_rate": 0.05})
    add_entities = MagicMock()
    asyncio.run(async_setup_entry(None, entry, add_entities))
    entities = add_entities.call_args.args[0]
    sensor = next(entity for entity in entities if isinstance(entity, ZKHCombinedHotWaterTariffSensor))
    assert sensor.native_value == 65
