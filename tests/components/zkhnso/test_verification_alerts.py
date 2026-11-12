"""Tests for meter verification alerts and their persistent state."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.zkhnso.sensor import ZKHMeterSensor
from custom_components.zkhnso.verification_alerts import (
    EVENT_METER_VERIFICATION_ALERT,
    MeterVerificationAlerts,
)

TODAY = datetime(2026, 11, 12, 12)


def meter_sensor(days=29, *, account="42", key="123", archived=False):
    """Create a real sensor backed by coordinator data."""
    meter = {
        "name": "Вода",
        "serial_number": key,
        "next_verification_date": (TODAY.date() + timedelta(days=days)).isoformat(),
        "is_archived": archived,
    }
    coordinator = SimpleNamespace(
        entry=SimpleNamespace(entry_id="entry"),
        data={"accounts": {account: {"address": "Private address", "meters": {key: meter}}}},
    )
    sensor = ZKHMeterSensor(coordinator, key, meter, account)
    sensor.entity_id = f"sensor.meter_{account}_{key}"
    return sensor, meter


@pytest.mark.parametrize("days,archived,expected", [
    (31, False, False), (30, False, True), (29, False, True), (0, False, True),
    (-1, False, True), (-365, False, True), (5, True, False), (-1, True, False),
])
def test_alert_threshold_and_archive(monkeypatch, days, archived, expected):
    async def run():
        hass = SimpleNamespace(bus=SimpleNamespace(async_fire=MagicMock()))
        alerts = MeterVerificationAlerts(hass, "entry")
        alerts.store = SimpleNamespace(async_save=AsyncMock())
        sensor, _ = meter_sensor(days, archived=archived)
        await alerts.async_check(sensor)
        assert hass.bus.async_fire.call_count == int(expected)
        assert alerts.store.async_save.await_count == int(expected)
        if expected:
            assert hass.bus.async_fire.call_args.args == (
                EVENT_METER_VERIFICATION_ALERT,
                {
                    "entry_id": "entry", "entity_id": "sensor.meter_42_123",
                    "account_id": "42", "meter_id": "123", "serial_number": "123",
                    "name": "Вода", "next_verification_date": (TODAY.date() + timedelta(days=days)).isoformat(),
                    "days_left": days,
                },
            )

    monkeypatch.setattr("custom_components.zkhnso.verification_alerts.Store", MagicMock())
    monkeypatch.setattr("custom_components.zkhnso.verification_alerts.dt_util.now", lambda: TODAY)
    asyncio.run(run())


@pytest.mark.parametrize("value", [None, "", "invalid", "12.11.2026", 123])
def test_invalid_date_is_ignored(monkeypatch, value):
    async def run():
        hass = SimpleNamespace(bus=SimpleNamespace(async_fire=MagicMock()))
        alerts = MeterVerificationAlerts(hass, "entry")
        alerts.store = SimpleNamespace(async_save=AsyncMock())
        sensor, meter = meter_sensor()
        meter["next_verification_date"] = value
        await alerts.async_check(sensor)
        meter.pop("next_verification_date")
        await alerts.async_check(sensor)
        hass.bus.async_fire.assert_not_called()
        alerts.store.async_save.assert_not_awaited()

    monkeypatch.setattr("custom_components.zkhnso.verification_alerts.Store", MagicMock())
    monkeypatch.setattr("custom_components.zkhnso.verification_alerts.dt_util.now", lambda: TODAY)
    asyncio.run(run())


def test_alerts_persist_across_restart_and_dates(monkeypatch, tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        fired = MagicMock()
        first = MeterVerificationAlerts(hass, "entry")
        first.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=fired))
        await first.async_load()
        sensor, meter = meter_sensor()
        await asyncio.gather(first.async_check(sensor), first.async_check(sensor))
        assert fired.call_count == 1

        # A fresh Home Assistant and store load the persisted alert from disk.
        restarted_hass = HomeAssistant(str(tmp_path))
        restarted_fire = MagicMock()
        restarted = MeterVerificationAlerts(restarted_hass, "entry")
        restarted.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=restarted_fire))
        await restarted.async_load()
        monkeypatch.setattr("custom_components.zkhnso.verification_alerts.dt_util.now", lambda: TODAY + timedelta(days=1))
        await restarted.async_check(sensor)
        restarted_fire.assert_not_called()

        meter["next_verification_date"] = (TODAY.date() + timedelta(days=28)).isoformat()
        await restarted.async_check(sensor)
        assert restarted_fire.call_count == 1
        other, _ = meter_sensor(account="43")
        await restarted.async_check(other)
        assert restarted_fire.call_count == 2

    monkeypatch.setattr("custom_components.zkhnso.verification_alerts.dt_util.now", lambda: TODAY)
    asyncio.run(run())


def test_sensor_checks_on_add_and_coordinator_update(monkeypatch):
    async def run():
        sensor, _ = meter_sensor()
        sensor.coordinator.verification_alerts = SimpleNamespace(async_check=AsyncMock())
        sensor.hass = SimpleNamespace(async_create_task=asyncio.create_task)
        monkeypatch.setattr(CoordinatorEntity, "async_added_to_hass", AsyncMock())
        monkeypatch.setattr(CoordinatorEntity, "_handle_coordinator_update", MagicMock())
        await sensor.async_added_to_hass()
        sensor._handle_coordinator_update()
        await asyncio.sleep(0)
        assert sensor.coordinator.verification_alerts.async_check.await_count == 2
        sensor.coordinator.verification_alerts.async_check.assert_awaited_with(sensor)

    asyncio.run(run())
