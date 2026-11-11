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
from custom_components.zkhnso.sensor import ZKHCombinedColdWaterTariffSensor, ZKHCombinedHotWaterTariffSensor, ZKHTariffSensor, async_setup_entry
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
    flow._config_entry = SimpleNamespace(options={"account_options": {"42": options or {}}}, runtime_data=SimpleNamespace(data={"accounts": {"42": {"address": "Адрес"}}}))
    flow._selected_account = "42"
    monkeypatch.setattr(flow, "async_show_form", lambda **kwargs: kwargs)
    monkeypatch.setattr(flow, "async_create_entry", lambda **kwargs: kwargs)
    return flow


def _suggested(form):
    return {
        marker.schema: (marker.description or {}).get("suggested_value")
        for marker in form["data_schema"].schema
    }


def test_options_select_account_and_keep_rates_separate(monkeypatch):
    flow = ZKHOptionsFlow()
    old = {"water_heating_rate": 0.1, "rate_type": "auto", "other": 5,
           "account_options": {"43": {"rate_type": "manual", "water_heating_rate": 0.08, "other": 5}}}
    flow._config_entry = SimpleNamespace(options=old, runtime_data=SimpleNamespace(data={"accounts": {
        "42": {"address": "Первый", "tariffs": {"cold": {"name": "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ"}}}, "43": {"address": "Второй"},
    }}))
    monkeypatch.setattr(flow, "async_show_form", lambda **kwargs: kwargs)
    monkeypatch.setattr(flow, "async_create_entry", lambda **kwargs: kwargs)
    assert asyncio.run(flow.async_step_init())["step_id"] == "init"
    assert asyncio.run(flow.async_step_init({"account_id": "missing"}))["errors"] == {"base": "invalid_account"}
    form = asyncio.run(flow.async_step_init({"account_id": "42"}))
    assert form["step_id"] == "tariffs"
    form = asyncio.run(flow.async_step_tariffs({"hot_water_tariff": "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ"}))
    assert form["step_id"] == "rate_type_unset"
    asyncio.run(flow.async_step_rate_type({"rate_type": "manual"}))
    result = asyncio.run(flow.async_step_rate_manual({"manual_rate": "0.04"}))
    assert result["data"]["account_options"]["42"]["water_heating_rate"] == 0.04
    assert result["data"]["account_options"]["43"]["water_heating_rate"] == 0.08
    assert set(result["data"]) == {"account_options"}
    assert result["data"]["account_options"]["43"] == {"rate_type": "manual", "water_heating_rate": 0.08}
    assert old["water_heating_rate"] == 0.1


def test_options_without_accounts_aborts(monkeypatch):
    flow = ZKHOptionsFlow()
    flow._config_entry = SimpleNamespace(options={}, runtime_data=SimpleNamespace(data={"accounts": {}}))
    monkeypatch.setattr(flow, "async_abort", lambda **kwargs: kwargs)
    assert asyncio.run(flow.async_step_init()) == {"reason": "no_accounts"}


@pytest.mark.parametrize("runtime_data", ["missing", None, SimpleNamespace(data=None)])
def test_options_not_ready_aborts_and_can_be_reopened(monkeypatch, runtime_data):
    flow = ZKHOptionsFlow()
    entry = SimpleNamespace(options={})
    if runtime_data != "missing":
        entry.runtime_data = runtime_data
    flow._config_entry = entry
    monkeypatch.setattr(flow, "async_abort", lambda **kwargs: kwargs)
    monkeypatch.setattr(flow, "async_show_form", lambda **kwargs: kwargs)

    assert asyncio.run(flow.async_step_init()) == {"reason": "not_ready"}
    entry.runtime_data = SimpleNamespace(data={"accounts": {"42": {"address": "Адрес"}}})
    assert asyncio.run(flow.async_step_init())["step_id"] == "init"
    assert entry.options == {}


