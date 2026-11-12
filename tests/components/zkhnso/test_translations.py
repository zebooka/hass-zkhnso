"""Tests for translation consistency."""
import json
from pathlib import Path
from string import Formatter
from types import SimpleNamespace

import pytest

from custom_components.zkhnso import config_flow
from custom_components.zkhnso.sensor import ZKHCompanySensor, ZKHMeterSensor, ZKHTariffSensor, ZKHCombinedColdWaterTariffSensor, ZKHCombinedHotWaterTariffSensor, ZKHCombinedTariffFormulaSensor
from homeassistant.util import slugify


def _flatten_translations(value, prefix=""):
    """Map translation paths to their text."""
    if isinstance(value, dict):
        return {
            path: text
            for key, item in value.items()
            for path, text in _flatten_translations(item, f"{prefix}.{key}" if prefix else key).items()
        }
    return {prefix: value}


def test_english_and_russian_translations_match() -> None:
    """Both languages provide the same keys and formatting placeholders."""
    translations_dir = Path(config_flow.__file__).parent / "translations"
    translations = {
        language: _flatten_translations(json.loads(
            (translations_dir / f"{language}.json").read_text(encoding="utf-8")
        ))
        for language in ("en", "ru")
    }
    english, russian = translations["en"], translations["ru"]
    assert english.keys() == russian.keys()

    for path, text in english.items():
        english_fields = {field for _, field, _, _ in Formatter().parse(text) if field is not None}
        russian_fields = {field for _, field, _, _ in Formatter().parse(russian[path]) if field is not None}
        assert english_fields == russian_fields, path


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ru", "Настройки ЖКХНСО обновлены"), ("en", "ZKHNSO settings were updated")],
)
def test_reconfigure_success_message_is_translated(language, expected) -> None:
    """Successful reconfiguration has a localized abort message."""
    translations_dir = Path(config_flow.__file__).parent / "translations"
    translation = json.loads((translations_dir / f"{language}.json").read_text(encoding="utf-8"))
    assert translation["config"]["abort"]["reconfigure_successful"] == expected


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("ru", "Данные интеграции ещё недоступны. Дождитесь завершения загрузки и попробуйте снова."),
        ("en", "Integration data is not available yet. Wait for it to finish loading and try again."),
    ],
)
def test_options_not_ready_message_is_translated(language, expected) -> None:
    translations_dir = Path(config_flow.__file__).parent / "translations"
    translation = json.loads((translations_dir / f"{language}.json").read_text(encoding="utf-8"))
    assert translation["options"]["abort"]["not_ready"] == expected


@pytest.mark.parametrize("language", ["en", "ru"])
def test_sensor_names_and_object_ids_use_entity_translations(language) -> None:
    """Home Assistant uses the configured language for names and English for IDs."""
    translations_dir = Path(config_flow.__file__).parent / "translations"
    def platform_translations(lang):
        translations = json.loads((translations_dir / f"{lang}.json").read_text(encoding="utf-8"))
        return {f"component.zkhnso.entity.sensor.{key}.name": value["name"]
                for key, value in translations["entity"]["sensor"].items()}

    coordinator = SimpleNamespace(entry=SimpleNamespace(entry_id="entry", data={}),
                                  data={"accounts": {"43": {"address": "Адрес 2"}}})
    meter = ZKHMeterSensor(coordinator, "222", {"name": "Газ", "serial_number": "222", "unit": "куб.м."}, "43")
    tariff = ZKHTariffSensor(coordinator, "ГАЗ", {"name": "ГАЗ", "unit": "куб.м."}, "43")
    combined = ZKHCombinedColdWaterTariffSensor(coordinator, "43")
    formula = ZKHCombinedTariffFormulaSensor(combined)
    for entity in (meter, tariff, combined, formula):
        entity.platform = SimpleNamespace(platform_name="zkhnso", domain="sensor",
            platform_translations=platform_translations(language), object_id_platform_translations=platform_translations("en"),
            component_translations={}, object_id_component_translations={})
        if hasattr(entity, "platform_data"):
            entity.platform_data = entity.platform
        assert entity.name == platform_translations(language)[f"component.zkhnso.entity.sensor.{entity.translation_key}.name"].format(**entity.translation_placeholders)
    assert slugify(f"{meter.device_info['name']} {meter.suggested_object_id}") == "no43_adres_2_meter_gaz_no222"
    assert slugify(f"{tariff.device_info['name']} {tariff.suggested_object_id}") == "no43_adres_2_tariff_gaz"
    assert slugify(f"{combined.device_info['name']} {combined.suggested_object_id}") == "no43_adres_2_combined_tariff_kholodnoe_vodosnabzhenie"


