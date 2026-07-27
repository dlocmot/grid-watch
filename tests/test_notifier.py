from datetime import datetime, timezone

import pytest

from grid_watch.config import Config
from grid_watch.models import (BATTERY_CRITICAL, GRID_DOWN, GRID_RESTORED,
                               Event, MONITOR_BLIND)
from grid_watch.notifier import DeliveryError, NtfySink, format_message

CFG = Config.load(None, {"GROWATT_USER": "u", "GROWATT_PASSWORD": "p",
                         "NTFY_TOPIC": "secret-topic"})
NOW = datetime(2026, 7, 26, 15, 0, tzinfo=timezone.utc)


def ev(kind, detail):
    return Event(kind=kind, event_id=f"{kind}@x", created_at=NOW, detail=detail)


def test_grid_down_is_urgent():
    title, body, priority, tags = format_message(
        ev(GRID_DOWN, {"grid_v": 0.0, "bat_soc": 87.0, "load_power": 900.0,
                       "pv_power": 1200.0}), CFG, now=NOW)
    assert priority == 5
    assert "87" in body and "900" in body


def test_grid_restored_is_normal_and_shows_duration():
    title, body, priority, tags = format_message(
        ev(GRID_RESTORED, {"outage_minutes": 42, "bat_soc": 61.0}), CFG, now=NOW)
    assert priority == 3
    assert "42" in body


def test_battery_critical_is_urgent():
    _, _, priority, _ = format_message(
        ev(BATTERY_CRITICAL, {"bat_soc": 18.0, "load_power": 800.0,
                              "outage_minutes": 90}), CFG, now=NOW)
    assert priority == 5


def test_monitor_blind_is_high():
    _, _, priority, _ = format_message(
        ev(MONITOR_BLIND, {"blind_minutes": 65, "error": "timeout"}), CFG, now=NOW)
    assert priority == 4


def test_delayed_event_declares_the_delay():
    old = Event(kind=GRID_DOWN, event_id="x",
                created_at=datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc),
                detail={"grid_v": 0.0, "bat_soc": 80.0, "load_power": 500.0,
                        "pv_power": 0.0})
    _, body, _, _ = format_message(old, CFG, now=NOW)
    assert "retraso" in body.lower()


def test_message_time_is_rendered_in_configured_timezone():
    """15:00 UTC son las 10:00 en Lima."""
    _, body, _, _ = format_message(
        ev(GRID_DOWN, {"grid_v": 0.0, "bat_soc": 80.0, "load_power": 500.0,
                       "pv_power": 0.0}), CFG, now=NOW)
    assert "10:00" in body


def test_send_posts_to_topic_url_with_headers():
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured.update(url=url, data=data, headers=headers)

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

        return R()

    NtfySink(CFG, post=fake_post).send(ev(GRID_DOWN, {
        "grid_v": 0.0, "bat_soc": 80.0, "load_power": 500.0, "pv_power": 0.0}))
    assert captured["url"] == "https://ntfy.sh/secret-topic"
    assert captured["headers"]["Priority"] == "5"


def test_send_raises_delivery_error_on_failure():
    def failing_post(*args, **kwargs):
        raise OSError("network down")

    with pytest.raises(DeliveryError):
        NtfySink(CFG, post=failing_post).send(ev(GRID_DOWN, {
            "grid_v": 0.0, "bat_soc": 80.0, "load_power": 500.0, "pv_power": 0.0}))
