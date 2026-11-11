"""Tests for the ZKHNSO data update coordinator."""
import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import web
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.zkhnso.api_client import ZKHAPIConnectionError
from custom_components.zkhnso.coordinator import ZKHDataUpdateCoordinator
from custom_components.zkhnso.sensor import ZKHMeterSensor, ZKHTariffSensor, async_setup_entry


class _Client:
    """Successful API client test double."""

    current_account_id = "42"
    accounts = {"42": "#42", "43": "#43"}

    def __init__(self):
        self.switched = []

    async def change_account(self, account_id):
        self.switched.append(account_id)
        self.current_account_id = account_id

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return None

    async def preflight(self):
        return {"JSESSIONID": "session", "FORM_TOKEN": "token"}

    async def login(self):
        return True

    async def get_address_info(self):
        return {
            "address": f"Адрес {self.current_account_id}",
            "company_name": f"УК {self.current_account_id}",
            "company_address": f"Адрес {self.current_account_id}/1"
        }

    async def get_meters(self):
        return {"serial": {"value": int(self.current_account_id)}}

    async def get_tariffs(self):
        return {"Water": {"rate_per_unit": int(self.current_account_id)}}


def test_http_session_fetches_distinct_accounts_and_creates_sensors(monkeypatch) -> None:
    """Exercise the actual client, parser, coordinator and sensor setup together."""
    from unittest.mock import MagicMock
    import custom_components.zkhnso.api_client as api_client

    async def run():
        current = "42"
        requests = []

        async def login_page(request):
            response = web.Response(text='<form id="loginForm"><input name="loginToken" value="token"></form>')
            response.set_cookie("JSESSIONID", "session")
            return response

        async def login(request):
            assert request.cookies.get("JSESSIONID") == "session"
            return web.Response(text='''<div class="header"><div class="account"><span>42</span>
                <div class="acc-select"><table class="accounts-grid">
                <tr><td><a href="/room/lk/main!change.action?account=42">ЛС: 42</a></td><td>Адрес 1</td></tr>
                <tr><td><a href="/room/lk/main!change.action?account=43">ЛС: 43</a></td><td>Адрес 2</td></tr>
                </table></div></div></div>''')

        async def switch(request):
            nonlocal current
            assert request.cookies.get("JSESSIONID") == "session"
            current = request.query["account"]
            requests.append(("switch", current))
            raise web.HTTPFound("main.action")

        async def address_info(request):
            assert request.cookies.get("JSESSIONID") == "session"
            requests.append(("address", current))
            html = ('<div class="header"><div class="address">Address 42</div></div>'
                    '<div class="main-content"><div class="contacts"><h2><span>Company 42</span>'
                    '<div class="text"><i>Address:</i>C42</div></div></div>'
                    if current == "42" else
                    '<div class="header"><div class="address">Address 43</div></div>'
                    '<div class="main-content"><div class="contacts"><h2><span>Company 43</span>'
                    '<div class="text"><i>Address:</i>C43</div></div></div>')
            return web.Response(text=html)

        async def counters(request):
            assert request.cookies.get("JSESSIONID") == "session"
            requests.append(("meters", current))
            row = ('<tr><td>Вода</td><td>111</td><td>куб.м.</td><td>01.01.2026</td><td>10</td><td>01.01.2030</td></tr>'
                   if current == "42" else
                   '<tr><td>Газ</td><td>222</td><td>куб.м.</td><td>01.01.2026</td><td>30</td><td>01.01.2030</td></tr>')
            return web.Response(text=f'<form id="countersForm"><table><tr><th>Счётчик</th></tr>{row}</table></form>')

        async def tariffs(request):
            assert request.cookies.get("JSESSIONID") == "session"
            requests.append(("tariffs", current))
            row = ('<tr><td>ВОДА</td><td>0</td><td>куб.м.</td><td>20</td><td>01.01.2026</td></tr>'
                   if current == "42" else
                   '<tr><td>ГАЗ</td><td>0</td><td>куб.м.</td><td>40</td><td>01.01.2026</td></tr>')
            return web.Response(text=f'<form id="tariffsForm"><table><tr><th>Тариф</th></tr>{row}</table></form>')

        app = web.Application()
        app.router.add_get('/room/lk/login.action', login_page)
        app.router.add_post('/room/lk/doLogin!enter.action', login)
        app.router.add_get('/room/lk/main!change.action', switch)
        app.router.add_get('/room/lk/main.action', address_info)
        app.router.add_get('/room/lk/counters.action', counters)
        app.router.add_get('/room/lk/tariffs.action', tariffs)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        try:
            port = site._server.sockets[0].getsockname()[1]
            monkeypatch.setattr(api_client, 'API_BASE_URL', f'http://localhost:{port}/room/lk/')
            coordinator = object.__new__(ZKHDataUpdateCoordinator)
            coordinator.entry = SimpleNamespace(entry_id='entry', data={'username': 'user', 'password': 'pass', 'account_id': 42})
            coordinator.data = await coordinator._async_update_data()
            coordinator.async_add_listener = lambda callback: lambda: None
            added = MagicMock()
            monkeypatch.setattr("custom_components.zkhnso.sensor.async_get_device_registry", lambda _: SimpleNamespace(async_get_device=lambda **_: None))
            await async_setup_entry(None, SimpleNamespace(entry_id="entry", runtime_data=coordinator, options={}, async_on_unload=MagicMock()), added)
            entities = added.call_args.args[0]
            meters = {(e._account_id, e.key): e.native_value for e in entities if isinstance(e, ZKHMeterSensor)}
            tariffs_by_account = {(e._account_id, e.key): e.native_value for e in entities if isinstance(e, ZKHTariffSensor)}
            assert meters == {('42', '111'): 10, ('43', '222'): 30}
            assert tariffs_by_account == {('42', 'ВОДА'): 20, ('43', 'ГАЗ'): 40}
            assert requests == [
                ('address', '42'),
                ('meters', '42'),
                ('tariffs', '42'),
                ('switch', '43'),
                ('address', '43'),
                ('meters', '43'),
                ('tariffs', '43')
            ]
        finally:
            await runner.cleanup()

    asyncio.run(run())


