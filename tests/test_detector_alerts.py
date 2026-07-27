from datetime import datetime, timedelta, timezone

from grid_watch.config import Config
from grid_watch.detector import detect
from grid_watch.models import (BATTERY_CRITICAL, INVERTER_REPORTING,
                               INVERTER_SILENT, MONITOR_BLIND, Reading, State)

CFG = Config.load(None, {"GROWATT_USER": "u", "GROWATT_PASSWORD": "p", "NTFY_TOPIC": "t"})
T0 = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)


def reading(volts, minutes, soc=80.0):
    return Reading(sample_time=datetime(2026, 7, 26, 5, 0) + timedelta(minutes=minutes),
                   grid_v=volts, bat_soc=soc, load_power=900.0)


def feed(state, samples):
    events = []
    for r, offset in samples:
        state, new = detect(state, r, T0 + timedelta(minutes=offset), CFG)
        events.extend(new)
    return state, events


def in_outage():
    state, _ = feed(State(), [(reading(220, 0), 0), (reading(0, 5), 5),
                              (reading(0, 10), 10)])
    assert state.grid == "down"
    return state


def test_battery_critical_fires_once_during_outage():
    state = in_outage()
    state, events = feed(state, [(reading(0, 15, soc=18.0), 15)])
    assert [e.kind for e in events] == [BATTERY_CRITICAL]
    assert events[0].detail["bat_soc"] == 18.0
    state, events = feed(state, [(reading(0, 20, soc=17.0), 20)])
    assert events == [], "no debe repetirse dentro del mismo apagón"


def test_battery_critical_not_fired_with_grid_present():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    state, events = feed(state, [(reading(220, 5, soc=10.0), 5)])
    assert events == []


def test_battery_alert_rearms_after_grid_returns():
    state = in_outage()
    state, _ = feed(state, [(reading(0, 15, soc=18.0), 15)])
    state, _ = feed(state, [(reading(220, 20), 20), (reading(220, 25), 25)])
    assert state.battery_alerted is False


def test_silent_fires_when_sample_stops_advancing():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    stuck = reading(220, 0)
    state, events = feed(state, [(stuck, 21)])   # 21 min > stale_after 1200 s
    assert [e.kind for e in events] == [INVERTER_SILENT]
    assert state.silent is True
    state, events = feed(state, [(stuck, 25)])
    assert events == [], "solo una vez"


def test_reporting_again_closes_the_silence():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    state, _ = feed(state, [(reading(220, 0), 21)])
    state, events = feed(state, [(reading(220, 25), 26)])
    assert [e.kind for e in events] == [INVERTER_REPORTING]
    assert state.silent is False
    assert events[0].detail["silent_minutes"] == 26


def test_monitor_blind_after_an_hour_of_failed_reads():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    state, events = feed(state, [(Reading.failed("timeout"), 30)])
    assert events == []
    state, events = feed(state, [(Reading.failed("timeout"), 61)])
    assert [e.kind for e in events] == [MONITOR_BLIND]
    assert state.blind_alerted is True
    state, events = feed(state, [(Reading.failed("timeout"), 90)])
    assert events == [], "solo una vez"


def test_blind_flag_clears_on_successful_read():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    state, _ = feed(state, [(Reading.failed("boom"), 61)])
    state, _ = feed(state, [(reading(220, 65), 65)])
    assert state.blind_alerted is False