@pytest.mark.parametrize("language", ["en", "ru"])
def test_meter_and_company_attribute_names_are_translated(language) -> None:
    translations_dir = Path(config_flow.__file__).parent / "translations"
    def platform_translations(lang):
        translations = json.loads((translations_dir / f"{lang}.json").read_text(encoding="utf-8"))
        return {f"component.zkhnso.entity.sensor.{key}.name": value["name"]
                for key, value in translations["entity"]["sensor"].items()}

    meter_data = {"name": "Газ", "serial_number": "222", "unit": "куб.м.", "value": 5}
    account = {"meters": {"222": meter_data}, "company": {"name": "УК", "address": "Адрес УК"}}
    coordinator = SimpleNamespace(entry=SimpleNamespace(entry_id="entry"), data={"accounts": {"43": account}})
    meter = ZKHMeterSensor(coordinator, "222", meter_data, "43")
    company = ZKHCompanySensor(coordinator, "43")
    for entity in (meter, company):
        entity.platform = SimpleNamespace(platform_name="zkhnso", domain="sensor",
            platform_translations=platform_translations(language), object_id_platform_translations=platform_translations("en"),
            component_translations={}, object_id_component_translations={})
        if hasattr(entity, "platform_data"):
            entity.platform_data = entity.platform
        expected = platform_translations(language)[f"component.zkhnso.entity.sensor.{entity.translation_key}.name"].format(**entity.translation_placeholders)
        assert entity.extra_state_attributes["name"] == expected
        assert entity.name == expected
    assert meter.extra_state_attributes["meter_name"] == "Газ"
    assert meter.extra_state_attributes["serial_number"] == "222"
    assert company.extra_state_attributes["address"] == "Адрес УК"
    account["meters"] = {}
    assert meter.extra_state_attributes == {}
    coordinator.data = None
    assert company.extra_state_attributes == {}


@pytest.mark.parametrize("language", ["en", "ru"])
@pytest.mark.parametrize("hot_type", ["direct", "heating"])
def test_combined_tariff_attribute_names_are_translated(language, hot_type) -> None:
    translations_dir = Path(config_flow.__file__).parent / "translations"
    def platform_translations(lang):
        translations = json.loads((translations_dir / f"{lang}.json").read_text(encoding="utf-8"))
        return {f"component.zkhnso.entity.sensor.{key}.name": value["name"]
                for key, value in translations["entity"]["sensor"].items()}

    tariffs = {
        "waste": {"name": "ВОДООТВЕДЕНИЕ", "unit": "м³", "rate_per_unit": 5},
        "cold": {"name": "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", "unit": "м³", "rate_per_unit": 10},
        "hot": {"name": "ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ", "unit": "м³", "rate_per_unit": 50},
        "heat": {"name": "ОТОПЛЕНИЕ", "unit": "Гкал", "rate_per_unit": 1000},
    }
    options = {"waste_water_tariff": "ВОДООТВЕДЕНИЕ", "cold_water_tariff": "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ",
               "hot_water_tariff": "ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ" if hot_type == "direct" else "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ",
               "hot_water_heating_tariff": "ОТОПЛЕНИЕ"}
    coordinator = SimpleNamespace(entry=SimpleNamespace(entry_id="entry", options={"account_options": {"43": options}}),
                                  data={"accounts": {"43": {"tariffs": tariffs}}})
    cold = ZKHCombinedColdWaterTariffSensor(coordinator, "43")
    hot = ZKHCombinedHotWaterTariffSensor(coordinator, 0.05, "43")
    for entity in (cold, hot, ZKHCombinedTariffFormulaSensor(cold), ZKHCombinedTariffFormulaSensor(hot)):
        entity.platform = SimpleNamespace(platform_name="zkhnso", domain="sensor",
            platform_translations=platform_translations(language), object_id_platform_translations=platform_translations("en"),
            component_translations={}, object_id_component_translations={})
        if hasattr(entity, "platform_data"):
            entity.platform_data = entity.platform
        expected = platform_translations(language)[f"component.zkhnso.entity.sensor.{entity.translation_key}.name"].format(**entity.translation_placeholders)
        assert entity.extra_state_attributes["name"] == expected
        assert entity.name == expected
