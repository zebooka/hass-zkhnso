"""Tests for ZKHNSO data parsing."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from aiohttp import web
from multidict import CIMultiDict
import pytest

from custom_components.zkhnso.api_client import ZKHAPIClient, ZKHAPIConnectionError


def _login_client(status=200, body='<div class="header"><div class="account"><span>42</span></div><div class="address">Адрес</div></div>', error=None):
    """Create a client with a controlled login response."""
    client = ZKHAPIClient("user", "password")
    client.jsessionid = "session"
    client.form_token = "token"
    response = SimpleNamespace(
        status=status,
        method="POST",
        headers=CIMultiDict(),
        url="https://example.test/login",
        text=AsyncMock(return_value=body, side_effect=error),
    )
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    request.__aexit__ = AsyncMock(return_value=None)
    client.session = SimpleNamespace(post=MagicMock(return_value=request), cookie_jar=MagicMock())
    return client


def test_login_distinguishes_accepted_and_rejected_credentials() -> None:
    """Only the returned login form represents rejected credentials."""
    assert asyncio.run(_login_client().login()) is True
    rejected = _login_client(body='<form id="loginForm"></form>')
    assert asyncio.run(rejected.login()) is False
    rejected.session.cookie_jar.update_cookies.assert_not_called()


def test_login_initializes_current_account_for_configuration() -> None:
    client = _login_client(body='''
        <div class="header"><div class="account"><span>123</span></div>
    ''')
    assert asyncio.run(client.login()) is True
    assert client.current_account_id == "123"
    assert client.accounts == {"123": None}


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
        <tr><td><a href="#42">ЛС: 42 </a></td><td>Адрес 1</td></tr>
        <tr><td><a href="#43"> ЛС:  43</a> </td><td> Адрес 2 </td></tr>
        <tr><td>broken</td></tr>
        </table></div></div><div class="address">Текущий адрес</div></div>
    """)
    assert asyncio.run(client.login()) is True
    assert client.current_account_id == "42"
    assert client.accounts == {"42": "#42", "43": "#43"}


@pytest.mark.parametrize("status,success", [
    (200, True),
    (204, True),
    (302, True),
    (304, True),
    (307, True),
    (400, False),
    (500, False),
    (503, False),
])
def test_change_account_requires_non_error_status_code(status, success) -> None:
    client = ZKHAPIClient("user", "password")
    client.jsessionid = "session"
    client.current_account_id = "42"
    client.accounts = {"42": None, "43": "/room/lk/main!change.action?account=987654"}
    response = SimpleNamespace(status=status)
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    request.__aexit__ = AsyncMock(return_value=None)
    client.session = SimpleNamespace(get=MagicMock(return_value=request))
    if success:
        asyncio.run(client.change_account("43"))
    else:
        with pytest.raises(ZKHAPIConnectionError):
            asyncio.run(client.change_account("43"))
    assert client.current_account_id == ("43" if success else "42")
    args, kwargs = client.session.get.call_args
    assert args[0] == "https://xn--f1aijeow.xn--p1ai/room/lk/main!change.action?account=987654"
    assert kwargs["allow_redirects"] is False


def test_account_numbers_keep_leading_zeroes() -> None:
    client = _login_client(body="""
        <div class="header"><div class="account"><span>0042</span>
        <div class="acc-select"><table class="accounts-grid">
        <tr><td>ЛС: 0042</td><td>Адрес</td></tr>
        <tr><td><a href="#43">ЛС: A-43</a></td><td>Другой адрес</td></tr>
        </table></div></div><div class="address">Адрес</div></div>
    """)
    assert asyncio.run(client.login())
    assert client.accounts == {"0042": None, "A-43": "#43"}


