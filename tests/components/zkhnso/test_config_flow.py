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


def test_reconfigure_updates_credentials_and_preserves_options(monkeypatch) -> None:
    import custom_components.zkhnso.config_flow as flow_module

    options = {"account_options": {"123": {"water_heating_rate": 0.05}}}
    flow, entry, manager = _reconfigure_flow(monkeypatch, options=options)
    monkeypatch.setattr(flow_module, "validate_input", AsyncMock(return_value={"username": "New-User", "title": "New-User"}))
    result = asyncio.run(flow.async_step_reconfigure({"username": "New-User", "password": "new-secret"}))
    assert result == {"reason": "reconfigure_successful"}
    kwargs = manager.async_update_entry.call_args.kwargs
    assert kwargs["unique_id"] == "new-user"
    assert kwargs["title"] == "New-User"
    assert kwargs["data"]["username"] == "New-User"
    assert kwargs["data"]["password"] == "new-secret"
    assert kwargs["data"]["account_id"] == 123
    assert entry.options == options
    manager.async_schedule_reload.assert_called_once_with("entry-1")


def test_reauth_updates_credentials_preserving_legacy_data(monkeypatch) -> None:
    import custom_components.zkhnso.config_flow as flow_module

    entry = SimpleNamespace(entry_id="entry", data={"username": "old", "account_id": 123})
    manager = SimpleNamespace(async_update_entry=MagicMock(), async_reload=AsyncMock())
    flow = ConfigFlow()
    flow.hass = SimpleNamespace(config_entries=manager)
    monkeypatch.setattr(flow, "_get_reauth_entry", lambda: entry)
    monkeypatch.setattr(flow, "async_abort", lambda **kwargs: kwargs)
    monkeypatch.setattr(flow_module, "validate_input", AsyncMock(return_value={"username": "user"}))
    result = asyncio.run(flow.async_step_reauth_confirm({"username": "user", "password": "pass"}))
    assert result == {"reason": "reauth_successful"}
    assert manager.async_update_entry.call_args.kwargs["data"] == {"username": "user", "password": "pass", "account_id": 123}
    manager.async_reload.assert_awaited_once_with("entry")


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


@pytest.mark.parametrize("other", [
    SimpleNamespace(entry_id="other", unique_id="new-user", data={}),
    SimpleNamespace(entry_id="other", unique_id="legacy", data={"username": "NEW-USER"}),
])
def test_reconfigure_rejects_duplicate_username(monkeypatch, other) -> None:
    import custom_components.zkhnso.config_flow as flow_module

    flow, _, manager = _reconfigure_flow(monkeypatch, other_entries=(other,))
    monkeypatch.setattr(flow_module, "validate_input", AsyncMock(return_value={"username": "New-User"}))
    result = asyncio.run(flow.async_step_reconfigure({"username": "New-User", "password": "pass"}))
    assert result["errors"] == {"base": "already_configured"}
    manager.async_update_entry.assert_not_called()
