"""Tests for water heating configuration and its coefficient table."""
import asyncio
import json
import string
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.zkhnso import _async_options_updated
from custom_components.zkhnso import config_flow as flow_module
from custom_components.zkhnso.config_flow import ConfigFlow, ZKHOptionsFlow
from custom_components.zkhnso.sensor import ZKHCombinedColdWaterTariffSensor, ZKHTariffSensor, async_setup_entry
from custom_components.zkhnso.water_heating import BUILDING_FEATURES, SYSTEMS, water_heating_rate


@pytest.mark.parametrize("system_index", range(3))
@pytest.mark.parametrize("feature_index", range(4))
def test_rate_table(system_index, feature_index):
    """All twelve table combinations match the published rates."""
    expected = [
        [0.06779, 0.06528, 0.06528],
        [0.06277, 0.06026, 0.06026],
        [0.06277, 0.06026, 0.06026],
        [0.05775, 0.05524, 0.05524],
    ]
    assert water_heating_rate(SYSTEMS[system_index], BUILDING_FEATURES[feature_index]) == expected[feature_index][system_index]


def _options_flow(monkeypatch, options=None):
    flow = ZKHOptionsFlow()
    flow._config_entry = SimpleNamespace(options=options or {})
    monkeypatch.setattr(flow, "async_show_form", lambda **kwargs: kwargs)
    monkeypatch.setattr(flow, "async_create_entry", lambda **kwargs: kwargs)
    return flow


def _suggested(form):
    return {
        marker.schema: (marker.description or {}).get("suggested_value")
        for marker in form["data_schema"].schema
    }


def test_options_none_and_existing_options(monkeypatch):
    """None clears the effective rate and preserves unrelated options."""
    flow = _options_flow(monkeypatch, {"other": 42, "water_heating_rate": 1, "rate_type": "manual"})
    first = asyncio.run(flow.async_step_init())
    assert first["step_id"] == "rate_type"
    assert _suggested(first)["rate_type"] == "manual"
    result = asyncio.run(flow.async_step_rate_type({"rate_type": "none"}))
    assert result["data"]["water_heating_rate"] is None
    assert result["data"]["other"] == 42


@pytest.mark.parametrize("rate", [0.06528, 0, 0.123456789])
def test_options_show_saved_rate_below_type(monkeypatch, rate):
    """Display the saved rate without rounding, including zero."""
    flow = _options_flow(monkeypatch, {"water_heating_rate": rate, "rate_type": "manual"})
    form = asyncio.run(flow.async_step_init())
    assert form["step_id"] == "rate_type"
    assert form["description_placeholders"] == {"rate": str(rate)}
    assert _suggested(form)["rate_type"] == "manual"
    for language in ("ru", "en"):
        translation = json.loads(
            (Path(flow_module.__file__).parent / "translations" / f"{language}.json").read_text(encoding="utf-8")
        )
        helper = translation["options"]["step"][form["step_id"]]["data_description"]["rate_type"]
        expected = {
            "ru": f"Текущий норматив — {rate} Гкал/м³",
            "en": f"Current rate — {rate} Gcal/m³",
        }
        assert helper.format(**form["description_placeholders"]) == expected[language]


@pytest.mark.parametrize("options", [{}, {"water_heating_rate": None, "rate_type": "none"}])
def test_options_show_unset_and_handle_submission(monkeypatch, options):
    """Old and unset entries show a translated helper and can still be saved."""
    flow = _options_flow(monkeypatch, options)
    form = asyncio.run(flow.async_step_init())
    assert form["step_id"] == "rate_type_unset"
    for language, expected in (("ru", "Текущий норматив — не задан"), ("en", "Current rate — not set")):
        translation = json.loads(
            (Path(flow_module.__file__).parent / "translations" / f"{language}.json").read_text(encoding="utf-8")
        )
        assert translation["options"]["step"][form["step_id"]]["data_description"]["rate_type"] == expected
    result = asyncio.run(flow.async_step_rate_type_unset({"rate_type": "none"}))
    assert result["data"]["water_heating_rate"] is None


