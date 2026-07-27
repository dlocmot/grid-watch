"""Sonda de lectura cruda contra la nube de Growatt.

Existe para validar contra el inversor real los dos supuestos del diseño:
si `vGrid` refleja la red de verdad, y si hay un timestamp del lado del
dispositivo con el que detectar que dejó de reportar.
"""
from __future__ import annotations

import functools
import json
import sys

import growattServer

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"


def build_session_api(user_agent: str = BROWSER_UA):
    """GrowattApi con User-Agent de navegador.

    Con el User-Agent por defecto de la librería (Dalvik/...), Cloudflare
    responde 403 y ninguna llamada funciona.
    """
    api = growattServer.GrowattApi(agent_identifier=user_agent)
    api.server_url = "https://server.growatt.com/"
    return api


def patch_session_timeout(api, timeout: int = 20) -> None:
    """Inyecta un timeout por defecto en la sesión de requests.

    growattServer usa requests.Session sin timeout: un socket a medias
    cuelga el proceso para siempre y el backoff nunca llega a activarse.
    """
    session = getattr(api, "session", None)
    if session is None or getattr(session, "_grid_watch_timeout", False):
        return
    original = session.request

    @functools.wraps(original)
    def request_with_timeout(*args, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return original(*args, **kwargs)

    session.request = request_with_timeout
    session._grid_watch_timeout = True


def fetch_raw(user: str, password: str, plant_id: str | None = None) -> dict:
    """Devuelve el storageDetailBean crudo del primer inversor storage."""
    api = build_session_api()
    patch_session_timeout(api)
    login = api.login(user, password)
    if not login or not login.get("success"):
        raise RuntimeError(f"login rechazado: {(login or {}).get('msg', 'sin detalle')}")
    user_id = login["user"]["id"]
    plants = api.plant_list(user_id).get("data", [])
    if not plants:
        raise RuntimeError("la cuenta no tiene plantas")
    plant = next(
        (p for p in plants if str(p.get("plantId")) == str(plant_id)), plants[0]
    )
    pid = plant.get("plantId") or plant.get("id")
    for device in api.device_list(pid) or []:
        dtype = (device.get("deviceType") or device.get("type") or "").lower()
        sn = device.get("deviceSn") or device.get("sn") or device.get("deviceAilas")
        if "storage" in dtype and sn:
            params = api.storage_params(sn) or {}
            return params.get("storageDetailBean", {}) or {}
    raise RuntimeError("no se encontró ningún dispositivo de tipo storage")


def main(argv: list[str] | None = None) -> int:
    import os

    user = os.environ.get("GROWATT_USER")
    password = os.environ.get("GROWATT_PASSWORD")
    if not user or not password:
        print("faltan GROWATT_USER y GROWATT_PASSWORD en el entorno", file=sys.stderr)
        return 2
    raw = fetch_raw(user, password, os.environ.get("GROWATT_PLANT_ID"))
    print(json.dumps(raw, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