def test_get_info_extracts_account_and_company_details() -> None:
    client = ZKHAPIClient("user", "password")
    client._get_page = AsyncMock(return_value="""
        <div class="header"><div class="address"> Новосибирск,\n ул. Ленина </div></div>
        <div class="main-content"><div class="contacts">
          <h2><span> Управляющая\n компания </span></h2>
          <div class="text"> <i>Адрес</i> Офис,\n дом 1 <br><i>Обслуживающий участок:</i><a href="#">УЭ-3</a></div>
        </div></div>
    """)

    assert asyncio.run(client.get_address_info()) == {
        "address": "Новосибирск, ул. Ленина",
        "company": {"name": "Управляющая компания", "address": "Офис, дом 1"},
    }
    client._get_page.assert_awaited_once_with(
        "https://xn--f1aijeow.xn--p1ai/room/lk/main.action", "Address info"
    )


@pytest.mark.parametrize("html", [None, ""])
def test_get_info_returns_none_when_page_unavailable(html) -> None:
    client = ZKHAPIClient("user", "password")
    client._get_page = AsyncMock(return_value=html)
    assert asyncio.run(client.get_address_info()) is None
    client._get_page.assert_awaited_once()


@pytest.mark.parametrize(("html", "expected"), [
    ("<html><body>Empty page</body></html>",
     {"address": None, "company": {"name": None, "address": None}}),
    ("<div class='header'><div class='address'> Address </div></div>"
     "<div class='main-content'><div class='contacts'><h2> </h2></div></div>",
     {"address": "Address", "company": {"name": None, "address": None}}),
])
def test_get_info_missing_or_empty_fields(html, expected) -> None:
    client = ZKHAPIClient("user", "password")
    client._get_page = AsyncMock(return_value=html)
    assert asyncio.run(client.get_address_info()) == expected


@pytest.mark.parametrize(("status", "body"), [
    (500, "<html></html>"),
    (200, '<form id="loginForm"></form>'),
])
def test_get_info_returns_none_for_http_error_or_login_page(status, body) -> None:
    client = ZKHAPIClient("user", "password")
    client.jsessionid = "session"
    response = SimpleNamespace(status=status, url="https://example.test/main", text=AsyncMock(return_value=body))
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    request.__aexit__ = AsyncMock(return_value=None)
    client.session = SimpleNamespace(get=MagicMock(return_value=request))
    assert asyncio.run(client.get_address_info()) is None


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
    assert result["123"]["is_archived"] is False


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


def test_get_meters_parses_active_and_archived_tables() -> None:
    client = ZKHAPIClient("user", "password")
    active_html = '''
        <form id="countersForm">
          <table><tr><th>Active</th></tr>
            <tr><td>Вода</td><td>22-123</td><td>куб.м.</td><td>01.01.2026</td><td>12,34</td><td>02.01.2030</td></tr>
          </table>
        </form>
    '''
    archived_html = '''
        <form id="countersForm">
          <table><tr><th>Active</th></tr></table>
          <table><tr><th>Archived</th></tr>
            <tr><td>Вода</td><td>22-456</td><td>куб.м.</td><td>02.02.2025</td><td>00193.000</td><td>03.03.2029</td></tr>
          </table>
        </form>
    '''
    client._get_page = AsyncMock(side_effect=[active_html, archived_html])
    assert asyncio.run(client.get_meters()) == {
        "22_123": {"name": "Вода", "serial_number": "22-123", "unit": "куб.м.",
                   "date": "2026-01-01", "value": 12.34, "next_verification_date": "2030-01-02", "is_archived": False},
    }
    assert asyncio.run(client.get_archived_meters()) == {
        "22_456": {"name": "Вода", "serial_number": "22-456", "unit": "куб.м.",
                   "date": "2025-02-02", "value": 193.0, "next_verification_date": "2029-03-03", "is_archived": True},
    }
    assert client._get_page.await_args_list == [
        (("https://xn--f1aijeow.xn--p1ai/room/lk/counters.action", "Meters"), {}),
        (("https://xn--f1aijeow.xn--p1ai/room/lk/counters!mode.action?showOff=true", "Archived meters"), {}),
    ]


