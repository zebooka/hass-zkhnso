"""Tests for ZKHNSO sensor entities."""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfVolume
from homeassistant.helpers.device_registry import DeviceEntryType
from custom_components.zkhnso.const import (
    WASTE_WATER_TARIFFS, COLD_WATER_TARIFFS, HOT_WATER_TARIFFS,
    HOT_WATER_HEATING_TARIFFS, HOT_WATER_FLUID_TARIFFS,
)

import pytest

from custom_components.zkhnso.sensor import (
    HEAT_UNITS,
    VOLUME_UNITS,
    ZKHCombinedTariffSensor,
    ZKHCombinedTariffFormulaSensor,
    ZKHCombinedColdWaterTariffSensor,
    ZKHCombinedHotWaterTariffSensor,
    ZKHCompanySensor,
    ZKHMeterSensor,
    ZKHTariffSensor,
    async_setup_entry,
)


def _coordinator(data: dict) -> SimpleNamespace:
    """Create the coordinator shape used by entity unit tests."""
    if data is not None and "accounts" not in data:
        data = {"accounts": {"42": {"address": "Новосибирск, ул. Ленина, 1", **data}}}
    accounts = (data or {}).get("accounts", {})
    def selected(account):
        tariffs = account.get("tariffs") or {}
        names = {tariff.get("name") for tariff in tariffs.values() if isinstance(tariff, dict)} if isinstance(tariffs, dict) else set()
        def choose(candidates):
            return next((name for name in candidates if name in names), candidates[0])
        return {
            "waste_water_tariff": choose(WASTE_WATER_TARIFFS),
            "cold_water_tariff": choose(COLD_WATER_TARIFFS),
            "hot_water_tariff": choose(HOT_WATER_TARIFFS + HOT_WATER_FLUID_TARIFFS),
            "hot_water_heating_tariff": choose(HOT_WATER_HEATING_TARIFFS),
        }
    return SimpleNamespace(
        data=data,
        entry=SimpleNamespace(entry_id="entry", data={"username": "user"}, options={"account_options": {account_id: selected(account) for account_id, account in accounts.items()}}),
    )


@pytest.fixture
def device_registry(monkeypatch):
    registry = MagicMock()
    registry.async_get_device.return_value = None
    monkeypatch.setattr("custom_components.zkhnso.sensor.async_get_device_registry", lambda hass: registry)
    return registry


def test_entry_sensors_share_account_device(device_registry) -> None:
    """Meters and tariff sensors use the account-specific device and IDs."""
    meter = {"name": "Вода", "unit": "куб.м.", "value": 12.5}
    tariff = {"name": "Water", "unit": "куб.м.", "rate_per_unit": 10}
    coordinator = _coordinator({"meters": {"1": meter}, "tariffs": {"water": tariff}})
    entry = SimpleNamespace(entry_id="entry", runtime_data=coordinator, options={})
    add_entities = MagicMock()
    asyncio.run(async_setup_entry(None, entry, add_entities))

    entities = add_entities.call_args.args[0]
    assert len(entities) == 7
    assert {entity.unique_id for entity in entities} == {
        "entry_account_42_meter_1", "entry_account_42_tariff_water",
        "entry_account_42_combined_tariff_ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ",
        "entry_account_42_combined_tariff_ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ",
        "entry_account_42_combined_tariff_ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ_formula",
        "entry_account_42_combined_tariff_ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ_formula",
        "entry_account_42_company",
    }
    for entity in entities:
        assert entity.device_info == {
            "identifiers": {("zkhnso", "entry_account_42")},
            "name": "№42 Новосибирск, ул. Ленина, 1",
            "manufacturer": None,
            "entry_type": DeviceEntryType.SERVICE,
        }
        assert entity.has_entity_name is True