def test_coordinator_returns_meter_and_tariff_containers(monkeypatch) -> None:
    """The coordinator wraps parser results in the structure used by sensors."""
    import custom_components.zkhnso.coordinator as coordinator_module

    client = _Client()
    monkeypatch.setattr(coordinator_module, "ZKHAPIClient", lambda *_: client)
    coordinator = object.__new__(ZKHDataUpdateCoordinator)
    coordinator.entry = SimpleNamespace(data={"username": "user", "password": "pass"})

    assert asyncio.run(coordinator._async_update_data()) == {
        "accounts": {
            "42": {
                "account_id": "42",
                "address": "Адрес 42",
                "company_name": "УК 42",
                "company_address": "Адрес 42/1",
                "meters": {"serial": {"value": 42}},
                "tariffs": {"Water": {"rate_per_unit": 42}}
            },
            "43": {
                "account_id": "43",
                "address": "Адрес 43",
                "company_name": "УК 43",
                "company_address": "Адрес 43/1",
                "meters": {"serial": {"value": 43}},
                "tariffs": {"Water": {"rate_per_unit": 43}}
            },
        },
    }
    assert client.switched == ["43"]


def test_accounts_are_processed_sequentially_on_initial_and_repeated_refresh(monkeypatch) -> None:
    """A slow tariff response must finish before switching to another account."""
    import custom_components.zkhnso.coordinator as coordinator_module

    async def run():
        events = []
        tariff_started = asyncio.Event()
        release_tariff = asyncio.Event()

        class SequentialClient(_Client):
            accounts = {"42": "Адрес 1", "43": "Адрес 2", "44": "Адрес 3"}

            async def change_account(self, account_id):
                events.append(("switch", account_id))
                await super().change_account(account_id)

            async def get_meters(self):
                events.append(("meters", self.current_account_id))
                await asyncio.sleep(0)
                return await super().get_meters()

            async def get_tariffs(self):
                account_id = self.current_account_id
                events.append(("tariffs_start", account_id))
                if account_id == "42":
                    tariff_started.set()
                    await release_tariff.wait()
                await asyncio.sleep(0)
                events.append(("tariffs_end", self.current_account_id))
                return await super().get_tariffs()

        monkeypatch.setattr(coordinator_module, "ZKHAPIClient", lambda *_: SequentialClient())
        coordinator = object.__new__(ZKHDataUpdateCoordinator)
        coordinator.entry = SimpleNamespace(data={"username": "user", "password": "pass"})

        for _ in range(2):
            events.clear()
            tariff_started.clear()
            release_tariff.clear()
            update = asyncio.create_task(coordinator._async_update_data())
            try:
                await asyncio.wait_for(tariff_started.wait(), timeout=2)
                await asyncio.sleep(0)
                assert events == [("meters", "42"), ("tariffs_start", "42")]
                release_tariff.set()
                result = await asyncio.wait_for(update, timeout=2)
            finally:
                if not update.done():
                    update.cancel()
                    await asyncio.gather(update, return_exceptions=True)

            assert events == [
                ("meters", "42"), ("tariffs_start", "42"), ("tariffs_end", "42"),
                ("switch", "43"), ("meters", "43"), ("tariffs_start", "43"), ("tariffs_end", "43"),
                ("switch", "44"), ("meters", "44"), ("tariffs_start", "44"), ("tariffs_end", "44"),
            ]
            for account_id in ("42", "43", "44"):
                account = result["accounts"][account_id]
                assert account["meters"]["serial"]["value"] == int(account_id)
                assert account["tariffs"]["Water"]["rate_per_unit"] == int(account_id)

    asyncio.run(run())


