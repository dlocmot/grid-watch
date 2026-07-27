"""Nube de Growatt → Reading."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from . import probe
from .config import Config
from .models import Reading

_UNITS = ("MWh", "kWh", "Wh", "kW", "W", "%", "V", "Hz", "VA")
# Confirmado contra el inversor real (docs/api-notes.md): el campo es `time`,
# con formato "%Y-%m-%d %H:%M:%S" y en hora LOCAL NAIVE del inversor (no UTC).
# Se usa solo para comparar muestras entre sí; nunca se resta de nuestro reloj.
_TIME_FIELDS = ("time",)


class Source(Protocol):
    def read(self) -> Reading: ...


def _num(value, default=None):
    if value is None:
        return default
    text = str(value).strip().replace(",", "")
    for unit in _UNITS:
        if text.endswith(unit):
            text = text[: -len(unit)].strip()
    try:
        return float(text) if text else default
    except ValueError:
        return default


def _sample_time(bean: dict) -> datetime | None:
    for key in _TIME_FIELDS:
        raw = bean.get(key)
        if not raw:
            continue
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(str(raw), fmt)
            except ValueError:
                continue
    return None


def parse_storage_bean(bean: dict) -> Reading:
    """Conversión pura del storageDetailBean a Reading."""
    return Reading(
        sample_time=_sample_time(bean),
        grid_v=_num(bean.get("vGrid"), 0.0),
        grid_hz=_num(bean.get("freqGrid"), 0.0),
        grid_power=_num(bean.get("pAcInPut"), 0.0) - _num(bean.get("pacToGrid"), 0.0),
        bat_soc=_num(bean.get("capacity"), None),
        load_power=_num(bean.get("outPutPower") or bean.get("activePower"), 0.0),
        pv_power=_num(bean.get("ppv"), 0.0),
        status_text=bean.get("SPF5000StatusText") or bean.get("statusText") or "",
        ok=True,
    )


class GrowattCloudSource:
    """Lee el inversor a través de server.growatt.com.

    Reinicia la sesión ante cualquier fallo: la librería no distingue una
    sesión caducada de un error de red, y reintentar sobre una sesión muerta
    devuelve HTML en vez de JSON indefinidamente.
    """

    def __init__(self, cfg: Config, api_factory=probe.build_session_api):
        self._cfg = cfg
        self._api_factory = api_factory
        self._api = None
        self._user_id = None
        self._plant_id = cfg.growatt_plant_id
        self._sn = None

    def _reset(self) -> None:
        self._api = None
        self._user_id = None
        self._sn = None

    def _login(self) -> None:
        api = self._api_factory()
        probe.patch_session_timeout(api)
        resp = api.login(self._cfg.growatt_user, self._cfg.growatt_password)
        if not resp or not resp.get("success"):
            raise RuntimeError(f"login: {(resp or {}).get('msg', 'rechazado')}")
        self._api = api
        self._user_id = resp["user"]["id"]

    def _resolve_device(self) -> None:
        plants = self._api.plant_list(self._user_id).get("data", [])
        if not plants:
            raise RuntimeError("la cuenta no tiene plantas")
        plant = next(
            (p for p in plants if str(p.get("plantId")) == str(self._plant_id)),
            plants[0],
        )
        self._plant_id = plant.get("plantId") or plant.get("id")
        for device in self._api.device_list(self._plant_id) or []:
            dtype = (device.get("deviceType") or device.get("type") or "").lower()
            sn = device.get("deviceSn") or device.get("sn") or device.get("deviceAilas")
            if "storage" in dtype and sn:
                self._sn = sn
                return
        raise RuntimeError("sin dispositivo de tipo storage")

    def read(self) -> Reading:
        try:
            if self._api is None:
                self._login()
            if self._sn is None:
                self._resolve_device()
            params = self._api.storage_params(self._sn) or {}
            bean = params.get("storageDetailBean") or {}
            if not bean:
                raise RuntimeError("storageDetailBean vacío")
            return parse_storage_bean(bean)
        except Exception as exc:
            self._reset()
            return Reading.failed(f"{type(exc).__name__}: {exc}")