def test_different_entries_have_separate_devices() -> None:
    """Separate config entries never share a device, even for the same account."""
    first = _coordinator({})
    second = _coordinator({})
    second.entry.entry_id = "another-entry"
    first_sensor = ZKHCombinedColdWaterTariffSensor(first, "42")
    second_sensor = ZKHCombinedColdWaterTariffSensor(second, "42")
    assert first_sensor.device_info["identifiers"] == {("zkhnso", "entry_account_42")}
    assert second_sensor.device_info["identifiers"] == {("zkhnso", "another-entry_account_42")}
    assert first_sensor.unique_id != second_sensor.unique_id


def test_multiple_accounts_are_isolated_and_new_accounts_are_discovered(device_registry) -> None:
    def account(address, reading, rate):
        return {"address": address, "meters": {"1": {"name": "Вода", "unit": "куб.м.", "value": reading}},
                "tariffs": {"water": {"name": "Water", "unit": "куб.м.", "rate_per_unit": rate}}}

    coordinator = _coordinator({"accounts": {"42": account("Адрес 1", 10, 20), "43": account("Адрес 2", 30, 40)}})
    listener = []
    coordinator.async_add_listener = lambda callback: listener.append(callback) or (lambda: None)
    entry = SimpleNamespace(entry_id="entry", runtime_data=coordinator, options={}, async_on_unload=MagicMock())
    add_entities = MagicMock()
    asyncio.run(async_setup_entry(None, entry, add_entities))
    entities = add_entities.call_args.args[0]
    assert len(entities) == 14
    assert len({entity.unique_id for entity in entities}) == 14
    meters = {entity._account_id: entity for entity in entities if isinstance(entity, ZKHMeterSensor)}
    tariffs = {entity._account_id: entity for entity in entities if isinstance(entity, ZKHTariffSensor)}
    assert meters["42"].unique_id == "entry_account_42_meter_1"
    assert meters["43"].unique_id == "entry_account_43_meter_1"
    assert meters["42"].device_info == {"identifiers": {("zkhnso", "entry_account_42")}, "name": "№42 Адрес 1", "manufacturer": None, "entry_type": DeviceEntryType.SERVICE}
    assert meters["43"].device_info == {"identifiers": {("zkhnso", "entry_account_43")}, "name": "№43 Адрес 2", "manufacturer": None, "entry_type": DeviceEntryType.SERVICE}
    assert (meters["42"].native_value, meters["43"].native_value) == (10, 30)
    assert (tariffs["42"].native_value, tariffs["43"].native_value) == (20, 40)
    coordinator.data["accounts"]["43"] = account("Новый адрес", 31, 41)
    assert meters["43"].native_value == 31
    assert tariffs["43"].native_value == 41
    assert meters["43"].device_info["name"] == "№43 Новый адрес"
    coordinator.data["accounts"]["44"] = account("Адрес 3", 50, 60)
    listener[0]()
    assert len(add_entities.call_args.args[0]) == 7
    listener[0]()
    assert add_entities.call_count == 2
    del coordinator.data["accounts"]["43"]
    assert meters["43"].native_value is None
    company = next(entity for entity in entities if isinstance(entity, ZKHCompanySensor) and entity._account_id == "43")
    assert company.native_value is None


def test_company_sensor_and_device_manufacturer_update_without_duplicates(device_registry) -> None:
    coordinator = _coordinator({"company_name": "Первая УК"})
    listeners = []
    coordinator.async_add_listener = lambda callback: listeners.append(callback) or (lambda: None)
    device = SimpleNamespace(id="device-42", manufacturer="Первая УК")
    device_registry.async_get_device.return_value = device
    entry = SimpleNamespace(entry_id="entry", runtime_data=coordinator, options={}, async_on_unload=MagicMock())
    added = MagicMock()
    asyncio.run(async_setup_entry(None, entry, added))
    company = next(entity for entity in added.call_args.args[0] if isinstance(entity, ZKHCompanySensor))
    assert company.native_value == "Первая УК"
    device_registry.async_update_device.assert_not_called()

    coordinator.data["accounts"]["42"]["company_name"] = "Новая УК"
    listeners[0]()
    assert company.native_value == "Новая УК"
    device_registry.async_get_device.assert_called_with(identifiers={("zkhnso", "entry_account_42")})
    device_registry.async_update_device.assert_called_once_with("device-42", manufacturer="Новая УК")
    added.assert_called_once()
    coordinator.data = None
    assert company.native_value is None


