from datetime import datetime, timezone

from grid_watch import state as state_mod
from grid_watch.models import Event, State, GRID_DOWN


def test_load_missing_file_returns_fresh_state(tmp_path):
    s = state_mod.load(str(tmp_path / "nope.json"))
    assert s == State()


def test_save_then_load_roundtrips(tmp_path):
    path = str(tmp_path / "state.json")
    e = Event(kind=GRID_DOWN, event_id="x",
              created_at=datetime(2026, 7, 26, tzinfo=timezone.utc), detail={})
    original = State(grid="down", queue=[e], seen_grid_ok=True)
    state_mod.save(path, original)
    assert state_mod.load(path) == original


def test_corrupt_file_falls_back_to_fresh_state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    assert state_mod.load(str(path)) == State()


def test_save_leaves_no_temporary_file_behind(tmp_path):
    path = tmp_path / "state.json"
    state_mod.save(str(path), State())
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]