@pytest.fixture
def portal_counters_form() -> str:
    """Portal form with active input fields and archived verification tooltips."""
    return '''
    <form id="countersForm" action="/room/lk/counters.action" method="post">
      <div class="white-block rc7-w-w">
        <div class="tl"></div><div class="tr"></div><div class="br"></div><div class="bl"></div>
        <h2>Счётчики</h2>
        <table class="grid"><tbody>
          <tr><th>Услуга</th><th>Серийный номер счётчика</th><th>Ед. изм.</th>
              <th>Дата предыдущих показаний</th><th>Предыдущие показания</th>
              <th>Текущие показания</th><th>Дата текущих показаний</th><th>Расход</th>
              <th>Дата следующей поверки</th><th>История</th></tr>
          <tr><td class="count-serv">гор.вода</td><td>22-157073</td><td class="count-measure">куб.м.</td>
              <td>25.10.2026</td><td>172.000<span class="count-prev" style="display: none">00172.000</span></td>
              <td><input name="counterValue[0]" value=""><span class="count-type" style="display: none">1</span></td>
              <td><input name="counterDate[0]" value="12.11.2026"><button>...</button></td><td class="count-diff"></td>
              <input type="hidden" name="counterCode[0]" value="146047636464:1">
              <td>02.06.2028</td><td><a href="/room/lk/counterHistoryPopup.action?code=146047636464%3A1">История</a></td></tr>
          <tr><td class="count-serv">хол.вода</td><td>22-171694</td><td class="count-measure">куб.м.</td>
              <td>25.10.2026</td><td>532<span class="count-prev" style="display: none">00532</span></td>
              <td><input name="counterValue[1]" value=""><span class="count-type" style="display: none">1</span></td>
              <td><input name="counterDate[1]" value="12.11.2026"><button>...</button></td><td class="count-diff"></td>
              <input type="hidden" name="counterCode[1]" value="145951290085:1">
              <td>02.06.2028</td><td><a href="/room/lk/counterHistoryPopup.action?code=145951290085%3A1">История</a></td></tr>
        </tbody></table>
        <div class="left"><a href="javascript:;" onclick="hideOff()">Скрыть неактивные счётчики</a></div>
        <div class="right"><a href="javascript:;" onclick="save(); return false">Сохранить показания счётчиков</a></div>
        <div class="clear"></div><h2>Неактивные счётчики</h2>
        <table class="grid"><tbody>
          <tr><th>Услуга</th><th>Серийный номер счётчика</th><th>Ед. изм.</th>
              <th>Дата предыдущих показаний</th><th>Предыдущие показания</th><th>Дата следующей поверки</th><th>История</th></tr>
          <tr><td>гор.вода</td><td>1016061717404</td><td>куб.м.</td><td>05.10.2022</td><td>00193</td>
              <td><span class="tip tip-simple" original-title="Поверка прибора закончилась">06.10.2022</span></td>
              <td><a href="/room/lk/counterHistoryPopup.action?code=34493865929%3A1">История</a></td></tr>
          <tr><td>гор.вода</td><td>1010122602500</td><td>куб.м.</td><td>01.02.2017</td><td>00326.000</td>
              <td><span class="tip tip-simple" original-title="Поверка прибора закончилась">04.02.2017</span></td>
              <td><a href="/room/lk/counterHistoryPopup.action?code=6729926580%3A1">История</a></td></tr>
          <tr><td>хол.вода</td><td>1016061717404</td><td>куб.м.</td><td>04.10.2022</td><td>00681</td>
              <td><span class="tip tip-simple" original-title="Поверка прибора закончилась">05.10.2022</span></td>
              <td><a href="/room/lk/counterHistoryPopup.action?code=34493893562%3A1">История</a></td></tr>
          <tr><td>хол.вода</td><td>1010122599404</td><td>куб.м.</td><td>01.02.2017</td><td>00377.000</td>
              <td><span class="tip tip-simple" original-title="Поверка прибора закончилась">04.02.2017</span></td>
              <td><a href="/room/lk/counterHistoryPopup.action?code=6729928201%3A1">История</a></td></tr>
        </tbody></table><div class="clear"></div>
      </div>
    </form>
    '''