def test_combined_tariffs_use_only_their_own_account() -> None:
    coordinator = _coordinator({"accounts": {
        "42": {"address": "Первый", "tariffs": {"w": _tariff("ВОДООТВЕДЕНИЕ", 5), "c": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10)}},
        "43": {"address": "Второй", "tariffs": {"w": _tariff("ВОДООТВЕДЕНИЕ", 20), "c": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 30)}},
    }})
    first = ZKHCombinedColdWaterTariffSensor(coordinator, "42")
    second = ZKHCombinedColdWaterTariffSensor(coordinator, "43")
    assert first.native_value == 15
    assert second.native_value == 50
    del coordinator.data["accounts"]["43"]
    assert second.native_value is None


@pytest.mark.parametrize("missing", ["waste_water_tariff", "cold_water_tariff", "hot_water_tariff", "hot_water_heating_tariff"])
def test_combined_tariffs_require_explicit_components(missing) -> None:
    coordinator = _coordinator({"tariffs": {
        "w": _tariff("ВОДООТВЕДЕНИЕ", 5),
        "c": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10),
        "h": _tariff("ОТОПЛЕНИЕ", 1000, unit="Гкал"),
    }})
    coordinator.entry.options["account_options"]["42"][missing] = ""
    cold = ZKHCombinedColdWaterTariffSensor(coordinator, "42")
    hot = ZKHCombinedHotWaterTariffSensor(coordinator, 0.05, "42")
    assert cold.native_value == (None if missing in ("waste_water_tariff", "cold_water_tariff") else 15)
    assert hot.native_value == (None if missing != "cold_water_tariff" else 65)


def test_no_tariff_settings_disable_automatic_calculation() -> None:
    coordinator = _coordinator({"tariffs": {
        "w": _tariff("ВОДООТВЕДЕНИЕ", 5), "c": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10),
        "h": _tariff("ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ", 50),
    }})
    coordinator.entry.options = {"account_options": {"42": {"water_heating_rate": 0.05}}}
    for sensor in (ZKHCombinedColdWaterTariffSensor(coordinator, "42"), ZKHCombinedHotWaterTariffSensor(coordinator, 0.05, "42")):
        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}


def test_selected_tariffs_override_other_available_choices() -> None:
    tariffs = {
        "w1": _tariff("ВОДООТВЕДЕНИЕ", 5),
        "w2": _tariff("ОТВЕД. СТОЧ. ВОД СОИ", 6),
        "c1": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10),
        "c2": _tariff("ХВ НА СОД-Е ОБЩ.ИМУЩ.", 11),
        "direct": _tariff("ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ", 50),
        "heat": _tariff("ОТОПЛЕНИЕ", 1000, unit="Гкал"),
    }
    coordinator = _coordinator({"tariffs": tariffs})
    options = coordinator.entry.options["account_options"]["42"]
    options.update({"waste_water_tariff": "ОТВЕД. СТОЧ. ВОД СОИ", "cold_water_tariff": "ХВ НА СОД-Е ОБЩ.ИМУЩ.",
                    "hot_water_tariff": "ХВ НА СОД-Е ОБЩ.ИМУЩ.", "hot_water_heating_tariff": "ОТОПЛЕНИЕ"})
    cold = ZKHCombinedColdWaterTariffSensor(coordinator, "42")
    hot = ZKHCombinedHotWaterTariffSensor(coordinator, 0.05, "42")
    assert cold.native_value == 17
    assert hot.native_value == 67
    del tariffs["c2"]
    assert cold.native_value is None
    assert hot.native_value is None


def test_leading_zeroes_preserved_in_account_ids() -> None:
    coordinator = _coordinator({"accounts": {"0042": {"address": "Адрес", "meters": {}}}})
    sensor = ZKHMeterSensor(coordinator, "1", {}, "0042")
    assert sensor.unique_id == "entry_account_0042_meter_1"
    assert sensor.device_info["identifiers"] == {("zkhnso", "entry_account_0042")}


