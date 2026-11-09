"""Tests for the ZKHNSO data update coordinator."""
import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.zkhnso.api_client import ZKHAPIConnectionError
from custom_components.zkhnso.coordinator import ZKHDataUpdateCoordinator


class _Client:
    """Successful API client test double."""

    current_account_id = "42"
    accounts = {"42": "Адрес 1", "43": "Адрес 2"}

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

    async def get_meters(self):
        return {"serial": {"value": int(self.current_account_id)}}

    async def get_tariffs(self):
        return {"Water": {"rate_per_unit": int(self.current_account_id)}}


def test_coordinator_returns_meter_and_tariff_containers(monkeypatch) -> None:
    """The coordinator wraps parser results in the structure used by sensors."""
    import custom_components.zkhnso.coordinator as coordinator_module

    client = _Client()
    monkeypatch.setattr(coordinator_module, "ZKHAPIClient", lambda *_: client)
    coordinator = object.__new__(ZKHDataUpdateCoordinator)
    coordinator.entry = SimpleNamespace(data={"username": "user", "password": "pass"})

    assert asyncio.run(coordinator._async_update_data()) == {
        "accounts": {
            "42": {"account_id": "42", "address": "Адрес 1", "meters": {"serial": {"value": 42}}, "tariffs": {"Water": {"rate_per_unit": 42}}},
            "43": {"account_id": "43", "address": "Адрес 2", "meters": {"serial": {"value": 43}}, "tariffs": {"Water": {"rate_per_unit": 43}}},
        },
    }
    assert client.switched == ["43"]


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