@pytest.mark.parametrize("step,user_input", [
    ("init", {"account_id": "42"}),
    ("tariffs", {"hot_water_tariff": ""}),
    ("rate_type", {"rate_type": "none"}),
    ("rate_type_unset", {"rate_type": "none"}),
    ("rate_manual", {"manual_rate": "0.05"}),
    ("rate_auto", {"heating_system": SYSTEMS[0], "building_features": BUILDING_FEATURES[0]}),
    ("rate_confirm", {}),
])
def test_options_data_disappearing_between_steps_does_not_save(monkeypatch, step, user_input):
    flow = _options_flow(monkeypatch, {"rate_type": "manual", "water_heating_rate": 0.1})
    monkeypatch.setattr(flow, "async_abort", lambda **kwargs: kwargs)
    create_entry = MagicMock()
    monkeypatch.setattr(flow, "async_create_entry", create_entry)
    del flow._config_entry.runtime_data

    assert asyncio.run(getattr(flow, f"async_step_{step}")(user_input)) == {"reason": "not_ready"}
    create_entry.assert_not_called()
    assert flow._config_entry.options == {"account_options": {"42": {"rate_type": "manual", "water_heating_rate": 0.1}}}


def test_options_loaded_empty_data_reports_no_accounts(monkeypatch):
    flow = _options_flow(monkeypatch)
    flow._config_entry.runtime_data.data = {}
    monkeypatch.setattr(flow, "async_abort", lambda **kwargs: kwargs)
    assert asyncio.run(flow.async_step_init()) == {"reason": "no_accounts"}


def test_tariff_forms_filter_restore_and_validate_account_choices(monkeypatch):
    names = ["ВОДООТВЕДЕНИЕ", "ХВ НА СОД-Е ОБЩ.ИМУЩ.", "ГВ НА СОИ (ТЕПЛОНОСИТ)", "ОТОПЛЕНИЕ"]
    flow = _options_flow(monkeypatch, {"waste_water_tariff": names[0], "hot_water_heating_tariff": names[3]})
    flow._config_entry.runtime_data.data["accounts"]["42"]["tariffs"] = {str(i): {"name": name} for i, name in enumerate(names)}
    flow._config_entry.runtime_data.data["accounts"]["43"] = {"tariffs": {"w": {"name": "ОТВЕД. СТОЧ. ВОД СОИ"}}}
    form = asyncio.run(flow.async_step_tariffs())
    selectors = {key.schema: value for key, value in form["data_schema"].schema.items()}
    assert [choice["value"] for choice in selectors["waste_water_tariff"].config["options"]] == ["", names[0]]
    assert [choice["value"] for choice in selectors["cold_water_tariff"].config["options"]] == ["", names[1]]
    assert [choice["value"] for choice in selectors["hot_water_tariff"].config["options"]] == ["", names[2], names[1]]
    assert _suggested(form)["waste_water_tariff"] == names[0]
    assert form["data_schema"]({}) == {"waste_water_tariff": "", "cold_water_tariff": "", "hot_water_tariff": ""}
    invalid = asyncio.run(flow.async_step_tariffs({"waste_water_tariff": "ОТВЕД. СТОЧ. ВОД СОИ"}))
    assert invalid["errors"] == {"base": "invalid_tariff"}
    selected = {"waste_water_tariff": names[0], "cold_water_tariff": names[1], "hot_water_tariff": names[2]}
    heating_form = asyncio.run(flow.async_step_tariffs(selected))
    assert heating_form["step_id"] == "rate_type_unset"
    assert list(key.schema for key in heating_form["data_schema"].schema) == ["hot_water_heating_tariff", "rate_type"]
    assert _suggested(heating_form)["hot_water_heating_tariff"] == names[3]
    invalid = asyncio.run(flow.async_step_rate_type({"hot_water_heating_tariff": "ГВ НА СОИ (ПОДОГРЕВ)", "rate_type": "none"}))
    assert invalid["errors"] == {"base": "invalid_tariff"}
    assert asyncio.run(flow.async_step_rate_type({"hot_water_heating_tariff": names[3], "rate_type": "manual"}))["step_id"] == "rate_manual"
    result = asyncio.run(flow.async_step_rate_manual({"manual_rate": "0.05"}))["data"]["account_options"]["42"]
    assert {key: result[key] for key in selected} == selected
    assert result["hot_water_heating_tariff"] == names[3]
    assert result["water_heating_rate"] == 0.05