@pytest.mark.parametrize("data,expected", [
    ({"account_id": 123, "address": "Адрес"}, "№123 Адрес"),
    ({"account_id": 0, "address": " Адрес "}, "№0  Адрес"),
])
def test_device_name_uses_account_details(data, expected) -> None:
    """The device name includes the account number and address as supplied."""
    account_id = str(data["account_id"])
    coordinator = _coordinator({"accounts": {account_id: {"address": data["address"]}}})
    coordinator.entry.data = data
    sensor = ZKHCombinedColdWaterTariffSensor(coordinator, account_id)
    assert sensor.device_info["name"] == expected
    assert sensor.device_info["identifiers"] == {("zkhnso", f"entry_account_{account_id}")}


def test_device_name_reflects_updated_account_details() -> None:
    coordinator = _coordinator({})
    sensor = ZKHCombinedColdWaterTariffSensor(coordinator, "42")
    coordinator.data["accounts"]["42"]["address"] = "Новый адрес"
    assert sensor.device_info["name"] == "№42 Новый адрес"
    assert sensor.device_info["identifiers"] == {("zkhnso", "entry_account_42")}


def test_meter_uses_current_coordinator_data_and_clears_removed_meter() -> None:
    """Meters do not retain stale readings after a successful refresh."""
    data = {"meters": {"1": {"name": "Вода", "unit": "куб.м.", "value": 12.5}}}
    coordinator = _coordinator(data)
    sensor = ZKHMeterSensor(coordinator, "1", data["meters"]["1"], "42")

    assert sensor.native_value == 12.5
    assert sensor.device_class == SensorDeviceClass.WATER
    assert sensor.native_unit_of_measurement == UnitOfVolume.CUBIC_METERS

    coordinator.data["accounts"]["42"]["meters"] = {}
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


@pytest.mark.parametrize("is_archived", [False, True])
def test_meter_archive_default_and_icon_follow_coordinator(is_archived) -> None:
    meter = {"name": "Вода", "serial_number": "1", "unit": "куб.м.", "value": 12.5,
             "is_archived": is_archived}
    coordinator = _coordinator({"meters": {"1": meter}})
    sensor = ZKHMeterSensor(coordinator, "1", meter, "42")
    assert sensor.unique_id == "entry_account_42_meter_1"
    assert sensor.entity_registry_enabled_default is (not is_archived)
    assert sensor.icon == ("mdi:archive" if is_archived else "mdi:counter")
    assert sensor.native_value == 12.5

    coordinator.data["accounts"]["42"]["meters"]["1"]["is_archived"] = not is_archived
    assert sensor.icon == ("mdi:counter" if is_archived else "mdi:archive")
    assert sensor.entity_registry_enabled_default is (not is_archived)


def test_meter_device_classes() -> None:
    """Gas, electricity, heat, and generic volumes use correct device classes."""
    cases = [
        ("Газ", "куб.м.", SensorDeviceClass.GAS, UnitOfVolume.CUBIC_METERS),
        ("Электричество", "кВтч", SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
        ("Тепло", "Гкал", SensorDeviceClass.ENERGY, UnitOfEnergy.GIGA_CALORIE),
    ]
    for index, (name, unit, device_class, native_unit) in enumerate(cases):
        meter = {"name": name, "unit": unit, "value": 1}
        sensor = ZKHMeterSensor(_coordinator({"meters": {str(index): meter}}), str(index), meter, "42")
        assert sensor.device_class == device_class
        assert sensor.native_unit_of_measurement == native_unit


def test_tariff_uses_current_data_and_clears_removed_tariff() -> None:
    """Tariffs use `rate_per_unit` and clear stale state when removed."""
    tariff = {"name": "Water", "unit": "куб.м.", "rate_per_unit": 50.25, "norm": 3, "from_date": "2026-01-01"}
    coordinator = _coordinator({"tariffs": {"Water": tariff}})
    sensor = ZKHTariffSensor(coordinator, "Water", tariff, "42")

    assert sensor.native_value == 50.25
    assert sensor.device_class is None
    assert sensor.native_unit_of_measurement == "RUB/m³"

    coordinator.data["accounts"]["42"]["tariffs"] = {}
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
            tariff, "42",
        )
        assert sensor.device_class is None
        assert sensor.native_unit_of_measurement == expected_unit