@pytest.mark.parametrize("system", SYSTEMS)
@pytest.mark.parametrize("features", BUILDING_FEATURES)
def test_auto_summary_and_selections_are_restored(monkeypatch, system, features):
    """Each auto combination displays the saved rate and restores its selections."""
    flow = _options_flow(monkeypatch, {
        "rate_type": "auto", "water_heating_rate": water_heating_rate(system, features),
        "heating_system": system, "building_features": features,
    })
    form = asyncio.run(flow.async_step_init())
    assert form["description_placeholders"] == {"rate": str(water_heating_rate(system, features))}
    assert _suggested(form)["rate_type"] == "auto"
    auto_form = asyncio.run(flow.async_step_rate_type({"rate_type": "auto"}))
    assert _suggested(auto_form) == {"heating_system": system, "building_features": features}
    for language in ("ru", "en"):
        translation = json.loads(
            (Path(flow_module.__file__).parent / "translations" / f"{language}.json").read_text(encoding="utf-8")
        )
        assert translation["selector"]["building_features"]["options"][features]
        assert translation["selector"]["heating_system"]["options"][system]


@pytest.mark.parametrize("rate_type", ["manual", "none"])
def test_summary_hides_stale_auto_selections(monkeypatch, rate_type):
    """Old auto selections are not presented as inputs to a manual or unset rate."""
    flow = _options_flow(monkeypatch, {
        "rate_type": rate_type, "water_heating_rate": 0.05 if rate_type == "manual" else None,
        "heating_system": SYSTEMS[0], "building_features": BUILDING_FEATURES[0],
    })
    form = asyncio.run(flow.async_step_init())
    if rate_type == "none":
        assert form["step_id"] == "rate_type_unset"
        assert form["description_placeholders"] is None
    else:
        assert form["step_id"] == "rate_type"
        assert form["description_placeholders"] == {"rate": "0.05"}


def test_summary_uses_only_saved_options(monkeypatch):
    """A calculated but unconfirmed rate is not displayed as the current rate."""
    flow = _options_flow(monkeypatch, {
        "rate_type": "auto", "water_heating_rate": 0.06779,
        "heating_system": SYSTEMS[0], "building_features": BUILDING_FEATURES[0],
    })
    asyncio.run(flow.async_step_rate_auto({"heating_system": SYSTEMS[1], "building_features": BUILDING_FEATURES[3]}))
    summary = asyncio.run(flow.async_step_init())["description_placeholders"]
    assert summary == {"rate": "0.06779"}


@pytest.mark.parametrize("language", ["ru", "en"])
def test_translation_placeholders_are_valid(language):
    """Ensure translation placeholders are valid identifiers for hassfest."""
    translation = json.loads(
        (Path(flow_module.__file__).parent / "translations" / f"{language}.json").read_text(encoding="utf-8")
    )

    def check(value):
        if isinstance(value, dict):
            for item in value.values():
                check(item)
        elif isinstance(value, str):
            for _, name, _, _ in string.Formatter().parse(value):
                if name:
                    assert name.isidentifier()

    check(translation)


def test_options_manual_validation_and_restore(monkeypatch):
    """Manual numbers may use a comma and are restored when reopened."""
    flow = _options_flow(monkeypatch)
    assert asyncio.run(flow.async_step_rate_type({"rate_type": "manual"}))["step_id"] == "rate_manual"
    for value in ("x", "-1", "nan", "inf", ""):
        assert asyncio.run(flow.async_step_rate_manual({"manual_rate": value}))["errors"] == {"base": "invalid_rate"}
    result = asyncio.run(flow.async_step_rate_manual({"manual_rate": "0,06277"}))
    assert result["data"]["water_heating_rate"] == 0.06277
    reopened = _options_flow(monkeypatch, result["data"])
    assert _suggested(asyncio.run(reopened.async_step_init()))["rate_type"] == "manual"
    assert _suggested(asyncio.run(reopened.async_step_rate_type({"rate_type": "manual"})))["manual_rate"] == "0,06277"