@pytest.fixture
def portal_meters(portal_counters_form) -> dict:
    from bs4 import BeautifulSoup

    client = ZKHAPIClient("user", "password")
    active_form = BeautifulSoup(portal_counters_form, "html.parser")
    active_form.select("#countersForm table")[1].decompose()
    client._get_page = AsyncMock(side_effect=[str(active_form), portal_counters_form])
    async def fetch():
        active = await client.get_meters()
        archived = await client.get_archived_meters()
        return {**active, **archived}
    return asyncio.run(fetch())


def test_get_meters_with_portal_form(portal_meters) -> None:
    assert portal_meters == {
        "22_157073": {"name": "гор.вода", "serial_number": "22-157073", "unit": "куб.м.",
                      "date": "2026-10-25", "value": 172.0, "next_verification_date": "2028-06-02", "is_archived": False},
        "22_171694": {"name": "хол.вода", "serial_number": "22-171694", "unit": "куб.м.",
                      "date": "2026-10-25", "value": 532.0, "next_verification_date": "2028-06-02", "is_archived": False},
        "1016061717404": {"name": "хол.вода", "serial_number": "1016061717404", "unit": "куб.м.",
                          "date": "2022-10-04", "value": 681.0, "next_verification_date": "2022-10-05", "is_archived": True},
        "1010122602500": {"name": "гор.вода", "serial_number": "1010122602500", "unit": "куб.м.",
                          "date": "2017-02-01", "value": 326.0, "next_verification_date": "2017-02-04", "is_archived": True},
        "1010122599404": {"name": "хол.вода", "serial_number": "1010122599404", "unit": "куб.м.",
                          "date": "2017-02-01", "value": 377.0, "next_verification_date": "2017-02-04", "is_archived": True},
    }


@pytest.mark.xfail(strict=True, reason="Счётчики с одинаковым серийным номером перезаписывают друг друга")
def test_get_meters_preserves_archived_meters_with_duplicate_serial(portal_meters) -> None:
    duplicates = [meter for meter in portal_meters.values() if meter["serial_number"] == "1016061717404"]
    assert {(meter["name"], meter["value"], meter["date"], meter["next_verification_date"]) for meter in duplicates} == {
        ("гор.вода", 193.0, "2022-10-05", "2022-10-06"),
        ("хол.вода", 681.0, "2022-10-04", "2022-10-05"),
    }


@pytest.mark.parametrize("html,expected", [
    (None, None),
    ("", None),
    ('<form id="countersForm"><table><tr><th>Active</th></tr></table></form>', {}),
    ('<form id="countersForm"></form>', {}),
])
def test_get_meters_handles_missing_or_empty_tables(html, expected) -> None:
    client = ZKHAPIClient("user", "password")
    client._get_page = AsyncMock(return_value=html)
    assert asyncio.run(client.get_meters()) == expected


@pytest.mark.parametrize("html,expected", [
    (None, None),
    ("", None),
    ('<form id="countersForm"><table><tr><th>Active</th></tr></table></form>', {}),
    ('<form id="countersForm"><table><tr><th>Active</th></tr></table>'
     '<table><tr><th>Archive</th></tr><tr><td>Water</td><td>1</td><td>м³</td>'
     '<td>01.01.2026</td><td>10</td><td>01.01.2030</td></tr></table></form>',
     {"1": {"name": "Water", "serial_number": "1", "unit": "м³", "date": "2026-01-01",
            "value": 10.0, "next_verification_date": "2030-01-01", "is_archived": True}}),
])
def test_get_archived_meters_handles_missing_or_empty_tables(html, expected) -> None:
    client = ZKHAPIClient("user", "password")
    client._get_page = AsyncMock(return_value=html)
    assert asyncio.run(client.get_archived_meters()) == expected