@pytest.mark.parametrize("sensor_type", [ZKHCombinedColdWaterTariffSensor, ZKHCombinedHotWaterTariffSensor])
def test_combined_tariff_search_uses_name_and_priority(sensor_type) -> None:
    """Select the first available name, not the first dictionary entry or key."""
    first = {"name": "ОТВЕД. СТОЧ. ВОД СОИ", "rate_per_unit": 12.5, "unit": "куб.м."}
    second = {"name": "ВОДООТВЕДЕНИЕ", "rate_per_unit": 8.0, "unit": "куб.м."}
    coordinator = _coordinator({"tariffs": {"ОТВЕД. СТОЧ. ВОД СОИ": second, "other": first}})
    sensor = sensor_type(coordinator, 0.05, "42") if sensor_type is ZKHCombinedHotWaterTariffSensor else sensor_type(coordinator, "42")
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

    coordinator.data["accounts"]["42"]["tariffs"] = {"new": {"name": names[0], "rate_per_unit": 15, "unit": "куб.м."}}
    assert sensor._get_first_available_tariff(names, VOLUME_UNITS) == coordinator.data["accounts"]["42"]["tariffs"]["new"]
    assert sensor._get_first_available_tariff([names[1]], VOLUME_UNITS) is None


@pytest.mark.parametrize("data", [None, {}, {"tariffs": None}, {"tariffs": []}, {"tariffs": {"x": None, "y": {"name": "X"}}}])
def test_combined_tariff_search_handles_missing_data(data) -> None:
    """Missing or incomplete data does not make a tariff available."""
    sensor = ZKHCombinedColdWaterTariffSensor(_coordinator(data), "42")
    assert sensor._get_first_available_tariff(["X"], VOLUME_UNITS) is None


def test_combined_tariff_search_skips_unavailable_duplicate() -> None:
    """A later tariff with the same name can supply the missing value."""
    available = {"name": "X", "rate_per_unit": 0, "unit": "куб.м."}
    sensor = ZKHCombinedColdWaterTariffSensor(_coordinator({
        "tariffs": {"first": {"name": "X", "rate_per_unit": None}, "second": available}
    }), "42")
    assert sensor._get_first_available_tariff(["X"], VOLUME_UNITS) is available


def test_combined_tariff_sensor_properties() -> None:
    sensor = ZKHCombinedTariffSensor(_coordinator({}), "Test", "42")
    assert sensor.unique_id == "entry_account_42_combined_tariff_Test"
    assert sensor.translation_key == "combined_tariff"
    assert sensor.translation_placeholders == {"name": "Test"}
    assert sensor.native_unit_of_measurement == "RUB/m³"
    assert sensor.icon == "mdi:sigma"


@pytest.mark.parametrize("unit", ["куб.м.", "куб.м", "м³", "m³"])
def test_combined_tariff_search_accepts_volume_units(unit) -> None:
    tariff = {"name": "X", "rate_per_unit": 10, "unit": unit}
    sensor = ZKHCombinedTariffSensor(_coordinator({"tariffs": {"key": tariff}}), "Test", "42")
    assert sensor._get_first_available_tariff(["X"], VOLUME_UNITS) is tariff


@pytest.mark.parametrize("unit", [None, "Гкал", "кВтч", "кв.м."])
def test_combined_tariff_search_rejects_non_volume_units(unit) -> None:
    tariff = {"name": "X", "rate_per_unit": 10, "unit": unit}
    sensor = ZKHCombinedTariffSensor(_coordinator({"tariffs": {"key": tariff}}), "Test", "42")
    assert sensor._get_first_available_tariff(["X"], VOLUME_UNITS) is None


