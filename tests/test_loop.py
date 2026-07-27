from datetime import datetime, timedelta, timezone

from grid_watch.__main__ import next_delay, tick
from grid_watch.config import Config
from grid_watch.models import GRID_DOWN, Reading, State
from grid_watch.notifier import DeliveryError

CFG = Config.load(None, {"GROWATT_USER": "u", "GROWATT_PASSWORD": "p", "NTFY_TOPIC": "t"})
T0 = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)


class FakeSource:
    def __init__(self, readings):
        self._readings = list(readings)

    def read(self):
        return self._readings.pop(0)


class RecordingSink:
    def __init__(self, fail_times=0):
        self.sent = []
        self._fail_times = fail_times

    def send(self, event):
        if self._fail_times > 0:
            self._fail_times -= 1
            raise DeliveryError("boom")
        self.sent.append(event)


def reading(volts, minutes):
    return Reading(sample_time=datetime(2026, 7, 26, 5, 0) + timedelta(minutes=minutes),
                   grid_v=volts, bat_soc=80.0, load_power=900.0, pv_power=0.0)


def run_ticks(source, sink, offsets, state=None):
    state = state or State()
    for offset in offsets:
        state, _read_ok = tick(state, source, sink, CFG,
                               T0 + timedelta(minutes=offset))
    return state


def test_outage_produces_one_delivered_notification():
    source = FakeSource([reading(220, 0), reading(0, 5), reading(0, 10)])
    sink = RecordingSink()
    state = run_ticks(source, sink, [0, 5, 10])
    assert [e.kind for e in sink.sent] == [GRID_DOWN]
    assert state.queue == []


def test_failed_delivery_is_queued_and_retried_later():
    source = FakeSource([reading(220, 0), reading(0, 5), reading(0, 10),
                         reading(0, 15)])
    sink = RecordingSink(fail_times=1)
    state = run_ticks(source, sink, [0, 5, 10])
    assert sink.sent == [], "la primera entrega falla"
    assert len(state.queue) == 1
    state = run_ticks(source, sink, [15], state=state)
    assert [e.kind for e in sink.sent] == [GRID_DOWN]
    assert state.queue == []


def test_queued_event_is_not_duplicated_on_replay():
    source = FakeSource([reading(220, 0), reading(0, 5), reading(0, 10),
                         reading(0, 15), reading(0, 20)])
    sink = RecordingSink(fail_times=2)
    state = run_ticks(source, sink, [0, 5, 10, 15, 20])
    ids = [e.event_id for e in sink.sent]
    assert len(ids) == len(set(ids)) == 1


def test_failed_reads_back_off_exponentially_up_to_the_cap():
    """§10 del diseño: reintentos con backoff, techo de 30 min."""
    base = CFG.poll_interval_s
    assert next_delay(0, CFG) == base
    assert next_delay(1, CFG) == base * 2
    assert next_delay(2, CFG) == base * 4
    assert next_delay(20, CFG) == 1800, "el techo son 30 min"