def test_options_auto_confirm_and_restore(monkeypatch):
    """Auto selection is saved only after displaying the calculated coefficient."""
    flow = _options_flow(monkeypatch)
    assert asyncio.run(flow.async_step_rate_type({"rate_type": "auto"}))["step_id"] == "rate_auto"
    selection = {"heating_system": "closed_external", "building_features": "insulated_without_towel_rails"}
    preview = asyncio.run(flow.async_step_rate_auto(selection))
    assert preview["step_id"] == "rate_confirm"
    assert preview["description_placeholders"]["rate"] == "0.05775"
    result = asyncio.run(flow.async_step_rate_confirm({}))
    assert result["data"]["water_heating_rate"] == 0.05775
    reopened = _options_flow(monkeypatch, result["data"])
    form = asyncio.run(reopened.async_step_rate_type({"rate_type": "auto"}))
    assert _suggested(form) == selection


def test_invalid_selections_do_not_save(monkeypatch):
    flow = _options_flow(monkeypatch)
    assert asyncio.run(flow.async_step_rate_type({"rate_type": "bad"}))["errors"] == {"base": "invalid_rate_type"}
    assert asyncio.run(flow.async_step_rate_auto({"heating_system": "bad", "building_features": "bad"}))["errors"] == {"base": "invalid_selection"}


@pytest.mark.parametrize("rate_type", ["none", "manual", "auto"])
def test_initial_setup_collects_rate_after_credentials(monkeypatch, rate_type):
    """Do not create an entry until credentials and a rate choice are complete."""
    import custom_components.zkhnso.config_flow as flow_module

    flow = ConfigFlow()
    flow.hass = None
    monkeypatch.setattr(flow_module, "validate_input", AsyncMock(return_value={
        "username": "user", "account_id": 123, "address": "address", "title": "account",
    }))
    monkeypatch.setattr(flow, "async_set_unique_id", AsyncMock())
    monkeypatch.setattr(flow, "_abort_if_unique_id_configured", MagicMock())
    monkeypatch.setattr(flow, "async_show_form", lambda **kwargs: kwargs)
    create = MagicMock(side_effect=lambda **kwargs: kwargs)
    monkeypatch.setattr(flow, "async_create_entry", create)
    result = asyncio.run(flow.async_step_user({"username": "user", "password": "secret"}))
    assert result["step_id"] == "rate_type"
    create.assert_not_called()
    result = asyncio.run(flow.async_step_rate_type({"rate_type": rate_type}))
    if rate_type == "manual":
        assert result["step_id"] == "rate_manual"
        result = asyncio.run(flow.async_step_rate_manual({"manual_rate": "0.05"}))
    elif rate_type == "auto":
        assert result["step_id"] == "rate_auto"
        result = asyncio.run(flow.async_step_rate_auto({"heating_system": SYSTEMS[0], "building_features": BUILDING_FEATURES[0]}))
        assert result["step_id"] == "rate_confirm"
        result = asyncio.run(flow.async_step_rate_confirm({}))
    assert result["data"]["username"] == "user"
    assert result["options"]["rate_type"] == rate_type
    assert result["options"]["water_heating_rate"] == {"none": None, "manual": 0.05, "auto": 0.06779}[rate_type]


def test_options_update_reloads_entry():
    reload = AsyncMock()
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_reload=reload))
    asyncio.run(_async_options_updated(hass, SimpleNamespace(entry_id="entry")))
    reload.assert_awaited_once_with("entry")


def test_sensor_setup_with_configured_rate():
    """A configured heating rate does not prevent tariff sensor creation."""
    entry = SimpleNamespace(
        entry_id="entry", data={"account_id": 42}, options={"water_heating_rate": 0.06528}
    )
    entry.runtime_data = SimpleNamespace(
        entry=entry,
        data={"tariffs": {"water": {"name": "Water", "unit": "куб.м.", "rate_per_unit": 50.25}}},
    )
    add_entities = MagicMock()
    asyncio.run(async_setup_entry(None, entry, add_entities))
    add_entities.assert_called_once()
    args, kwargs = add_entities.call_args
    assert kwargs == {"update_before_add": False}
    assert len(args[0]) == 2
    assert sum(isinstance(entity, ZKHCombinedColdWaterTariffSensor) for entity in args[0]) == 1
    sensor = next(entity for entity in args[0] if isinstance(entity, ZKHTariffSensor))
    assert isinstance(sensor, ZKHTariffSensor)
    assert sensor.unique_id == "entry_tariff_water"
    assert sensor.native_value == 50.25