@pytest.mark.parametrize("unit", ["Гкал", "Gcal"])
def test_combined_tariff_search_accepts_heat_units(unit) -> None:
    tariff = {"name": "X", "rate_per_unit": 10, "unit": unit}
    sensor = ZKHCombinedTariffSensor(_coordinator({"tariffs": {"key": tariff}}), "Test", "42")
    assert sensor._get_first_available_tariff(["X"], HEAT_UNITS) is tariff
    assert sensor._get_first_available_tariff(["X"], VOLUME_UNITS) is None


def _tariff(name, rate, date=None, unit="куб.м."):
    return {"name": name, "rate_per_unit": rate, "unit": unit, "from_date": date}


@pytest.mark.parametrize("waste_name", ["ВОДООТВЕДЕНИЕ", "ОТВЕД. СТОЧ. ВОД СОИ"])
@pytest.mark.parametrize("cold_name", ["ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", "ХВ НА СОД-Е ОБЩ.ИМУЩ."])
def test_combined_cold_water_calculates_rate_and_attributes(waste_name, cold_name) -> None:
    waste = _tariff(waste_name, 12.5, "2026-02-01")
    cold = _tariff(cold_name, 30.25, "2026-03-01")
    sensor = ZKHCombinedColdWaterTariffSensor(_coordinator({"tariffs": {"waste": waste, "cold": cold}}), "42")
    assert sensor.native_value == 42.75
    assert sensor.extra_state_attributes == {
        "name": "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ",
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
    }}), "42")
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
    sensor = ZKHCombinedColdWaterTariffSensor(_coordinator({"tariffs": tariffs}), "42")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_combined_cold_water_priority_and_coordinator_updates() -> None:
    coordinator = _coordinator({"tariffs": {
        "waste_backup": _tariff("ОТВЕД. СТОЧ. ВОД СОИ", 6),
        "waste_primary": _tariff("ВОДООТВЕДЕНИЕ", 5),
        "cold_backup": _tariff("ХВ НА СОД-Е ОБЩ.ИМУЩ.", 11),
        "cold_primary": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10),
    }})
    sensor = ZKHCombinedColdWaterTariffSensor(coordinator, "42")
    assert sensor.native_value == 15
    assert sensor.extra_state_attributes["names"] == ["ВОДООТВЕДЕНИЕ", "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ"]
    coordinator.data["accounts"]["42"]["tariffs"]["waste_primary"]["rate_per_unit"] = None
    coordinator.data["accounts"]["42"]["tariffs"]["cold_primary"]["rate_per_unit"] = None
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}
    coordinator.data["accounts"]["42"]["tariffs"] = {}
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_setup_adds_combined_water_tariff_sensors(device_registry) -> None:
    coordinator = _coordinator({"tariffs": {"water": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10)}})
    entry = SimpleNamespace(entry_id="entry", runtime_data=coordinator, options={})
    add_entities = MagicMock()
    asyncio.run(async_setup_entry(None, entry, add_entities))
    add_entities.assert_called_once()
    entities = add_entities.call_args.args[0]
    assert sum(isinstance(sensor, ZKHTariffSensor) for sensor in entities) == 1
    assert sum(isinstance(sensor, ZKHCombinedColdWaterTariffSensor) for sensor in entities) == 1
    assert sum(isinstance(sensor, ZKHCombinedHotWaterTariffSensor) for sensor in entities) == 1
    assert sum(isinstance(sensor, ZKHCombinedTariffFormulaSensor) for sensor in entities) == 2