@pytest.mark.parametrize("hot", ["", "ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ", "ГВ НА СОД-Е ОБЩ.ИМУЩ."])
def test_direct_or_empty_hot_tariff_skips_heating_and_preserves_rate(monkeypatch, hot):
    flow = _options_flow(monkeypatch, {"water_heating_rate": 0.05, "rate_type": "manual", "hot_water_heating_tariff": "ОТОПЛЕНИЕ"})
    flow._config_entry.runtime_data.data["accounts"]["42"]["tariffs"] = {"hot": {"name": hot}}
    result = asyncio.run(flow.async_step_tariffs({"hot_water_tariff": hot}))["data"]["account_options"]["42"]
    assert result["hot_water_tariff"] == hot
    assert result["waste_water_tariff"] == ""
    assert result["cold_water_tariff"] == ""
    assert result["water_heating_rate"] == 0.05
    assert result["hot_water_heating_tariff"] == "ОТОПЛЕНИЕ"


def test_tariff_selection_and_automatic_heating_rate_are_saved_together(monkeypatch):
    flow = _options_flow(monkeypatch)
    names = ["ВОДООТВЕДЕНИЕ", "ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ", "ОТОПЛЕНИЕ"]
    flow._config_entry.runtime_data.data["accounts"]["42"]["tariffs"] = {name: {"name": name} for name in names}
    form = asyncio.run(flow.async_step_tariffs({"waste_water_tariff": names[0], "cold_water_tariff": names[1], "hot_water_tariff": names[1]}))
    assert form["step_id"] == "rate_type_unset"
    auto = asyncio.run(flow.async_step_rate_type_unset({"hot_water_heating_tariff": names[2], "rate_type": "auto"}))
    assert auto["step_id"] == "rate_auto"
    preview = asyncio.run(flow.async_step_rate_auto({"heating_system": "closed_external", "building_features": "insulated_without_towel_rails"}))
    assert preview["step_id"] == "rate_confirm"
    result = asyncio.run(flow.async_step_rate_confirm({}))["data"]["account_options"]["42"]
    assert result["hot_water_heating_tariff"] == names[2]
    assert result["hot_water_tariff"] == names[1]
    assert result["water_heating_rate"] == 0.05775


def test_missing_saved_tariff_is_not_suggested_but_remains_saved_until_submit(monkeypatch):
    flow = _options_flow(monkeypatch, {"waste_water_tariff": "ВОДООТВЕДЕНИЕ", "hot_water_heating_tariff": "ОТОПЛЕНИЕ"})
    assert _suggested(asyncio.run(flow.async_step_tariffs()))["waste_water_tariff"] == ""
    assert _suggested(asyncio.run(flow.async_step_rate_type()))["hot_water_heating_tariff"] == ""
    assert flow._config_entry.options["account_options"]["42"]["waste_water_tariff"] == "ВОДООТВЕДЕНИЕ"


def test_options_none_and_existing_options(monkeypatch):
    """None clears the effective rate and discards unsupported options."""
    flow = _options_flow(monkeypatch, {"other": 42, "water_heating_rate": 1, "rate_type": "manual"})
    first = asyncio.run(flow.async_step_rate_type())
    assert first["step_id"] == "rate_type"
    assert _suggested(first)["rate_type"] == "manual"
    result = asyncio.run(flow.async_step_rate_type({"rate_type": "none"}))
    assert result["data"]["account_options"]["42"]["water_heating_rate"] is None
    assert "other" not in result["data"]["account_options"]["42"]