def test_process_archive_meters_skips_incomplete_rows() -> None:
    client = ZKHAPIClient("user", "password")
    assert client._process_archive_meters_data([]) == {}
    result = client._process_archive_meters_data([
        ["header"], ["incomplete"], ["Water", "42", "м³", "01.01.2026", "1", "02.01.2030"]
    ])
    assert list(result) == ["42"]
    assert result["42"]["is_archived"] is True


def test_session_cookie_jar_handles_login_rotation_and_client_isolation(monkeypatch) -> None:
    """Real HTTP requests use isolated jars and respect server cookie updates."""
    import custom_components.zkhnso.api_client as api_client
    from yarl import URL

    async def run():
        received = []

        async def preflight(request):
            response = web.Response(text='<form id="loginForm"><input name="loginToken" value="token"></form>')
            response.set_cookie("JSESSIONID", "preflight", path="/")
            return response

        async def login(request):
            form = await request.post()
            received.append(("login", form["userName"], dict(request.cookies)))
            response = web.Response(text='''<div class="header"><div class="account"><span>42</span>
                <div class="acc-select"><table class="accounts-grid"><tr>
                <td><a href="/room/lk/main!change.action?account=43">ЛС: 43</a></td><td>Адрес</td>
                </tr></table></div></div></div>''')
            response.set_cookie("JSESSIONID", f"login-{form['userName']}", path="/")
            return response

        async def address(request):
            received.append(("address", None, dict(request.cookies)))
            response = web.Response(text='<div class="header"><div class="address">Адрес</div></div>')
            response.set_cookie("JSESSIONID", f"rotated-{request.cookies['userLogin']}", path="/")
            return response

        async def switch(request):
            received.append(("switch", None, dict(request.cookies)))
            raise web.HTTPFound("main.action")

        app = web.Application()
        app.router.add_get("/room/lk/login.action", preflight)
        app.router.add_post("/room/lk/doLogin!enter.action", login)
        app.router.add_get("/room/lk/main.action", address)
        app.router.add_get("/room/lk/main!change.action", switch)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        try:
            port = site._server.sockets[0].getsockname()[1]
            base_url = f"http://localhost:{port}/room/lk/"
            monkeypatch.setattr(api_client, "API_BASE_URL", base_url)
            async with ZKHAPIClient("first", "password") as first, ZKHAPIClient("second", "password") as second:
                for client in (first, second):
                    assert await client.preflight()
                    assert await client.login()
                for client in (first, second):
                    assert (await client.get_address_info())["address"] == "Адрес"
                    await client.change_account("43")
                    other_path_cookies = client.session.cookie_jar.filter_cookies(URL(f"http://localhost:{port}/other"))
                    assert "userLogin" not in other_path_cookies
                    assert "loginModule" not in other_path_cookies
                    assert not client.session.cookie_jar.filter_cookies(URL(f"http://other.example:{port}/room/lk/"))

            assert received == [
                ("login", "first", {"JSESSIONID": "preflight"}),
                ("login", "second", {"JSESSIONID": "preflight"}),
                ("address", None, {"JSESSIONID": "login-first", "userLogin": "first", "loginModule": "lk"}),
                ("switch", None, {"JSESSIONID": "rotated-first", "userLogin": "first", "loginModule": "lk"}),
                ("address", None, {"JSESSIONID": "login-second", "userLogin": "second", "loginModule": "lk"}),
                ("switch", None, {"JSESSIONID": "rotated-second", "userLogin": "second", "loginModule": "lk"}),
            ]
            assert first.session is None
            assert second.session is None
        finally:
            await runner.cleanup()

    asyncio.run(run())


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
    assert client._parse_float("00193.000") == 193
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