def test_coordinator_fails_when_switch_fails(monkeypatch) -> None:
    import custom_components.zkhnso.coordinator as coordinator_module

    class FailedSwitchClient(_Client):
        async def change_account(self, account_id):
            raise ZKHAPIConnectionError("offline")

    monkeypatch.setattr(coordinator_module, "ZKHAPIClient", lambda *_: FailedSwitchClient())
    coordinator = object.__new__(ZKHDataUpdateCoordinator)
    coordinator.entry = SimpleNamespace(data={"username": "user", "password": "pass"})
    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())


def test_coordinator_uses_current_account_before_switching(monkeypatch) -> None:
    import custom_components.zkhnso.coordinator as coordinator_module

    client = _Client()
    client.current_account_id = "42"
    monkeypatch.setattr(coordinator_module, "ZKHAPIClient", lambda *_: client)
    coordinator = object.__new__(ZKHDataUpdateCoordinator)
    coordinator.entry = SimpleNamespace(data={"username": "user", "password": "pass"})
    data = asyncio.run(coordinator._async_update_data())
    assert data["accounts"]["43"]["meters"]["serial"]["value"] == 43
    assert data["accounts"]["42"]["meters"]["serial"]["value"] == 42
    assert client.switched == ["43"]


@pytest.mark.parametrize("failed_method", ["get_meters", "get_tariffs"])
def test_coordinator_rejects_partial_account_data(monkeypatch, failed_method) -> None:
    import custom_components.zkhnso.coordinator as coordinator_module

    client = _Client()
    async def fetch():
        return None if client.current_account_id == "43" else {}
    setattr(client, failed_method, fetch)
    monkeypatch.setattr(coordinator_module, "ZKHAPIClient", lambda *_: client)
    coordinator = object.__new__(ZKHDataUpdateCoordinator)
    coordinator.entry = SimpleNamespace(data={"username": "user", "password": "pass"})
    with pytest.raises(UpdateFailed):
        asyncio.run(coordinator._async_update_data())


@pytest.mark.parametrize(
    ("login_result", "expected"),
    [(False, ConfigEntryAuthFailed), (ZKHAPIConnectionError("offline"), UpdateFailed)],
)
def test_coordinator_classifies_login_failures(monkeypatch, login_result, expected) -> None:
    """Only rejected credentials initiate reauthentication."""
    import custom_components.zkhnso.coordinator as coordinator_module

    class FailedLoginClient(_Client):
        async def login(self):
            if isinstance(login_result, Exception):
                raise login_result
            return login_result

    monkeypatch.setattr(coordinator_module, "ZKHAPIClient", lambda *_: FailedLoginClient())
    coordinator = object.__new__(ZKHDataUpdateCoordinator)
    coordinator.entry = SimpleNamespace(data={"username": "user", "password": "pass"})
    with pytest.raises(expected):
        asyncio.run(coordinator._async_update_data())