def test_combined_tariff_formula_diagnostics_update_and_handle_missing_data() -> None:
    coordinator = _coordinator({"tariffs": {
        "waste": _tariff("ВОДООТВЕДЕНИЕ", 5),
        "cold": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10),
        "hot": _tariff("ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ", 50),
        "heating": _tariff("ОТОПЛЕНИЕ", 1000, unit="Гкал"),
    }})
    cold = ZKHCombinedColdWaterTariffSensor(coordinator, "42")
    hot = ZKHCombinedHotWaterTariffSensor(coordinator, 0.05, "42")
    cold_formula = ZKHCombinedTariffFormulaSensor(cold)
    hot_formula = ZKHCombinedTariffFormulaSensor(hot)

    assert cold_formula.entity_category == EntityCategory.DIAGNOSTIC
    assert cold_formula.unique_id == f"{cold.unique_id}_formula"
    assert hot_formula.unique_id == f"{hot.unique_id}_formula"
    assert cold_formula.device_info == cold.device_info
    assert cold_formula.translation_key == "combined_tariff_formula"
    assert cold_formula.translation_placeholders == {"name": "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ"}
    assert cold_formula.native_unit_of_measurement is None
    assert cold_formula.native_value == "ВОДООТВЕДЕНИЕ + ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ"
    assert hot_formula.native_value == "ВОДООТВЕДЕНИЕ + ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ"

    coordinator.entry.options["account_options"]["42"]["hot_water_tariff"] = "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ"
    assert hot_formula.native_value == "ВОДООТВЕДЕНИЕ + ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ + ОТОПЛЕНИЕ × 0.05"
    coordinator.data["accounts"]["42"]["tariffs"]["cold"]["rate_per_unit"] = None
    assert cold_formula.native_value is None
    assert hot_formula.native_value is None
    coordinator.data = None
    assert cold_formula.native_value is None
    assert hot_formula.native_value is None


def test_combined_tariff_formula_diagnostics_are_account_specific() -> None:
    coordinator = _coordinator({"accounts": {
        "42": {"tariffs": {"w": _tariff("ВОДООТВЕДЕНИЕ", 5), "c": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10)}},
        "43": {"tariffs": {"w": _tariff("ОТВЕД. СТОЧ. ВОД СОИ", 5), "c": _tariff("ХВ НА СОД-Е ОБЩ.ИМУЩ.", 10)}},
    }})
    first = ZKHCombinedTariffFormulaSensor(ZKHCombinedColdWaterTariffSensor(coordinator, "42"))
    second = ZKHCombinedTariffFormulaSensor(ZKHCombinedColdWaterTariffSensor(coordinator, "43"))
    assert first.native_value == "ВОДООТВЕДЕНИЕ + ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ"
    assert second.native_value == "ОТВЕД. СТОЧ. ВОД СОИ + ХВ НА СОД-Е ОБЩ.ИМУЩ."
    assert first.unique_id != second.unique_id


@pytest.mark.parametrize("hot_name", ["ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ", "ГВ НА СОД-Е ОБЩ.ИМУЩ."])
def test_combined_hot_water_direct_tariff(hot_name) -> None:
    """A direct hot-water tariff takes precedence over the heating calculation."""
    coordinator = _coordinator({"tariffs": {
        "waste": _tariff("ВОДООТВЕДЕНИЕ", 12.5, "2026-02-01"),
        "hot": _tariff(hot_name, 50, "2026-03-01"),
        "fluid": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 20),
        "heating": _tariff("ОТОПЛЕНИЕ", 1000, unit="Гкал"),
    }})
    sensor = ZKHCombinedHotWaterTariffSensor(coordinator, None, "42")
    assert sensor.native_value == 62.5
    assert sensor.extra_state_attributes == {
        "name": "ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ",
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
    sensor = ZKHCombinedHotWaterTariffSensor(coordinator, 0.06528, "42")
    assert sensor.native_value == 123.34
    assert sensor.extra_state_attributes == {
        "name": "ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ",
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
    sensor = ZKHCombinedHotWaterTariffSensor(coordinator, 0, "42")
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes["from_date"] == expected


def test_combined_hot_water_without_heating_rate() -> None:
    """The component calculation needs a configured heating rate, but accepts zero."""
    coordinator = _coordinator({"tariffs": {
        "waste": _tariff("ВОДООТВЕДЕНИЕ", 5, "2026-01-01"),
        "fluid": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10, "2026-02-01"),
        "heating": _tariff("ОТОПЛЕНИЕ", 1000, "2026-03-01", "Гкал"),
    }})
    sensor = ZKHCombinedHotWaterTariffSensor(coordinator, None, "42")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}

    zero_rate_sensor = ZKHCombinedHotWaterTariffSensor(coordinator, 0, "42")
    assert zero_rate_sensor.native_value == 15
    assert zero_rate_sensor.extra_state_attributes == {
        "name": "ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ",
        "names": ["ВОДООТВЕДЕНИЕ", "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", "ОТОПЛЕНИЕ"],
        "formula": "ВОДООТВЕДЕНИЕ + ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ + ОТОПЛЕНИЕ × 0",
        "from_date": "2026-03-01",
    }


