"""Loop principal y línea de comandos."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from . import state as state_mod
from .config import Config, ConfigError
from .detector import detect
from .models import GRID_DOWN, Event, State
from .notifier import DeliveryError, NtfySink
from .source import GrowattCloudSource

POLL_MAX_S = 1800   # techo del backoff: 30 min (§10 del diseño)


def _log(message: str) -> None:
    print(f"[grid-watch] {message}", flush=True)


def next_delay(consecutive_failures: int, cfg: Config) -> int:
    """Backoff exponencial sobre el intervalo normal, con techo."""
    if consecutive_failures <= 0:
        return cfg.poll_interval_s
    return min(cfg.poll_interval_s * (2 ** consecutive_failures), POLL_MAX_S)


def tick(state: State, source, sink, cfg: Config,
         now: datetime) -> tuple[State, bool]:
    """Una iteración: leer, detectar, encolar y entregar lo pendiente.

    Devuelve el estado nuevo y si la lectura fue válida — `run()` lo usa para
    decidir el backoff, y no se persiste porque no sobrevive al reinicio.
    """
    reading = source.read()
    if not reading.ok:
        _log(f"lectura fallida: {reading.error}")
    state, events = detect(state, reading, now, cfg)
    for event in events:
        _log(f"evento {event.kind}")
    state.queue = list(state.queue) + events

    delivered = []
    for event in state.queue:
        try:
            sink.send(event)
            delivered.append(event.event_id)
        except DeliveryError as exc:
            _log(f"entrega fallida ({exc}); queda en cola")
            break
    if delivered:
        state.queue = [e for e in state.queue if e.event_id not in delivered]
    return state, reading.ok


def run(cfg: Config, source=None, sink=None, sleep=time.sleep, iterations=None) -> int:
    source = source or GrowattCloudSource(cfg)
    sink = sink or NtfySink(cfg)
    state = state_mod.load(cfg.state_path)
    _log(f"arrancando · estado de red: {state.grid} · intervalo {cfg.poll_interval_s}s")
    count = 0
    failures = 0
    while iterations is None or count < iterations:
        state, read_ok = tick(state, source, sink, cfg, datetime.now(timezone.utc))
        state_mod.save(cfg.state_path, state)
        failures = 0 if read_ok else failures + 1
        count += 1
        if iterations is None or count < iterations:
            delay = next_delay(failures, cfg)
            if failures:
                _log(f"{failures} fallos seguidos; próximo intento en {delay}s")
            sleep(delay)
    return 0


def _diagnose(cfg: Config) -> int:
    """Sondea sin notificar nada: valida la señal antes de confiar en ella."""
    source = GrowattCloudSource(cfg)
    reading = source.read()
    print(json.dumps({
        "ok": reading.ok,
        "error": reading.error,
        "sample_time": reading.sample_time.isoformat() if reading.sample_time else None,
        "status_text": reading.status_text,
        "grid_v": reading.grid_v,
        "grid_hz": reading.grid_hz,
        "grid_power": reading.grid_power,
        "bat_soc": reading.bat_soc,
        "load_power": reading.load_power,
        "pv_power": reading.pv_power,
    }, indent=2))
    print(f"\numbrales: caída < {cfg.grid_down_below} V · "
          f"recuperación > {cfg.grid_ok_above} V", file=sys.stderr)
    return 0 if reading.ok else 1


def _test_notify(cfg: Config) -> int:
    event = Event(kind=GRID_DOWN, event_id="test@manual",
                  created_at=datetime.now(timezone.utc),
                  detail={"grid_v": 0.0, "bat_soc": 88.0,
                          "load_power": 750.0, "pv_power": 1100.0})
    try:
        NtfySink(cfg).send(event)
    except DeliveryError as exc:
        print(f"fallo de entrega: {exc}", file=sys.stderr)
        return 1
    print("notificación de prueba enviada")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grid-watch")
    parser.add_argument("--config", default=os.environ.get("GRID_WATCH_CONFIG"))
    parser.add_argument("--diagnose", action="store_true",
                        help="imprime una lectura y sale, sin notificar")
    parser.add_argument("--test-notify", action="store_true",
                        help="envía una notificación de prueba y sale")
    args = parser.parse_args(argv)
    try:
        cfg = Config.load(args.config, os.environ)
    except ConfigError as exc:
        print(f"configuración inválida: {exc}", file=sys.stderr)
        return 2
    if args.diagnose:
        return _diagnose(cfg)
    if args.test_notify:
        return _test_notify(cfg)
    try:
        return run(cfg)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
