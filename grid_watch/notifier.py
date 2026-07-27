"""Entrega de eventos por ntfy."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from .config import Config
from .models import (BATTERY_CRITICAL, GRID_DOWN, GRID_RESTORED,
                     INVERTER_REPORTING, INVERTER_SILENT, MONITOR_BLIND, Event)


class DeliveryError(Exception):
    """No se pudo entregar el evento; el loop lo reencolará."""


_STYLE = {
    GRID_DOWN:          ("Corte de red pública", 5, ["zap", "rotating_light"]),
    GRID_RESTORED:      ("Volvió la red",        3, ["white_check_mark"]),
    BATTERY_CRITICAL:   ("Batería crítica",      5, ["battery", "warning"]),
    INVERTER_SILENT:    ("Inversor sin reportar", 4, ["mute"]),
    INVERTER_REPORTING: ("Inversor reportando de nuevo", 3, ["satellite"]),
    MONITOR_BLIND:      ("Monitor sin datos",    4, ["warning"]),
}


def _body(event: Event, tz: ZoneInfo) -> str:
    d = event.detail
    local = event.created_at.astimezone(tz).strftime("%H:%M")
    if event.kind == GRID_DOWN:
        return (f"{local} · red {d['grid_v']:.0f} V\n"
                f"Batería {d['bat_soc']:.0f}% · consumo {d['load_power']:.0f} W · "
                f"PV {d['pv_power']:.0f} W")
    if event.kind == GRID_RESTORED:
        return (f"{local} · el corte duró {d['outage_minutes']} min · "
                f"batería {d['bat_soc']:.0f}%")
    if event.kind == BATTERY_CRITICAL:
        return (f"{local} · batería {d['bat_soc']:.0f}% con {d['load_power']:.0f} W "
                f"de consumo\nLlevas {d['outage_minutes']} min sin red")
    if event.kind == INVERTER_SILENT:
        return f"{local} · sin datos nuevos desde hace {d['silent_minutes']} min"
    if event.kind == INVERTER_REPORTING:
        return f"{local} · estuvo {d['silent_minutes']} min en silencio"
    return (f"{local} · {d.get('blind_minutes', 0)} min sin lecturas válidas "
            f"({d.get('error')})")


def format_message(event: Event, cfg: Config,
                   now: datetime | None = None) -> tuple[str, str, int, list[str]]:
    tz = ZoneInfo(cfg.timezone)
    title, priority, tags = _STYLE[event.kind]
    body = _body(event, tz)
    now = now or datetime.now(timezone.utc)
    delay_min = int((now - event.created_at).total_seconds() // 60)
    if delay_min >= 5:
        body += f"\n\n(entregado con {delay_min} min de retraso)"
    return title, body, priority, tags


class NtfySink:
    """Envía eventos a un topic de ntfy. El topic funciona como contraseña."""

    def __init__(self, cfg: Config, post=requests.post):
        self._cfg = cfg
        self._post = post

    def send(self, event: Event) -> None:
        title, body, priority, tags = format_message(event, self._cfg)
        headers = {
            "Title": title,
            "Priority": str(priority),
            "Tags": ",".join(tags),
        }
        if self._cfg.ntfy_token:
            headers["Authorization"] = f"Bearer {self._cfg.ntfy_token}"
        url = f"{self._cfg.ntfy_url.rstrip('/')}/{self._cfg.ntfy_topic}"
        try:
            response = self._post(url, data=body.encode("utf-8"),
                                  headers=headers, timeout=15)
            response.raise_for_status()
        except Exception as exc:
            raise DeliveryError(f"{type(exc).__name__}: {exc}") from exc
