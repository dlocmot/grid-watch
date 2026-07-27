"""Vocabulario compartido entre fuente, detector, notificador y estado.

Dos relojes distintos conviven aquí y NO se pueden mezclar:

- `Reading.sample_time` y `State.last_sample_time` vienen del inversor: son
  naive y están en su hora local. Solo sirven para comparar muestras entre sí.
- `State.last_sample_seen_at`, `pending_since`, `last_ok_read_at` y
  `Event.created_at` son nuestro reloj, aware en UTC. Cualquier duración se
  mide con estos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

GRID_DOWN = "grid_down"
GRID_RESTORED = "grid_restored"
BATTERY_CRITICAL = "battery_critical"
INVERTER_SILENT = "inverter_silent"
INVERTER_REPORTING = "inverter_reporting"
MONITOR_BLIND = "monitor_blind"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(frozen=True)
class Reading:
    """Una muestra del inversor. `sample_time` es la hora del dispositivo."""

    sample_time: datetime | None = None
    grid_v: float = 0.0
    grid_hz: float = 0.0
    grid_power: float = 0.0
    bat_soc: float | None = None
    load_power: float = 0.0
    pv_power: float = 0.0
    # El inversor declara su modo ("Grid Bypass" con red presente). Se expone
    # desde ya; se evaluará como señal principal cuando veamos un corte real.
    status_text: str = ""
    ok: bool = True
    error: str | None = None

    @classmethod
    def failed(cls, error: str) -> "Reading":
        return cls(ok=False, error=error)


@dataclass(frozen=True)
class Event:
    kind: str
    event_id: str
    created_at: datetime
    detail: dict

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "event_id": self.event_id,
            "created_at": _iso(self.created_at),
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(
            kind=d["kind"],
            event_id=d["event_id"],
            created_at=_parse(d["created_at"]),
            detail=d.get("detail", {}),
        )


@dataclass
class State:
    grid: str = "unknown"                     # unknown | ok | down
    pending_kind: str | None = None           # "down" | "ok"
    pending_since: datetime | None = None     # nuestro reloj
    pending_samples: int = 0
    outage_started_at: datetime | None = None  # nuestro reloj
    battery_alerted: bool = False
    silent: bool = False
    blind_alerted: bool = False
    last_sample_time: datetime | None = None      # reloj del inversor (naive local)
    last_sample_seen_at: datetime | None = None   # nuestro reloj: cuándo la vimos
    last_ok_read_at: datetime | None = None       # nuestro reloj
    seen_grid_ok: bool = False
    queue: list[Event] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "grid": self.grid,
            "pending_kind": self.pending_kind,
            "pending_since": _iso(self.pending_since),
            "pending_samples": self.pending_samples,
            "outage_started_at": _iso(self.outage_started_at),
            "battery_alerted": self.battery_alerted,
            "silent": self.silent,
            "blind_alerted": self.blind_alerted,
            "last_sample_time": _iso(self.last_sample_time),
            "last_sample_seen_at": _iso(self.last_sample_seen_at),
            "last_ok_read_at": _iso(self.last_ok_read_at),
            "seen_grid_ok": self.seen_grid_ok,
            "queue": [e.to_dict() for e in self.queue],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        return cls(
            grid=d.get("grid", "unknown"),
            pending_kind=d.get("pending_kind"),
            pending_since=_parse(d.get("pending_since")),
            pending_samples=d.get("pending_samples", 0),
            outage_started_at=_parse(d.get("outage_started_at")),
            battery_alerted=d.get("battery_alerted", False),
            silent=d.get("silent", False),
            blind_alerted=d.get("blind_alerted", False),
            last_sample_time=_parse(d.get("last_sample_time")),
            last_sample_seen_at=_parse(d.get("last_sample_seen_at")),
            last_ok_read_at=_parse(d.get("last_ok_read_at")),
            seen_grid_ok=d.get("seen_grid_ok", False),
            queue=[Event.from_dict(e) for e in d.get("queue", [])],
        )
