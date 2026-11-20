"""Tests for event-driven meter submission."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant, callback

from custom_components.zkhnso import meter_submission as submission
from custom_components.zkhnso.api_client import ZKHAPIConnectionError
from custom_components.zkhnso.coordinator import ZKHDataUpdateCoordinator


class Client:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.current_account_id = "42"
        self.accounts = {"42": None, "43": None}
        self.preflight = AsyncMock(return_value=True)
        self.login = AsyncMock(return_value=True)
        self.get_active_meters = AsyncMock(return_value={})
        self.get_archived_meters = AsyncMock(return_value={})
        self.post_meters = AsyncMock()
        self.change_account = AsyncMock(side_effect=self.switch)

    async def switch(self, account_id):
        self.current_account_id = account_id

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def coordinator(entry_id, accounts):
    entry = SimpleNamespace(entry_id=entry_id, data={"username": entry_id, "password": "secret"}, async_on_unload=MagicMock())
    coord = SimpleNamespace(entry=entry, data={"accounts": accounts})
    coord.async_set_updated_data = MagicMock(side_effect=lambda data: setattr(coord, "data", data))
    coord.async_request_refresh = AsyncMock()
    return coord


def account(*meters):
    return {"meters": {meter["serial_number"]: meter for meter in meters}}


def meter(serial, archived=False, code="code:1"):
    return {"serial_number": serial, "is_archived": archived, "zkhnso_id": code}


def test_mixed_readings_are_filtered_for_event_account(monkeypatch):
    async def run():
        clients = []

        def create(*args):
            client = Client(*args)
            client.get_active_meters.return_value = account(meter("A-1"), meter("blocked", code=None))["meters"]
            clients.append(client)
            return client

        monkeypatch.setattr(submission, "ZKHAPIClient", create)
        manager = submission.MeterSubmission(SimpleNamespace(bus=SimpleNamespace(async_listen=MagicMock())))
        manager.coordinators["first"] = coordinator("first", {
            "42": account(meter("A-1"), meter("old", True), meter("blocked", code=None)),
            "43": account(meter("other", code="code:2")),
        })
        manager.coordinators["second"] = coordinator("second", {"44": account(meter("B", code="code:3"))})
        readings = [
            {"serial_number": "A-1", "value": 0, "date": "2026-02-28", "zkhnso_id": "forged"},
            {"account_id": "43", "serial_number": "other", "value": 12.5},
            {"serial_number": "B", "value": "10"},
            {"serial_number": "old", "value": 1},
            {"serial_number": "blocked", "value": 1},
            {"serial_number": "A-1"},
            {"serial_number": "A-1", "value": 1, "date": "2026-02-29"},
            {"serial_number": "A-1", "value": 1, "date": "2026-2-01"},
            None,
        ]
        await manager.async_handle_event(SimpleNamespace(data={"account_id": "42", "meters": readings}))
        assert len(clients) == 1
        assert clients[0].username == "first"
        clients[0].post_meters.assert_awaited_once_with({
            "counterCode[0]": "code:1", "counterDate[0]": "28.02.2026", "counterValue[0]": 0.0, "counterDiff[0]": 0.0,
        })
        clients[0].change_account.assert_not_awaited()
        assert clients[0].preflight.await_count == clients[0].login.await_count == 1

    asyncio.run(run())


def test_submission_uses_fresh_meters_and_previous_value(monkeypatch):
    async def run():
        client = Client("user", "password")
        client.get_active_meters.return_value = {
            "fresh": {"serial_number": "fresh", "zkhnso_id": "fresh:1", "value": 8},
            "blocked": {"serial_number": "blocked", "zkhnso_id": None, "value": 1},
        }
        monkeypatch.setattr(submission, "ZKHAPIClient", lambda *args: client)
        manager = submission.MeterSubmission(SimpleNamespace(bus=SimpleNamespace(async_listen=MagicMock())))
        manager.coordinators["first"] = coordinator("first", {"42": account(
            meter("old"), meter("blocked"), meter("fresh", code="stale:1"),
        )})
        await manager.async_handle_event(SimpleNamespace(data={"account_id": "42", "meters": [
            {"serial_number": "old", "value": 1},
            {"serial_number": "blocked", "value": 2},
            {"serial_number": "fresh", "value": 9, "date": "2026-02-28"},
        ]}))
        client.get_active_meters.assert_awaited_once()
        client.post_meters.assert_awaited_once_with({
            "counterCode[0]": "fresh:1", "counterDate[0]": "28.02.2026",
            "counterValue[0]": 9.0, "counterDiff[0]": 1.0,
        })
    asyncio.run(run())


def test_success_requests_refresh_after_post_and_session_close(monkeypatch):
    async def run():
        calls = []
        client = Client("user", "password")
        client.get_active_meters.return_value = account(meter("123"))["meters"]

        async def post(payload):
            calls.append("post")

        class ManagedClient:
            async def __aenter__(self):
                return client

            async def __aexit__(self, *args):
                calls.append("close")

        monkeypatch.setattr(submission, "ZKHAPIClient", lambda *args: ManagedClient())
        client.post_meters.side_effect = post
        manager = submission.MeterSubmission(SimpleNamespace(bus=SimpleNamespace(async_listen=MagicMock())))
        coord = coordinator("first", {"42": account(meter("123")), "43": {"tariffs": {"gas": 1}}})

        async def refresh():
            calls.append("refresh")

        coord.async_request_refresh.side_effect = refresh
        manager.coordinators["first"] = coord
        await manager.async_handle_event(SimpleNamespace(data={"account_id": "42", "meters": [{"serial_number": "123", "value": 1}]}))
        assert calls == ["post", "close", "refresh"]
        coord.async_request_refresh.assert_awaited_once()
        client.get_active_meters.assert_awaited_once()
        client.get_archived_meters.assert_not_awaited()
        coord.async_set_updated_data.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize("change", ["unload", "replace"])
def test_refresh_is_not_requested_after_unload_or_reload(monkeypatch, change):
    async def run():
        client = Client("user", "password")
        client.get_active_meters.return_value = account(meter("123"))["meters"]
        manager = submission.MeterSubmission(SimpleNamespace(bus=SimpleNamespace(async_listen=MagicMock())))
        coord = coordinator("first", {"42": account(meter("123"))})
        manager.coordinators["first"] = coord

        async def post(payload):
            if change == "unload":
                manager.coordinators.pop("first")
            else:
                manager.coordinators["first"] = coordinator("first", {"42": account(meter("123"))})

        client.post_meters.side_effect = post
        monkeypatch.setattr(submission, "ZKHAPIClient", lambda *args: client)
        await manager.async_handle_event(SimpleNamespace(data={"account_id": "42", "meters": [{"serial_number": "123", "value": 1}]}))
        client.post_meters.assert_awaited_once()
        coord.async_request_refresh.assert_not_awaited()
    asyncio.run(run())


def test_failed_post_does_not_request_refresh(monkeypatch):
    async def run():
        client = Client("user", "password")
        client.get_active_meters.return_value = account(meter("123"))["meters"]
        client.post_meters.side_effect = ZKHAPIConnectionError("post failed")
        monkeypatch.setattr(submission, "ZKHAPIClient", lambda *args: client)
        manager = submission.MeterSubmission(SimpleNamespace(bus=SimpleNamespace(async_listen=MagicMock())))
        coord = coordinator("first", {"42": account(meter("123"))})
        manager.coordinators["first"] = coord
        await manager.async_handle_event(SimpleNamespace(data={"account_id": "42", "meters": [{"serial_number": "123", "value": 1}]}))
        client.post_meters.assert_awaited_once()
        coord.async_request_refresh.assert_not_awaited()
    asyncio.run(run())


def test_refresh_failure_does_not_resubmit_readings(monkeypatch):
    async def run():
        client = Client("user", "password")
        client.get_active_meters.return_value = account(meter("123"))["meters"]
        monkeypatch.setattr(submission, "ZKHAPIClient", lambda *args: client)
        manager = submission.MeterSubmission(SimpleNamespace(bus=SimpleNamespace(async_listen=MagicMock())))
        coord = coordinator("first", {"42": account(meter("123"))})
        coord.async_request_refresh.side_effect = RuntimeError("refresh failed")
        manager.coordinators["first"] = coord
        await manager.async_handle_event(SimpleNamespace(data={"account_id": "42", "meters": [{"serial_number": "123", "value": 1}]}))
        client.post_meters.assert_awaited_once()
        coord.async_request_refresh.assert_awaited_once()
    asyncio.run(run())


def test_submission_refreshes_real_coordinator_and_notifies_listeners(monkeypatch, tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        entry = SimpleNamespace(entry_id="first", data={"username": "user", "password": "password"}, async_on_unload=MagicMock(), pref_disable_polling=True)
        coord = ZKHDataUpdateCoordinator(hass, entry)
        coord.data = {"accounts": {"42": account(meter("123"))}}
        updated = {"accounts": {"42": {"meters": {"123": {"value": 8}}}, "43": {"tariffs": {"water": 5}}}}
        coord._async_update_data = AsyncMock(return_value=updated)
        notifications = []

        @callback
        def listener():
            notifications.append(coord.data)

        remove = coord.async_add_listener(listener)
        client = Client("user", "password")
        client.get_active_meters.return_value = account(meter("123"))["meters"]
        monkeypatch.setattr(submission, "ZKHAPIClient", lambda *args: client)
        manager = submission.MeterSubmission(hass)
        manager.coordinators["first"] = coord
        await manager.async_handle_event(SimpleNamespace(data={"account_id": "42", "meters": [{"serial_number": "123", "value": 8}]}))
        client.post_meters.assert_awaited_once()
        coord._async_update_data.assert_awaited_once()
        assert coord.data == updated
        assert notifications == [updated]
        remove()
        manager.unsubscribe()
        await coord.async_shutdown()
    asyncio.run(run())


@pytest.mark.parametrize("result", [None, {}])
def test_empty_or_failed_active_meter_fetch_does_not_post(monkeypatch, result):
    async def run():
        client = Client("user", "password")
        client.get_active_meters.return_value = result
        monkeypatch.setattr(submission, "ZKHAPIClient", lambda *args: client)
        manager = submission.MeterSubmission(SimpleNamespace(bus=SimpleNamespace(async_listen=MagicMock())))
        manager.coordinators["first"] = coordinator("first", {"42": account(meter("123"))})
        await manager.async_handle_event(SimpleNamespace(data={"account_id": "42", "meters": [{"serial_number": "123", "value": 1}]}))
        client.get_active_meters.assert_awaited_once()
        client.post_meters.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("payload", [
    None, {}, {"account_id": "42", "meters": {}}, {"account_id": "42", "meters": []},
    {"meters": [{"account_id": "42", "serial_number": "serial", "value": 1}]},
    {"account_id": "", "meters": [{"serial_number": "serial", "value": 1}]},
    {"account_id": "99", "meters": [{"serial_number": "serial", "value": 1}]},
])
def test_invalid_or_empty_payload_does_not_open_client(monkeypatch, payload):
    async def run():
        factory = MagicMock()
        monkeypatch.setattr(submission, "ZKHAPIClient", factory)
        manager = submission.MeterSubmission(SimpleNamespace(bus=SimpleNamespace(async_listen=MagicMock())))
        manager.coordinators["first"] = coordinator("first", {"42": account(meter("serial"))})
        await manager.async_handle_event(SimpleNamespace(data=payload or {}))
        factory.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize("input_account,expected_account", [
    (42, "42"), (" 42 ", "42"), (" 0042 ", "0042"),
])
def test_account_id_is_normalized_before_lookup(monkeypatch, input_account, expected_account):
    async def run():
        client = Client("user", "password")
        client.current_account_id = expected_account
        client.accounts = {expected_account: None}
        client.get_active_meters.return_value = account(meter("serial"))["meters"]
        monkeypatch.setattr(submission, "ZKHAPIClient", lambda *args: client)
        manager = submission.MeterSubmission(SimpleNamespace(bus=SimpleNamespace(async_listen=MagicMock())))
        coord = coordinator("first", {expected_account: account(meter("serial"))})
        manager.coordinators["first"] = coord
        await manager.async_handle_event(SimpleNamespace(data={
            "account_id": input_account, "meters": [{"serial_number": "serial", "value": 1}],
        }))
        client.post_meters.assert_awaited_once()
        client.change_account.assert_not_awaited()
        coord.async_request_refresh.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize("reading", [
    {},
    {"serial_number": "123"},
    {"value": 1},
    None,
    "not a reading",
    {"serial_number": "123", "value": "invalid"},
])
def test_invalid_reading_is_rejected_without_post(monkeypatch, reading):
    async def run():
        client = Client("user", "password")
        client.get_active_meters.return_value = account(meter("123"))["meters"]
        monkeypatch.setattr(submission, "ZKHAPIClient", lambda *args: client)
        manager = submission.MeterSubmission(SimpleNamespace(bus=SimpleNamespace(async_listen=MagicMock())))
        manager.coordinators["first"] = coordinator("first", {"42": account(meter("123"))})
        await manager.async_handle_event(SimpleNamespace(data={"account_id": "42", "meters": [reading]}))
        client.get_active_meters.assert_awaited_once()
        client.post_meters.assert_not_awaited()
    asyncio.run(run())


def test_ambiguous_account_uses_first_entry(monkeypatch):
    async def run():
        clients = []

        def create(*args):
            client = Client(*args)
            client.get_active_meters.return_value = account(meter("123"))["meters"]
            clients.append(client)
            return client

        monkeypatch.setattr(submission, "ZKHAPIClient", create)
        manager = submission.MeterSubmission(SimpleNamespace(bus=SimpleNamespace(async_listen=MagicMock())))
        for name in ("first", "second"):
            manager.coordinators[name] = coordinator(name, {"42": account(meter("123"))})
        await manager.async_handle_event(SimpleNamespace(data={"account_id": "42", "meters": [{"serial_number": "123", "value": 1, "date": "2026-02-28"}]}))
        assert len(clients) == 1
        assert clients[0].username == "first"
        clients[0].post_meters.assert_awaited_once_with({
            "counterCode[0]": "code:1", "counterDate[0]": "28.02.2026", "counterValue[0]": 1.0, "counterDiff[0]": 1.0,
        })
    asyncio.run(run())


@pytest.mark.parametrize("account_id,username,serial,code", [
    ("43", "first", "123", "code:2"),
    ("44", "second", "456", "code:3"),
])
def test_account_routing_and_switch(monkeypatch, account_id, username, serial, code):
    async def run():
        clients = []

        def create(*args):
            client = Client(*args)
            client.accounts["44"] = None
            async def get_meters():
                assert client.current_account_id == account_id
                return account(meter(serial, code=code))["meters"]
            client.get_active_meters.side_effect = get_meters
            clients.append(client)
            return client

        monkeypatch.setattr(submission, "ZKHAPIClient", create)
        manager = submission.MeterSubmission(SimpleNamespace(bus=SimpleNamespace(async_listen=MagicMock())))
        manager.coordinators["first"] = coordinator("first", {"43": account(meter("123", code="code:2"))})
        manager.coordinators["second"] = coordinator("second", {"44": account(meter("456", code="code:3"))})
        await manager.async_handle_event(SimpleNamespace(data={
            "account_id": account_id, "meters": [{"serial_number": serial, "value": 10, "date": "2026-02-28"}],
        }))
        assert len(clients) == 1
        assert clients[0].username == username
        clients[0].change_account.assert_awaited_once_with(account_id)
        clients[0].post_meters.assert_awaited_once_with({
            "counterCode[0]": code, "counterDate[0]": "28.02.2026", "counterValue[0]": 10.0, "counterDiff[0]": 10.0,
        })
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["preflight", "login", "switch", "missing_account"])
def test_session_failures_do_not_post(monkeypatch, failure):
    async def run():
        client = Client("user", "password")
        if failure in ("preflight", "login"):
            getattr(client, failure).return_value = False
        elif failure == "switch":
            client.change_account.side_effect = ZKHAPIConnectionError("failure")
        else:
            client.accounts.pop("43")
        monkeypatch.setattr(submission, "ZKHAPIClient", lambda *args: client)
        manager = submission.MeterSubmission(SimpleNamespace(bus=SimpleNamespace(async_listen=MagicMock())))
        manager.coordinators["first"] = coordinator("first", {"43": account(meter("123"))})
        await manager.async_handle_event(SimpleNamespace(data={"account_id": "43", "meters": [{"serial_number": "123", "value": 1}]}))
        client.preflight.assert_awaited_once()
        if failure != "preflight":
            client.login.assert_awaited_once()
        if failure == "switch":
            client.change_account.assert_awaited_once_with("43")
        client.get_active_meters.assert_not_awaited()
        client.post_meters.assert_not_awaited()
    asyncio.run(run())


def test_event_bus_listener_and_unload(monkeypatch):
    async def run():
        hass = HomeAssistant("/config")
        client = Client("user", "password")
        client.get_active_meters.return_value = account(meter("123"))["meters"]
        monkeypatch.setattr(submission, "ZKHAPIClient", lambda *args: client)
        first = coordinator("first", {"42": account(meter("123"))})
        second = coordinator("second", {"43": account(meter("other"))})
        submission.async_register_meter_submission(hass, first)
        submission.async_register_meter_submission(hass, second)
        assert hass.bus.async_listeners()[submission.EVENT_SEND_METER] == 1
        hass.bus.async_fire(submission.EVENT_SEND_METER, {"account_id": "42", "meters": [{"serial_number": "123", "value": 1}]})
        await hass.async_block_till_done()
        client.post_meters.assert_awaited_once()
        first.entry.async_on_unload.call_args.args[0]()
        assert hass.bus.async_listeners()[submission.EVENT_SEND_METER] == 1
        second.entry.async_on_unload.call_args.args[0]()
        assert submission.EVENT_SEND_METER not in hass.bus.async_listeners()
        assert submission._DATA_KEY not in hass.data
    asyncio.run(run())


def test_listener_is_removed_after_last_entry_unloads():
    async def run():
        hass = HomeAssistant("/config")
        first = coordinator("first", {})
        second = coordinator("second", {})
        submission.async_register_meter_submission(hass, first)
        submission.async_register_meter_submission(hass, second)
        assert hass.bus.async_listeners()[submission.EVENT_SEND_METER] == 1
        first.entry.async_on_unload.call_args.args[0]()
        assert hass.bus.async_listeners()[submission.EVENT_SEND_METER] == 1
        second.entry.async_on_unload.call_args.args[0]()
        assert submission.EVENT_SEND_METER not in hass.bus.async_listeners()
        assert submission._DATA_KEY not in hass.data
    asyncio.run(run())


def test_integration_setup_registers_listener_after_refresh(monkeypatch):
    import custom_components.zkhnso as integration

    async def run():
        hass = SimpleNamespace(
            data={},
            bus=SimpleNamespace(async_listen=MagicMock(return_value=MagicMock())),
            config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock()),
        )
        entry = SimpleNamespace(entry_id="entry", async_on_unload=MagicMock(), add_update_listener=MagicMock())
        coord = SimpleNamespace(entry=entry, async_config_entry_first_refresh=AsyncMock())
        alerts = SimpleNamespace(async_load=AsyncMock())
        monkeypatch.setattr(integration, "ZKHDataUpdateCoordinator", lambda *args: coord)
        monkeypatch.setattr(integration, "MeterVerificationAlerts", lambda *args: alerts)
        assert await integration.async_setup_entry(hass, entry)
        alerts.async_load.assert_awaited_once()
        coord.async_config_entry_first_refresh.assert_awaited_once()
        assert entry.runtime_data is coord
        hass.bus.async_listen.assert_called_once()
        assert hass.bus.async_listen.call_args.args[0] == submission.EVENT_SEND_METER
    asyncio.run(run())