@pytest.mark.parametrize("rate", [0.06528, 0, 0.123456789])
def test_options_show_saved_rate_below_type(monkeypatch, rate):
    """Display the saved rate without rounding, including zero."""
    flow = _options_flow(monkeypatch, {"water_heating_rate": rate, "rate_type": "manual"})
    form = asyncio.run(flow.async_step_rate_type())
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
    form = asyncio.run(flow.async_step_rate_type())
    assert form["step_id"] == "rate_type_unset"
    for language, expected in (("ru", "Текущий норматив — не задан"), ("en", "Current rate — not set")):
        translation = json.loads(
            (Path(flow_module.__file__).parent / "translations" / f"{language}.json").read_text(encoding="utf-8")
        )
        assert translation["options"]["step"][form["step_id"]]["data_description"]["rate_type"] == expected
    result = asyncio.run(flow.async_step_rate_type_unset({"rate_type": "none"}))
    assert result["data"]["account_options"]["42"]["water_heating_rate"] is None


@pytest.mark.parametrize("system", SYSTEMS)
@pytest.mark.parametrize("features", BUILDING_FEATURES)
def test_auto_summary_and_selections_are_restored(monkeypatch, system, features):
    """Each auto combination displays the saved rate and restores its selections."""
    flow = _options_flow(monkeypatch, {
        "rate_type": "auto", "water_heating_rate": water_heating_rate(system, features),
        "heating_system": system, "building_features": features,
    })
    form = asyncio.run(flow.async_step_rate_type())
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
    form = asyncio.run(flow.async_step_rate_type())
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
    summary = asyncio.run(flow.async_step_rate_type())["description_placeholders"]
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
    assert result["data"]["account_options"]["42"]["water_heating_rate"] == 0.06277
    reopened = _options_flow(monkeypatch, result["data"]["account_options"]["42"])
    assert _suggested(asyncio.run(reopened.async_step_rate_type()))["rate_type"] == "manual"
    assert _suggested(asyncio.run(reopened.async_step_rate_type({"rate_type": "manual"})))["manual_rate"] == "0,06277"


@pytest.mark.parametrize("rate", [0.06528, 0.0, 0.123456789])
@pytest.mark.parametrize("old_manual", [None, "0.09"])
def test_options_manual_prefills_saved_auto_rate(monkeypatch, rate, old_manual):
    """The current auto rate takes precedence over any older manual value."""
    options = {"rate_type": "auto", "water_heating_rate": rate, "other": 42}
    if old_manual is not None:
        options["manual_rate"] = old_manual
    flow = _options_flow(monkeypatch, options)
    form = asyncio.run(flow.async_step_rate_type({"rate_type": "manual"}))
    assert form["step_id"] == "rate_manual"
    assert _suggested(form)["manual_rate"] == str(rate)
    assert options["rate_type"] == "auto"
    assert options.get("manual_rate") == old_manual

    invalid = asyncio.run(flow.async_step_rate_manual({"manual_rate": "bad"}))
    assert invalid["errors"] == {"base": "invalid_rate"}
    assert _suggested(invalid)["manual_rate"] == "bad"
    result = asyncio.run(flow.async_step_rate_manual({"manual_rate": str(rate)}))
    assert result["data"]["account_options"]["42"]["rate_type"] == "manual"
    assert result["data"]["account_options"]["42"]["water_heating_rate"] == rate
    assert "other" not in result["data"]["account_options"]["42"]


def test_options_auto_without_rate_does_not_prefill_manual(monkeypatch):
    flow = _options_flow(monkeypatch, {"rate_type": "auto", "water_heating_rate": None})
    form = asyncio.run(flow.async_step_rate_type({"rate_type": "manual"}))
    assert _suggested(form)["manual_rate"] is None


