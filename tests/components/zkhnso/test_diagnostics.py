"""Tests for ZKHNSO diagnostics redaction."""
import asyncio
from types import SimpleNamespace

from custom_components.zkhnso.diagnostics import async_get_config_entry_diagnostics


def test_diagnostics_redacts_personal_and_session_data() -> None:
    """Personal information and credentials are hidden in diagnostics."""
    secrets = {
        "username": "private-user",
        "password": "private-password",
        "JSESSIONID": "session-id",
        "FORM_TOKEN": "form-token",
        "loginToken": "login-token",
    }
    entry = SimpleNamespace(
        data={**secrets, "public": "visible"},
        options={},
        version=1,
    )

    result = asyncio.run(async_get_config_entry_diagnostics(None, entry))

    assert result["entry"]["data"] == {
        **{key: "**REDACTED**" for key in secrets},
        "public": "visible",
    }
    assert result["entry"]["options"] == {
    }
    assert result["entry"]["version"] == 1
