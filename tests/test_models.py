from datetime import datetime, timezone

from grid_watch.models import Event, Reading, State, GRID_DOWN


def _dt(hour, minute=0):
    return datetime(2026, 7, 26, hour, minute, tzinfo=timezone.utc)


def test_failed_reading_carries_error_and_is_not_ok():
    r = Reading.failed("timeout")
    assert r.ok is False
    assert r.error == "timeout"
    assert r.grid_v == 0.0


def test_event_roundtrips_through_dict():
    e = Event(kind=GRID_DOWN, event_id="grid_down@2026-07-26T10:00:00+00:00",
              created_at=_dt(10), detail={"grid_v": 0.0, "bat_soc": 87.0})
    assert Event.from_dict(e.to_dict()) == e


def test_state_roundtrips_with_datetimes_and_queue():
    e = Event(kind=GRID_DOWN, event_id="x", created_at=_dt(10), detail={})
    s = State(grid="down", outage_started_at=_dt(9, 55), last_sample_time=_dt(9, 50),
              last_sample_seen_at=_dt(9, 51), seen_grid_ok=True, queue=[e])
    restored = State.from_dict(s.to_dict())
    assert restored == s
    assert restored.outage_started_at == _dt(9, 55)
    assert restored.last_sample_seen_at == _dt(9, 51)
    assert restored.queue[0].kind == GRID_DOWN


def test_fresh_state_defaults_to_unknown_grid_and_empty_queue():
    s = State()
    assert s.grid == "unknown"
    assert s.queue == []
    assert s.seen_grid_ok is False


def test_reading_exposes_status_text_defaulting_to_empty():
    assert Reading().status_text == ""
    assert Reading(status_text="Grid Bypass").status_text == "Grid Bypass"
