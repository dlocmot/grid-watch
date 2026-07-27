"""Carga y validación de configuración. Los secretos solo vienen del entorno."""
from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field


class ConfigError(Exception):
    """Configuración ausente o incoherente. Aborta el arranque."""


@dataclass(frozen=True)
class Config:
    growatt_user: str
    growatt_password: str = field(repr=False)
    ntfy_topic: str = ""
    growatt_plant_id: str | None = None
    ntfy_url: str = "https://ntfy.sh"
    ntfy_token: str | None = field(default=None, repr=False)
    poll_interval_s: int = 180
    grid_nominal_v: float = 220.0
    grid_down_below: float = 0.0     # 0 ⇒ derivar del nominal
    grid_ok_above: float = 0.0       # 0 ⇒ derivar del nominal
    min_sustain_s: int = 300
    soc_critical: float = 20.0
    stale_after_s: int = 1200
    blind_after_s: int = 3600
    timezone: str = "America/Lima"
    state_path: str = "state.json"

    def __repr__(self) -> str:
        return (
            f"Config(growatt_user={self.growatt_user!r}, growatt_password='***', "
            f"ntfy_topic={self.ntfy_topic!r}, ntfy_token='***', "
            f"grid_nominal_v={self.grid_nominal_v}, "
            f"grid_down_below={self.grid_down_below}, "
            f"grid_ok_above={self.grid_ok_above}, "
            f"poll_interval_s={self.poll_interval_s})"
        )

    @classmethod
    def load(cls, toml_path: str | None, env: Mapping[str, str]) -> "Config":
        data: dict = {}
        if toml_path:
            try:
                with open(toml_path, "rb") as fh:
                    data = tomllib.load(fh)
            except OSError as exc:
                raise ConfigError(f"no puedo leer {toml_path}: {exc}") from exc
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"TOML inválido en {toml_path}: {exc}") from exc

        for var in ("GROWATT_USER", "GROWATT_PASSWORD", "NTFY_TOPIC"):
            if not env.get(var):
                raise ConfigError(f"falta la variable de entorno {var}")

        grid = data.get("grid", {})
        poll = data.get("poll", {})
        alerts = data.get("alerts", {})
        ntfy = data.get("ntfy", {})

        nominal = float(grid.get("nominal_v", 220.0))
        down_below = float(grid.get("down_below", 0.0)) or round(nominal * 0.68, 2)
        ok_above = float(grid.get("ok_above", 0.0)) or round(nominal * 0.82, 2)
        if down_below >= ok_above:
            raise ConfigError(
                f"down_below ({down_below}) debe ser menor que ok_above ({ok_above})"
            )

        cfg = cls(
            growatt_user=env["GROWATT_USER"],
            growatt_password=env["GROWATT_PASSWORD"],
            growatt_plant_id=env.get("GROWATT_PLANT_ID"),
            ntfy_topic=env["NTFY_TOPIC"],
            ntfy_token=env.get("NTFY_TOKEN"),
            ntfy_url=ntfy.get("url", "https://ntfy.sh"),
            poll_interval_s=int(poll.get("interval_s", 180)),
            grid_nominal_v=nominal,
            grid_down_below=down_below,
            grid_ok_above=ok_above,
            min_sustain_s=int(grid.get("min_sustain_s", 300)),
            soc_critical=float(alerts.get("soc_critical", 20.0)),
            stale_after_s=int(alerts.get("stale_after_s", 1200)),
            blind_after_s=int(alerts.get("blind_after_s", 3600)),
            timezone=data.get("timezone", "America/Lima"),
            state_path=data.get("state_path", "state.json"),
        )
        if cfg.poll_interval_s < 30:
            raise ConfigError(
                "poll_interval_s por debajo de 30 s arriesga bloqueo de Cloudflare"
            )
        return cfg