@pytest.mark.parametrize("options", [{}, {"water_heating_rate": None}])
def test_setup_combined_hot_water_without_heating_rate(options, device_registry) -> None:
    """Missing and explicitly unset options both yield an unknown value."""
    coordinator = _coordinator({"tariffs": {
        "waste": _tariff("ВОДООТВЕДЕНИЕ", 5),
        "fluid": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10),
        "heating": _tariff("ОТОПЛЕНИЕ", 1000, unit="Гкал"),
    }})
    entry = SimpleNamespace(entry_id="entry", runtime_data=coordinator, options=options)
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
    sensor = ZKHCombinedHotWaterTariffSensor(_coordinator({"tariffs": tariffs}), 0.05, "42")
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
    sensor = ZKHCombinedHotWaterTariffSensor(_coordinator({"tariffs": tariffs}), 0.05, "42")
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
    sensor = ZKHCombinedHotWaterTariffSensor(coordinator, 0.05, "42")
    assert sensor.native_value == 75
    assert sensor.extra_state_attributes["names"] == [
        "ВОДООТВЕДЕНИЕ", "ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ (ТЕПЛОНОСИТ)", "ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ (ПОДОГРЕВ)"
    ]
    coordinator.data["accounts"]["42"]["tariffs"]["fluid_primary"]["rate_per_unit"] = None
    coordinator.data["accounts"]["42"]["tariffs"]["heat_primary"]["rate_per_unit"] = None
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}
    coordinator.data["accounts"]["42"]["tariffs"] = {}
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_setup_passes_heating_rate_to_combined_hot_water_sensor(device_registry) -> None:
    coordinator = _coordinator({"tariffs": {
        "waste": _tariff("ВОДООТВЕДЕНИЕ", 5),
        "fluid": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10),
        "heating": _tariff("ОТОПЛЕНИЕ", 1000, unit="Гкал"),
    }})
    coordinator.entry.options["account_options"]["42"]["water_heating_rate"] = 0.05
    entry = SimpleNamespace(entry_id="entry", runtime_data=coordinator, options=coordinator.entry.options)
    add_entities = MagicMock()
    asyncio.run(async_setup_entry(None, entry, add_entities))
    entities = add_entities.call_args.args[0]
    sensor = next(entity for entity in entities if isinstance(entity, ZKHCombinedHotWaterTariffSensor))
    assert sensor.native_value == 65


def test_heating_rate_is_independent_per_account_and_ignores_legacy_rate(device_registry) -> None:
    tariffs = {"waste": _tariff("ВОДООТВЕДЕНИЕ", 5), "fluid": _tariff("ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", 10), "heating": _tariff("ОТОПЛЕНИЕ", 1000, unit="Гкал")}
    coordinator = _coordinator({"accounts": {number: {"address": number, "tariffs": tariffs} for number in ("42", "43", "44")}})
    options = {"water_heating_rate": 0.9, "account_options": coordinator.entry.options["account_options"]}
    options["account_options"]["42"]["water_heating_rate"] = 0.05
    options["account_options"]["43"]["water_heating_rate"] = 0.02
    coordinator.entry.options = options
    add_entities = MagicMock()
    asyncio.run(async_setup_entry(None, SimpleNamespace(entry_id="entry", runtime_data=coordinator, options=options), add_entities))
    sensors = {sensor._account_id: sensor for sensor in add_entities.call_args.args[0] if isinstance(sensor, ZKHCombinedHotWaterTariffSensor)}
    assert sensors["42"].native_value == 65
    assert sensors["43"].native_value == 35
    assert sensors["44"].native_value is None
