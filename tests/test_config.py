import pytest

from grid_watch.config import Config, ConfigError

ENV = {
    "GROWATT_USER": "someone",
    "GROWATT_PASSWORD": "s3cret",
    "NTFY_TOPIC": "topic-abc",
}


def test_defaults_derive_thresholds_from_nominal_voltage():
    cfg = Config.load(None, ENV)
    assert cfg.grid_nominal_v == 220.0
    assert cfg.grid_down_below == pytest.approx(149.6)   # 68%
    assert cfg.grid_ok_above == pytest.approx(180.4)     # 82%
    assert cfg.poll_interval_s == 180


def test_toml_overrides_defaults(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[grid]\nnominal_v = 230\n[poll]\ninterval_s = 60\n')
    cfg = Config.load(str(p), ENV)
    assert cfg.grid_nominal_v == 230.0
    assert cfg.poll_interval_s == 60


def test_missing_secret_raises_config_error():
    with pytest.raises(ConfigError, match="GROWATT_PASSWORD"):
        Config.load(None, {"GROWATT_USER": "x", "NTFY_TOPIC": "t"})


def test_incoherent_thresholds_rejected(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[grid]\ndown_below = 200\nok_above = 150\n")
    with pytest.raises(ConfigError, match="down_below"):
        Config.load(str(p), ENV)


def test_password_is_redacted_in_repr():
    cfg = Config.load(None, ENV)
    assert "s3cret" not in repr(cfg)
    assert "***" in repr(cfg)
