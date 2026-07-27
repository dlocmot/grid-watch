from datetime import datetime, timedelta, timezone

from grid_watch.config import Config
from grid_watch.detector import detect
from grid_watch.models import GRID_DOWN, GRID_RESTORED, Reading, State

ENV = {"GROWATT_USER": "u", "GROWATT_PASSWORD": "p", "NTFY_TOPIC": "t"}
CFG = Config.load(None, ENV)
T0 = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)


def reading(volts, minutes, soc=80.0):
    """El sample_time imita al inversor: naive y en hora local (UTC-5)."""
    return Reading(sample_time=datetime(2026, 7, 26, 5, 0) + timedelta(minutes=minutes),
                   grid_v=volts, grid_hz=60.0 if volts else 0.0,
                   bat_soc=soc, load_power=900.0)


def feed(state, samples):
    """Aplica una lista de (reading, offset_min) y acumula los eventos."""
    events = []
    for r, offset in samples:
        state, new = detect(state, r, T0 + timedelta(minutes=offset), CFG)
        events.extend(new)
    return state, events


def test_first_ok_sample_sets_grid_ok_without_events():
    state, events = feed(State(), [(reading(220, 0), 0)])
    assert state.grid == "ok"
    assert state.seen_grid_ok is True
    assert events == []


def test_outage_needs_two_distinct_samples_before_alerting():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    state, events = feed(state, [(reading(0, 5), 5)])
    assert events == [], "una sola muestra caída no debe alertar"
    assert state.grid == "ok"
    state, events = feed(state, [(reading(0, 10), 10)])
    assert [e.kind for e in events] == [GRID_DOWN]
    assert state.grid == "down"
    assert state.outage_started_at == T0 + timedelta(minutes=5)


def test_repeated_identical_sample_does_not_confirm():
    """La nube repite la misma muestra: no puede contar como segunda."""
    state, _ = feed(State(), [(reading(220, 0), 0)])
    same = reading(0, 5)
    state, events = feed(state, [(same, 5), (same, 8)])
    assert events == []
    assert state.grid == "ok"


def test_min_sustain_confirms_when_sample_time_never_advances():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    stuck = reading(0, 5)
    state, events = feed(state, [(stuck, 5)])
    assert events == []
    state, events = feed(state, [(stuck, 11)])   # 6 min > min_sustain 300 s
    assert [e.kind for e in events] == [GRID_DOWN]


def test_micro_outage_recovering_before_confirmation_is_silent():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    state, events = feed(state, [(reading(0, 5), 5), (reading(220, 10), 10)])
    assert events == []
    assert state.grid == "ok"
    assert state.pending_kind is None


def test_restore_reports_outage_duration():
    state, _ = feed(State(), [(reading(220, 0), 0), (reading(0, 5), 5),
                              (reading(0, 10), 10)])
    state, events = feed(state, [(reading(220, 15), 15), (reading(220, 20), 20)])
    assert [e.kind for e in events] == [GRID_RESTORED]
    assert events[0].detail["outage_minutes"] == 15
    assert state.grid == "ok"
    assert state.outage_started_at is None


def test_hysteresis_band_does_not_restore():
    """160 V está bajo ok_above (180.4): sigue siendo corte."""
    state, _ = feed(State(), [(reading(220, 0), 0), (reading(0, 5), 5),
                              (reading(0, 10), 10)])
    state, events = feed(state, [(reading(160, 15), 15), (reading(160, 20), 20)])
    assert events == []
    assert state.grid == "down"


def test_failed_reading_never_produces_grid_down():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    state, events = feed(state, [(Reading.failed("timeout"), 5),
                                 (Reading.failed("timeout"), 10)])
    assert events == []
    assert state.grid == "ok"


def test_cold_start_without_validated_signal_stays_silent():
    """Sin haber visto nunca la red arriba, no alerta (señal no validada)."""
    state, events = feed(State(), [(reading(0, 0), 0), (reading(0, 5), 5)])
    assert events == []
    # Una vez validada la señal, sí alerta.
    state, _ = feed(State(), [(reading(220, 0), 0)])
    state, events = feed(state, [(reading(0, 5), 5), (reading(0, 10), 10)])
    assert [e.kind for e in events] == [GRID_DOWN]


def test_device_clock_is_never_subtracted_from_ours():
    """El sample_time es naive; restarlo de `now` (aware) sería TypeError."""
    state, _ = feed(State(), [(reading(220, 0), 0)])
    assert state.last_sample_time.tzinfo is None
    assert state.last_sample_seen_at == T0
