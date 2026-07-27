"""Lógica de detección. Función pura: sin red, sin disco, sin reloj propio.

El tiempo entra siempre como parámetro `now` (aware, UTC). Los timestamps que
vienen del inversor son naive y en su hora local, así que solo se usan para
comparar muestras entre sí — nunca para medir duraciones.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime

from .config import Config
from .models import GRID_DOWN, GRID_RESTORED, Event, Reading, State


def _event(kind: str, when: datetime, sample_time: datetime | None, detail: dict) -> Event:
    stamp = (sample_time or when).isoformat()
    return Event(kind=kind, event_id=f"{kind}@{stamp}", created_at=when, detail=detail)


def _is_new_sample(state: State, reading: Reading) -> bool:
    """La nube repite la misma muestra durante minutos: solo cuenta si avanzó."""
    if reading.sample_time is None:
        return True
    return state.last_sample_time is None or reading.sample_time > state.last_sample_time


def detect(state: State, reading: Reading, now: datetime,
           cfg: Config) -> tuple[State, list[Event]]:
    s = dataclasses.replace(state, queue=list(state.queue))
    events: list[Event] = []

    if not reading.ok:
        # Una lectura fallida nunca toca el estado de la red: "no sé" no es
        # "no hay luz".
        return s, events

    s.last_ok_read_at = now
    fresh = _is_new_sample(s, reading)

    if reading.grid_v >= cfg.grid_ok_above:
        s.seen_grid_ok = True
        observed = "ok"
    elif reading.grid_v < cfg.grid_down_below:
        observed = "down"
    else:
        observed = s.grid if s.grid != "unknown" else "ok"   # banda de histéresis

    if s.grid == "unknown":
        s.grid = observed
        if fresh:
            s.last_sample_time = reading.sample_time or s.last_sample_time
            s.last_sample_seen_at = now
        return s, events

    if observed == s.grid:
        s.pending_kind = None
        s.pending_since = None
        s.pending_samples = 0
    else:
        if s.pending_kind != observed:
            s.pending_kind = observed
            # Nuestro reloj, no el del inversor: su `time` es naive local y
            # restarlo de `now` (UTC aware) lanzaría TypeError.
            s.pending_since = now
            s.pending_samples = 1
        elif fresh:
            s.pending_samples += 1

        elapsed = (now - s.pending_since).total_seconds() if s.pending_since else 0.0
        confirmed = s.pending_samples >= 2 or elapsed >= cfg.min_sustain_s

        if confirmed:
            if observed == "down" and s.seen_grid_ok:
                s.grid = "down"
                s.outage_started_at = s.pending_since
                events.append(_event(GRID_DOWN, now, reading.sample_time, {
                    "grid_v": reading.grid_v,
                    "bat_soc": reading.bat_soc,
                    "load_power": reading.load_power,
                    "pv_power": reading.pv_power,
                }))
            elif observed == "down":
                # Nunca vimos la red arriba: la señal no está validada, no
                # alertamos (ver §7.1 del diseño).
                s.grid = "down"
            elif observed == "ok":
                minutes = 0
                if s.outage_started_at is not None:
                    minutes = int((now - s.outage_started_at).total_seconds() // 60)
                was_down = s.grid == "down"
                s.grid = "ok"
                s.outage_started_at = None
                s.battery_alerted = False
                if was_down and s.seen_grid_ok:
                    events.append(_event(GRID_RESTORED, now, reading.sample_time, {
                        "outage_minutes": minutes,
                        "bat_soc": reading.bat_soc,
                    }))
            s.pending_kind = None
            s.pending_since = None
            s.pending_samples = 0

    if fresh:
        if reading.sample_time is not None:
            s.last_sample_time = reading.sample_time
        s.last_sample_seen_at = now
    return s, events
