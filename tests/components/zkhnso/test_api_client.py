"""Tests for ZKHNSO data parsing."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from multidict import CIMultiDict
import pytest

from custom_components.zkhnso.api_client import ZKHAPIClient, ZKHAPIConnectionError


def _login_client(status=200, body="<html>Account</html>", error=None):
    """Create a client with a controlled login response."""
    client = ZKHAPIClient("user", "password")
    client.jsessionid = "session"
    client.form_token = "token"
    response = SimpleNamespace(
        status=status,
        headers=CIMultiDict(),
        url="https://example.test/login",
        text=AsyncMock(return_value=body, side_effect=error),
    )
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    request.__aexit__ = AsyncMock(return_value=None)
    client.session = SimpleNamespace(post=MagicMock(return_value=request))
    return client


def test_login_distinguishes_accepted_and_rejected_credentials() -> None:
    """Only the returned login form represents rejected credentials."""
    assert asyncio.run(_login_client().login()) is True
    assert asyncio.run(_login_client(body='<form id="loginForm"></form>').login()) is False


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_login_http_error_is_connection_error(status) -> None:
    """HTTP errors are not interpreted as bad credentials, even with a login form."""
    with pytest.raises(ZKHAPIConnectionError, match=f"HTTP status {status}"):
        asyncio.run(_login_client(status=status, body='<form id="loginForm"></form>').login())


@pytest.mark.parametrize("error", [TimeoutError(), aiohttp.ClientError(), ValueError()])
def test_login_request_errors_are_connection_errors(error) -> None:
    """Timeouts, client errors and unexpected errors remain connection failures."""
    with pytest.raises(ZKHAPIConnectionError) as exc_info:
        asyncio.run(_login_client(error=error).login())
    assert exc_info.value.__cause__ is error


def test_login_without_preflight_is_connection_error() -> None:
    """A missing session is not a credentials rejection."""
    with pytest.raises(ZKHAPIConnectionError):
        asyncio.run(ZKHAPIClient("user", "password").login())


def test_login_extracts_accounts_and_current_selection() -> None:
    client = _login_client(body="""
        <div class="header"><div class="account"><span> 42 </span>
        <div class="acc-select"><table class="accounts-grid">
        <tr><td> ЛС: 42 </td><td>Адрес 1</td></tr>
        <tr><td> ЛС:  43 </td><td> Адрес 2 </td></tr>
        <tr><td>broken</td></tr>
        </table></div></div><div class="address">Текущий адрес</div></div>
    """)
    assert asyncio.run(client.login()) is True
    assert client.current_account_id == 42
    assert client.current_address == "Текущий адрес"
    assert client.accounts == {"42": "Текущий адрес", "43": "Адрес 2"}


def test_account_numbers_keep_leading_zeroes() -> None:
    client = _login_client(body="""
        <div class="header"><div class="account"><span>42</span>
        <div class="acc-select"><table class="accounts-grid">
        <tr><td>ЛС: 0042</td><td>Адрес</td></tr>
        <tr><td>ЛС: A-43</td><td>Другой адрес</td></tr>
        </table></div></div><div class="address">Адрес</div></div>
    """)
    assert asyncio.run(client.login())
    assert client.accounts == {"0042": "Адрес", "A-43": "Другой адрес"}


@pytest.mark.parametrize("status,location,success", [
    (302, "main.action", True),
    (302, "/room/lk/main.action", True),
    (302, "login.action", False),
    (302, "https://other.example/room/lk/main.action", False),
    (200, "main.action", False),
    (302, "", False),
])
def test_change_account_requires_main_redirect(status, location, success) -> None:
    client = ZKHAPIClient("user", "password")
    client.jsessionid = "session"
    response = SimpleNamespace(status=status, headers={"Location": location})
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    request.__aexit__ = AsyncMock(return_value=None)
    client.session = SimpleNamespace(get=MagicMock(return_value=request))
    if success:
        asyncio.run(client.change_account("43"))
    else:
        with pytest.raises(ZKHAPIConnectionError):
            asyncio.run(client.change_account("43"))
    args, kwargs = client.session.get.call_args
    assert args[0].endswith("main!change.action?account=43")
    assert kwargs["allow_redirects"] is False


def test_process_meters_preserves_fractional_reading() -> None:
    """Meter values retain fractional readings from the portal."""
    client = ZKHAPIClient("user", "password")

    result = client._process_meters_data(
        [
            ["header"],
            ["Water", "123", "куб.м.", "01.01.2026", "12,34", "", "", "", "01.01.2030"],
        ]
    )

    assert result["123"]["value"] == 12.34


def test_process_meters_supports_both_counter_row_formats() -> None:
    """The verification date location depends on whether a reading was sent today."""
    client = ZKHAPIClient("user", "password")

    result = client._process_meters_data(
        [
            ["header"],
            [
                "гор.вода",
                "26_16185541",
                "куб.м.",
                "21.10.2026",
                "0.016",
                "",
                "",
                "",
                "02.06.2028",
                "История",
            ],
            [
                "гор.вода",
                "22-157073",
                "куб.м.",
                "25.10.2026",
                "172.000",
                "02.06.2028",
                "История",
            ],
        ]
    )

    assert result["26_16185541"]["next_verification_date"] == "2028-06-02"
    assert result["22_157073"]["next_verification_date"] == "2028-06-02"


def test_authenticated_cookies_exclude_session_cookie() -> None:
    """The cookie jar, rather than manual headers, manages JSESSIONID."""
    client = ZKHAPIClient("user", "password")
    client.jsessionid = "session-id"

    assert client._get_authenticated_cookies() == {
        "userLogin": "user",
        "loginModule": "lk",
    }


def test_process_tariffs_numbers_duplicate_names() -> None:
    """Duplicate tariff names receive stable numbered keys and names."""
    client = ZKHAPIClient("user", "password")

    result = client._process_tariffs_data(
        [
            ["header"],
            ["А", "1", "кв.м.", "2", "01.01.2026"],
            ["Б", "3", "куб.м.", "4", "01.01.2026"],
            ["Б", "5", "кВтч", "6", "01.01.2026"],
        ]
    )

    assert list(result) == ["А", "Б №1", "Б №2"]
    assert result["Б №1"]["name"] == "Б №1"
    assert result["Б №2"]["name"] == "Б №2"


def test_api_client_parsing_helpers() -> None:
    """Numeric, date, serial, cookie, and login-page helper behavior is stable."""
    client = ZKHAPIClient("user", "password")

    assert client._parse_float("1\u00a0234,56") == 1234.56
    assert client._parse_float("") is None
    assert client._parse_float("invalid") is None
    assert client._parse_date("31.12.2026") == "2026-12-31"
    assert client._parse_date("invalid") is None
    assert client._sanitize_serial_number("AB-12/34") == "___12_34"
    assert client._extract_jsessionid(
        CIMultiDict({"Set-Cookie": "JSESSIONID=session; Path=/"})
    ) == "session"
    assert client._extract_jsessionid(CIMultiDict()) is None
    assert client._is_login_page('<form id="loginForm"></form>')
    assert client._is_login_page('<input name="loginToken">')
    assert not client._is_login_page("<html><body>Account</body></html>")


def test_process_data_skips_invalid_rows() -> None:
    """Parsers retain valid rows and ignore incomplete input rows."""
    client = ZKHAPIClient("user", "password")

    meters = client._process_meters_data(
        [["header"], ["incomplete"], ["Water", "42", "куб.м.", "01.01.2026", "1", "", "", "", "02.01.2030"]]
    )
    tariffs = client._process_tariffs_data(
        [["header"], ["incomplete"], ["Water", "1", "куб.м.", "2", "01.01.2026"]]
    )

    assert list(meters) == ["42"]
    assert list(tariffs) == ["Water"]


def test_empty_tables_produce_empty_data() -> None:
    client = ZKHAPIClient("user", "password")
    assert client._process_meters_data([]) == {}
    assert client._process_tariffs_data([]) == {}
