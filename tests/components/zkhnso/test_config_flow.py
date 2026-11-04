"""Tests for the ZKHNSO config flow input validation."""
import asyncio

import pytest

from custom_components.zkhnso.api_client import ZKHAPIConnectionError
from custom_components.zkhnso.config_flow import CannotConnect, ConfigFlow, InvalidAuth, validate_input


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
