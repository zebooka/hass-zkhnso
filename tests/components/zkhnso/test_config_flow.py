"""Tests for the ZKHNSO config flow input validation."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.zkhnso.api_client import ZKHAPIConnectionError
from custom_components.zkhnso.config_flow import (
    AccountParseError,
    CannotConnect,
    ConfigFlow,
    InvalidAuth,
    validate_input,
)


def test_validate_input_rejects_blank_credentials() -> None:
    """Whitespace-only credentials are rejected before making HTTP requests."""
    with pytest.raises(InvalidAuth):
        asyncio.run(
            validate_input(None, {"username": " ", "password": "password"})
        )

    with pytest.raises(InvalidAuth):
        asyncio.run(
            validate_input(None, {"username": "user", "password": " "})
        )


@pytest.mark.parametrize(
    ("login_result", "expected", "error_key"),
    [(False, InvalidAuth, "invalid_auth"),
     (ZKHAPIConnectionError("offline"), CannotConnect, "cannot_connect")],
)
def test_login_failure_classification(monkeypatch, login_result, expected, error_key) -> None:
    """The flow shows connection errors separately from bad credentials."""
    import custom_components.zkhnso.config_flow as flow_module

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def preflight(self):
            return {"token": "token"}

        async def login(self):
            if isinstance(login_result, Exception):
                raise login_result
            return login_result

    monkeypatch.setattr(flow_module, "ZKHAPIClient", lambda *_: Client())
    credentials = {"username": "user", "password": "password"}
    with pytest.raises(expected):
        asyncio.run(validate_input(None, credentials))

    flow = ConfigFlow()
    flow.hass = None
    monkeypatch.setattr(flow, "async_show_form", lambda **kwargs: kwargs)
    result = asyncio.run(flow.async_step_user(credentials))
    assert result["errors"] == {"base": error_key}


def _reconfigure_flow(monkeypatch, account_id=123, other_entries=(), options=None):
    """Set up a reconfiguration flow and a config entry manager double."""
    entry = SimpleNamespace(
        entry_id="entry-1",
        unique_id="old-user",
        options=options or {},
        data={"username": "old-user", "password": "old-secret", "account_id": account_id,
              "address": "Old address", "extra": "preserve"},
    )
    manager = SimpleNamespace(
        async_entries=MagicMock(return_value=[entry, *other_entries]),
        async_update_entry=MagicMock(return_value=True),
        async_schedule_reload=MagicMock(),
    )
    flow = ConfigFlow()
    flow.hass = SimpleNamespace(config_entries=manager)
    flow.context = {"source": "reconfigure"}
    monkeypatch.setattr(flow, "_get_reconfigure_entry", lambda: entry)
    monkeypatch.setattr(flow, "async_show_form", lambda **kwargs: kwargs)
    monkeypatch.setattr(flow, "async_abort", lambda **kwargs: kwargs)
    return flow, entry, manager


def test_reconfigure_form_does_not_expose_password(monkeypatch) -> None:
    """Offer the username without prefilling the secret."""
    flow, _, _ = _reconfigure_flow(monkeypatch)
    result = asyncio.run(flow.async_step_reconfigure())
    assert result["step_id"] == "reconfigure"
    fields = {key.schema: key for key in result["data_schema"].schema}
    assert fields["username"].description["suggested_value"] == "old-user"
    assert not fields["password"].description


def test_reconfigure_updates_existing_entry_and_reloads(monkeypatch) -> None:
    """Keep entry identity and unrelated data while updating login and unique ID."""
    import custom_components.zkhnso.config_flow as flow_module

    flow, entry, manager = _reconfigure_flow(monkeypatch)
    validate = AsyncMock(return_value={
        "username": "New-User", "account_id": 123,
        "address": "New address", "title": "№123 New address",
    })
    monkeypatch.setattr(flow_module, "validate_input", validate)
    result = asyncio.run(flow.async_step_reconfigure({"username": "New-User", "password": "new-secret"}))

    assert result["step_id"] == "rate_type"
    manager.async_update_entry.assert_not_called()
    manager.async_schedule_reload.assert_not_called()
    result = asyncio.run(flow.async_step_rate_type({"rate_type": "none"}))
    assert result == {"reason": "reconfigure_successful"}
    kwargs = manager.async_update_entry.call_args.kwargs
    assert kwargs["entry"] is entry
    assert kwargs["unique_id"] == "new-user"
    assert kwargs["title"] == "№123 New address"
    assert kwargs["data"] == {
        "username": "New-User", "password": "new-secret", "account_id": 123,
        "address": "New address", "extra": "preserve",
    }
    assert kwargs["options"] == {"rate_type": "none", "water_heating_rate": None}
    manager.async_schedule_reload.assert_called_once_with("entry-1")


def test_reconfigure_manual_prefills_saved_auto_rate(monkeypatch) -> None:
    """Reconfiguration suggests the active auto rate rather than an older manual rate."""
    import custom_components.zkhnso.config_flow as flow_module

    options = {"rate_type": "auto", "water_heating_rate": 0.06528, "manual_rate": "0.04"}
    flow, entry, manager = _reconfigure_flow(monkeypatch, options=options)
    monkeypatch.setattr(flow_module, "validate_input", AsyncMock(return_value={
        "username": "New-User", "account_id": 123, "address": "New address", "title": "Account",
    }))
    asyncio.run(flow.async_step_reconfigure({"username": "New-User", "password": "secret"}))
    form = asyncio.run(flow.async_step_rate_type({"rate_type": "manual"}))
    assert form["step_id"] == "rate_manual"
    field = next(key for key in form["data_schema"].schema if key.schema == "manual_rate")
    assert field.description["suggested_value"] == "0.06528"
    assert entry.options == options
    manager.async_update_entry.assert_not_called()
    result = asyncio.run(flow.async_step_rate_manual({"manual_rate": "0.06528"}))
    assert result == {"reason": "reconfigure_successful"}
    assert manager.async_update_entry.call_args.kwargs["options"]["water_heating_rate"] == 0.06528
    manager.async_schedule_reload.assert_called_once_with("entry-1")


@pytest.mark.parametrize("rate_type", ["none", "manual", "auto"])
def test_reconfigure_collects_and_saves_heating_options(monkeypatch, rate_type) -> None:
    """Reconfigure preserves existing options and saves only after the final step."""
    import custom_components.zkhnso.config_flow as flow_module

    existing_options = {
        "other": 42, "rate_type": "manual", "manual_rate": "0.04", "water_heating_rate": 0.04,
        "building_features": "insulated_without_towel_rails", "heating_system": "closed_external",
    }
    flow, entry, manager = _reconfigure_flow(monkeypatch, options=existing_options)
    monkeypatch.setattr(flow_module, "validate_input", AsyncMock(return_value={
        "username": "New-User", "account_id": 123, "address": "New address", "title": "Account",
    }))
    form = asyncio.run(flow.async_step_reconfigure({"username": "New-User", "password": "secret"}))
    assert form["step_id"] == "rate_type"
    assert next(key for key in form["data_schema"].schema if key.schema == "rate_type").description["suggested_value"] == "manual"
    assert entry.options == existing_options
    manager.async_update_entry.assert_not_called()

    result = asyncio.run(flow.async_step_rate_type({"rate_type": rate_type}))
    if rate_type == "manual":
        assert result["step_id"] == "rate_manual"
        assert next(key for key in result["data_schema"].schema if key.schema == "manual_rate").description["suggested_value"] == "0.04"
        invalid = asyncio.run(flow.async_step_rate_manual({"manual_rate": "invalid"}))
        assert invalid["errors"] == {"base": "invalid_rate"}
        manager.async_update_entry.assert_not_called()
        result = asyncio.run(flow.async_step_rate_manual({"manual_rate": "0,05"}))
    elif rate_type == "auto":
        assert result["step_id"] == "rate_auto"
        suggested = {key.schema: key.description["suggested_value"] for key in result["data_schema"].schema}
        assert suggested == {"heating_system": "closed_external", "building_features": "insulated_without_towel_rails"}
        invalid = asyncio.run(flow.async_step_rate_auto({"heating_system": "bad", "building_features": "bad"}))
        assert invalid["errors"] == {"base": "invalid_selection"}
        manager.async_update_entry.assert_not_called()
        result = asyncio.run(flow.async_step_rate_auto({
            "heating_system": "closed_external", "building_features": "insulated_without_towel_rails",
        }))
        assert result["step_id"] == "rate_confirm"
        assert result["description_placeholders"] == {"rate": "0.05775"}
        manager.async_update_entry.assert_not_called()
        manager.async_schedule_reload.assert_not_called()
        result = asyncio.run(flow.async_step_rate_confirm({}))

    assert result == {"reason": "reconfigure_successful"}
    manager.async_update_entry.assert_called_once()
    updated_options = manager.async_update_entry.call_args.kwargs["options"]
    assert updated_options["other"] == 42
    assert updated_options["rate_type"] == rate_type
    assert updated_options["water_heating_rate"] == {"none": None, "manual": 0.05, "auto": 0.05775}[rate_type]
    assert entry.options == existing_options
    manager.async_schedule_reload.assert_called_once_with("entry-1")


@pytest.mark.parametrize(
    ("failure", "error"),
    [(CannotConnect(), "cannot_connect"), (InvalidAuth(), "invalid_auth"),
     (AccountParseError(), "account_parse_error"), (RuntimeError(), "unknown")],
)
def test_reconfigure_validation_errors_leave_entry_unchanged(monkeypatch, failure, error) -> None:
    """A failed login must not update or reload the existing entry."""
    import custom_components.zkhnso.config_flow as flow_module

    flow, _, manager = _reconfigure_flow(monkeypatch)
    monkeypatch.setattr(flow_module, "validate_input", AsyncMock(side_effect=failure))
    result = asyncio.run(flow.async_step_reconfigure({"username": "user", "password": "pass"}))
    assert result["errors"] == {"base": error}
    manager.async_update_entry.assert_not_called()
    manager.async_schedule_reload.assert_not_called()


@pytest.mark.parametrize(
    ("account_id", "new_account_id", "other_entries", "error"),
    [
        (123, 456, (), "account_mismatch"),
        (None, 123, (), "account_id_missing"),
        (123, 123, (SimpleNamespace(entry_id="other", unique_id="new-user", data={}),), "already_configured"),
        (123, 123, (SimpleNamespace(entry_id="other", unique_id="legacy", data={"username": "NEW-USER"}),), "already_configured"),
    ],
)
def test_reconfigure_rejects_unsafe_changes(monkeypatch, account_id, new_account_id, other_entries, error) -> None:
    """Do not switch accounts or allow a duplicate username."""
    import custom_components.zkhnso.config_flow as flow_module

    flow, _, manager = _reconfigure_flow(monkeypatch, account_id, other_entries)
    monkeypatch.setattr(flow_module, "validate_input", AsyncMock(return_value={
        "username": "New-User", "account_id": new_account_id,
        "address": "Address", "title": "Account",
    }))
    result = asyncio.run(flow.async_step_reconfigure({"username": "New-User", "password": "pass"}))
    assert result["errors"] == {"base": error}
    manager.async_update_entry.assert_not_called()
    manager.async_schedule_reload.assert_not_called()