def test_options_auto_confirm_and_restore(monkeypatch):
    """Auto selection is saved only after displaying the calculated coefficient."""
    flow = _options_flow(monkeypatch)
    assert asyncio.run(flow.async_step_rate_type({"rate_type": "auto"}))["step_id"] == "rate_auto"
    selection = {"heating_system": "closed_external", "building_features": "insulated_without_towel_rails"}
    preview = asyncio.run(flow.async_step_rate_auto(selection))
    assert preview["step_id"] == "rate_confirm"
    assert preview["description_placeholders"]["rate"] == "0.05775"
    result = asyncio.run(flow.async_step_rate_confirm({}))
    assert result["data"]["account_options"]["42"]["water_heating_rate"] == 0.05775
    reopened = _options_flow(monkeypatch, result["data"]["account_options"]["42"])
    form = asyncio.run(reopened.async_step_rate_type({"rate_type": "auto"}))
    assert _suggested(form) == selection


def test_invalid_selections_do_not_save(monkeypatch):
    flow = _options_flow(monkeypatch)
    assert asyncio.run(flow.async_step_rate_type({"rate_type": "bad"}))["errors"] == {"base": "invalid_rate_type"}
    assert asyncio.run(flow.async_step_rate_auto({"heating_system": "bad", "building_features": "bad"}))["errors"] == {"base": "invalid_selection"}


def test_initial_setup_only_asks_for_credentials(monkeypatch):
    flow = ConfigFlow()
    flow.hass = None
    monkeypatch.setattr(flow_module, "validate_input", AsyncMock(return_value={"username": "user", "title": "user"}))
    monkeypatch.setattr(flow, "async_set_unique_id", AsyncMock())
    monkeypatch.setattr(flow, "_abort_if_unique_id_configured", MagicMock())
    monkeypatch.setattr(flow, "async_create_entry", lambda **kwargs: kwargs)
    result = asyncio.run(flow.async_step_user({"username": "user", "password": "secret"}))
    assert result == {"title": "user", "data": {"username": "user", "password": "secret"}}


def test_options_update_reloads_entry():
    reload = AsyncMock()
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_reload=reload))
    asyncio.run(_async_options_updated(hass, SimpleNamespace(entry_id="entry")))
    reload.assert_awaited_once_with("entry")


def test_sensor_setup_with_configured_rate(monkeypatch):
    """A configured heating rate does not prevent tariff sensor creation."""
    entry = SimpleNamespace(
        entry_id="entry", data={}, options={"account_options": {"42": {"water_heating_rate": 0.06528}}}
    )
    entry.runtime_data = SimpleNamespace(
        entry=entry,
        data={"accounts": {"42": {"address": "Адрес", "tariffs": {"water": {"name": "Water", "unit": "куб.м.", "rate_per_unit": 50.25}}}}},
    )
    add_entities = MagicMock()
    monkeypatch.setattr("custom_components.zkhnso.sensor.async_get_device_registry", lambda _: SimpleNamespace(async_get_device=lambda **_: None))
    asyncio.run(async_setup_entry(None, entry, add_entities))
    add_entities.assert_called_once()
    args, kwargs = add_entities.call_args
    assert kwargs == {"update_before_add": False}
    assert len(args[0]) == 4
    assert sum(isinstance(entity, ZKHCombinedColdWaterTariffSensor) for entity in args[0]) == 1
    assert sum(isinstance(entity, ZKHCombinedHotWaterTariffSensor) for entity in args[0]) == 1
    sensor = next(entity for entity in args[0] if isinstance(entity, ZKHTariffSensor))
    assert isinstance(sensor, ZKHTariffSensor)
    assert sensor.unique_id == "entry_account_42_tariff_water"
    assert sensor.native_value == 50.25
    hot = next(entity for entity in args[0] if isinstance(entity, ZKHCombinedHotWaterTariffSensor))
    assert hot._water_heating_rate == 0.06528
