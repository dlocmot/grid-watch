from datetime import datetime

from grid_watch.source import parse_storage_bean


def test_parses_grid_fields_with_units_and_commas():
    r = parse_storage_bean({"vGrid": "220.5 V", "freqGrid": "60.0 Hz",
                            "pAcInPut": "1,200 W", "pacToGrid": "0",
                            "capacity": "87 %", "outPutPower": "900 W",
                            "ppv": "1500 W"})
    assert r.ok is True
    assert r.grid_v == 220.5
    assert r.grid_hz == 60.0
    assert r.grid_power == 1200.0
    assert r.bat_soc == 87.0
    assert r.load_power == 900.0
    assert r.pv_power == 1500.0


def test_outage_bean_reports_zero_grid():
    r = parse_storage_bean({"vGrid": "0", "freqGrid": "0", "capacity": "64"})
    assert r.grid_v == 0.0
    assert r.ok is True


def test_sample_time_parsed_when_present():
    r = parse_storage_bean({"vGrid": "220", "time": "2026-07-26 15:04:05"})
    assert r.sample_time == datetime(2026, 7, 26, 15, 4, 5)
    assert r.sample_time.tzinfo is None, "hora local naive del inversor"


def test_missing_timestamp_leaves_sample_time_none():
    r = parse_storage_bean({"vGrid": "220"})
    assert r.sample_time is None


def test_missing_soc_is_none_not_zero():
    """0% y 'sin dato' no significan lo mismo para la alerta de batería."""
    r = parse_storage_bean({"vGrid": "220"})
    assert r.bat_soc is None


def test_status_text_is_exposed_for_future_use():
    r = parse_storage_bean({"vGrid": "218.3", "statusText": "Bypass",
                            "SPF5000StatusText": "Grid Bypass"})
    assert r.status_text == "Grid Bypass"


def test_status_text_falls_back_to_generic_field():
    r = parse_storage_bean({"vGrid": "218.3", "statusText": "Bypass"})
    assert r.status_text == "Bypass"
