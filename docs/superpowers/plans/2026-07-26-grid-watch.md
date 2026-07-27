# grid-watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un servicio que vigila la nube de Growatt y notifica al celular por ntfy cuando cae la red pública, cuando vuelve, cuando la batería llega a crítica durante el corte y cuando el inversor deja de reportar.

**Architecture:** Un daemon único bajo systemd. La fuente de datos está detrás de un protocolo de un método (`read() -> Reading`), el detector es una función pura sin red ni reloj propio (`detect(state, reading, now, cfg) -> (state, events)`), y la entrega es otro protocolo (`send(event)`). El loop cablea las tres piezas y persiste el estado en JSON, con los eventos no entregados encolados dentro de ese mismo estado.

**Tech Stack:** Python 3.11+ (stdlib `tomllib`), `growattServer` y `requests` para la nube, `pytest` para pruebas, systemd para el despliegue, GitHub Actions para CI.

## Global Constraints

- Python **3.12 o superior**. `tomllib` ya estaba en 3.11, pero `growattServer`
  2.x usa la sintaxis `type X = ...` (PEP 695) y en 3.11 falla al importar con
  `SyntaxError` — detectado por la CI, no en local.
- Licencia **MIT**; repositorio **público**: ningún secreto, IP, serial ni topic real puede entrar en un archivo versionado.
- Los secretos llegan **solo por variables de entorno**: `GROWATT_USER`, `GROWATT_PASSWORD`, `NTFY_TOPIC`, `NTFY_TOKEN`.
- La contraseña se **redacta** en cualquier log, repr o traza.
- Dependencias de ejecución limitadas a `growattServer` y `requests`. De desarrollo, `pytest`.
- Zona horaria por defecto `America/Lima`; configurable.
- Todos los `datetime` que crucen fronteras entre módulos son **aware en UTC**; la conversión a hora local ocurre solo al formatear mensajes.
- Un fallo de la API **nunca** puede producir un evento `grid_down`.
- Los tres detalles ya conocidos de la API de Growatt son obligatorios en `source.py`: User-Agent de navegador, timeout inyectado en la sesión de `requests`, y re-login forzado cuando la respuesta no es JSON.

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `grid_watch/models.py` | `Reading`, `Event`, `State` y su serialización JSON |
| `grid_watch/config.py` | `Config`: carga de TOML + entorno, validación, redacción |
| `grid_watch/detector.py` | `detect()` pura: toda la lógica de estados y umbrales |
| `grid_watch/state.py` | Carga y guardado atómico del estado |
| `grid_watch/notifier.py` | `Sink` y `NtfySink`: formato de mensajes y envío |
| `grid_watch/source.py` | `Source` y `GrowattCloudSource`: nube → `Reading` |
| `grid_watch/__main__.py` | CLI y loop: `run`, `--diagnose`, `--test-notify` |
| `deploy/grid-watch.service` | Unidad systemd |
| `docs/api-notes.md` | Hallazgos reales de la API (Tarea 2) |

---

### Task 1: Esqueleto del proyecto, pytest y CI

**Files:**
- Create: `pyproject.toml`
- Create: `grid_watch/__init__.py`
- Create: `tests/test_smoke.py`
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: nada.
- Produces: paquete importable `grid_watch` con `__version__: str`; comando `pytest` funcionando desde la raíz del repo.

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_smoke.py
import grid_watch


def test_package_exposes_version():
    assert isinstance(grid_watch.__version__, str)
    assert grid_watch.__version__
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

Run: `python -m pytest tests/test_smoke.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'grid_watch'`

- [ ] **Step 3: Crear el paquete y el pyproject**

```toml
# pyproject.toml
[project]
name = "grid-watch"
version = "0.1.0"
description = "Phone alert when the public grid goes down, detected through an off-grid Growatt inverter"
requires-python = ">=3.11"
license = { text = "MIT" }
dependencies = ["growattServer>=1.5.0", "requests>=2.31.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
grid-watch = "grid_watch.__main__:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["grid_watch*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

```python
# grid_watch/__init__.py
"""grid-watch — phone alerts when the public grid goes down."""

__version__ = "0.1.0"
```

- [ ] **Step 4: Instalar en modo editable y ejecutar el test**

Run: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest -v`
Expected: PASS (1 test)

- [ ] **Step 5: Añadir la CI**

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -e ".[dev]"
      - run: pytest -v
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml grid_watch/__init__.py tests/test_smoke.py .github/workflows/ci.yml
git commit -m "chore: esqueleto del paquete, pytest y CI"
```

---

### Task 2: Sonda contra la API real y registro de hallazgos

Esta tarea existe para resolver los dos supuestos de la sección 7 del spec **antes** de escribir el detector. No inventa abstracciones: imprime lo que la nube devuelve de verdad.

**Files:**
- Create: `grid_watch/probe.py`
- Create: `tests/test_probe.py`
- Create: `docs/api-notes.md`

**Interfaces:**
- Consumes: nada del proyecto.
- Produces: `probe.fetch_raw(user: str, password: str, plant_id: str | None = None) -> dict` que devuelve el `storageDetailBean` crudo del primer dispositivo de tipo `storage`; `probe.build_session_api(user_agent: str) -> growattServer.GrowattApi` reutilizada más tarde por `source.py`.

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_probe.py
import pytest
from grid_watch import probe


class FakeSession:
    def __init__(self):
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append(kwargs)
        return None


def test_build_session_api_injects_timeout_and_user_agent():
    api = probe.build_session_api("Mozilla/5.0 (X11; Linux x86_64)")
    assert "Mozilla" in api.agent_identifier
    fake = FakeSession()
    api.session = fake
    probe.patch_session_timeout(api, timeout=20)
    api.session.request("GET", "http://example.invalid")
    assert fake.calls[0]["timeout"] == 20


def test_patch_session_timeout_is_idempotent():
    api = probe.build_session_api("UA")
    fake = FakeSession()
    api.session = fake
    probe.patch_session_timeout(api, timeout=20)
    probe.patch_session_timeout(api, timeout=20)
    api.session.request("GET", "http://example.invalid")
    assert len(fake.calls) == 1
    assert fake.calls[0]["timeout"] == 20
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `.venv/bin/pytest tests/test_probe.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'grid_watch.probe'`

- [ ] **Step 3: Implementar la sonda**

```python
# grid_watch/probe.py
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
```

- [ ] **Step 4: Ejecutar los tests y verificar que pasan**

Run: `.venv/bin/pytest tests/test_probe.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Ejecutar la sonda contra el inversor real**

```bash
GROWATT_USER='...' GROWATT_PASSWORD='...' .venv/bin/python -m grid_watch.probe
```

Anotar del volcado: el valor de `vGrid` y `freqGrid` con la red presente, y qué campo (si alguno) contiene una marca de tiempo — candidatos habituales: `lastUpdateTime`, `time`, `calendar`. Anotar también un contador monótono utilizable como respaldo (`epvToday`, `eBatChargeToday`).

- [ ] **Step 6: Registrar los hallazgos**

Crear `docs/api-notes.md` con: nombre exacto del campo de timestamp o la constatación de que no existe; el campo de respaldo monótono elegido; y los valores observados de `vGrid`/`freqGrid` con red presente. Sin números de serie, sin `plantId`, sin credenciales — el repositorio es público.

- [ ] **Step 7: Commit**

```bash
git add grid_watch/probe.py tests/test_probe.py docs/api-notes.md
git commit -m "feat: sonda cruda de la API de Growatt y notas de campos reales"
```

---

### Task 3: Modelos de datos

**Files:**
- Create: `grid_watch/models.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `Reading(sample_time: datetime | None, grid_v: float, grid_hz: float, grid_power: float, bat_soc: float | None, load_power: float, pv_power: float, status_text: str = "", ok: bool = True, error: str | None = None)`, congelada.
  - `Reading.failed(error: str) -> Reading` constructor de lectura fallida.
  - `Event(kind: str, event_id: str, created_at: datetime, detail: dict)`, congelada, con `to_dict()` y `Event.from_dict(d)`.
  - `State` mutable con: `grid: str`, `pending_kind: str | None`, `pending_since: datetime | None`, `pending_samples: int`, `outage_started_at: datetime | None`, `battery_alerted: bool`, `silent: bool`, `blind_alerted: bool`, `last_sample_time: datetime | None`, `last_sample_seen_at: datetime | None`, `last_ok_read_at: datetime | None`, `seen_grid_ok: bool`, `queue: list[Event]`; con `to_dict()` y `State.from_dict(d)`.

> **Dos relojes distintos, no mezclar.** `last_sample_time` es la hora que
> declara el inversor: naive y en su zona local (verificado en la Tarea 2,
> campo `time`). Solo sirve para comparar muestras entre sí. Todo lo demás
> —`pending_since`, `last_sample_seen_at`, `last_ok_read_at`— es nuestro reloj
> en UTC aware, y es con esos con los que se mide cualquier duración. Restar
> uno de otro lanzaría `TypeError`, y forzarlo daría un desfase igual al huso
> del inversor.
  - Constantes de tipo de evento: `GRID_DOWN`, `GRID_RESTORED`, `BATTERY_CRITICAL`, `INVERTER_SILENT`, `INVERTER_REPORTING`, `MONITOR_BLIND`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_models.py
from datetime import datetime, timezone

from grid_watch.models import Event, Reading, State, GRID_DOWN


def _dt(hour, minute=0):
    return datetime(2026, 7, 26, hour, minute, tzinfo=timezone.utc)


def test_failed_reading_carries_error_and_is_not_ok():
    r = Reading.failed("timeout")
    assert r.ok is False
    assert r.error == "timeout"
    assert r.grid_v == 0.0


def test_event_roundtrips_through_dict():
    e = Event(kind=GRID_DOWN, event_id="grid_down@2026-07-26T10:00:00+00:00",
              created_at=_dt(10), detail={"grid_v": 0.0, "bat_soc": 87.0})
    assert Event.from_dict(e.to_dict()) == e


def test_state_roundtrips_with_datetimes_and_queue():
    e = Event(kind=GRID_DOWN, event_id="x", created_at=_dt(10), detail={})
    s = State(grid="down", outage_started_at=_dt(9, 55), last_sample_time=_dt(9, 50),
              seen_grid_ok=True, queue=[e])
    restored = State.from_dict(s.to_dict())
    assert restored == s
    assert restored.outage_started_at == _dt(9, 55)
    assert restored.queue[0].kind == GRID_DOWN


def test_fresh_state_defaults_to_unknown_grid_and_empty_queue():
    s = State()
    assert s.grid == "unknown"
    assert s.queue == []
    assert s.seen_grid_ok is False
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'grid_watch.models'`

- [ ] **Step 3: Implementar los modelos**

```python
# grid_watch/models.py
"""Vocabulario compartido entre fuente, detector, notificador y estado."""
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
    pending_since: datetime | None = None
    pending_samples: int = 0
    outage_started_at: datetime | None = None
    battery_alerted: bool = False
    silent: bool = False
    blind_alerted: bool = False
    last_sample_time: datetime | None = None      # reloj del inversor (naive local)
    last_sample_seen_at: datetime | None = None   # nuestro reloj: cuándo la vimos
    last_ok_read_at: datetime | None = None
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
```

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add grid_watch/models.py tests/test_models.py
git commit -m "feat: modelos Reading, Event y State con serialización"
```

---

### Task 4: Configuración y redacción de secretos

**Files:**
- Create: `grid_watch/config.py`
- Create: `tests/test_config.py`
- Create: `config.example.toml`
- Create: `.env.example`

**Interfaces:**
- Consumes: nada.
- Produces: `Config` congelada con `poll_interval_s: int`, `grid_nominal_v: float`, `grid_down_below: float`, `grid_ok_above: float`, `min_sustain_s: int`, `soc_critical: float`, `stale_after_s: int`, `blind_after_s: int`, `timezone: str`, `growatt_user: str`, `growatt_password: str`, `growatt_plant_id: str | None`, `ntfy_url: str`, `ntfy_topic: str`, `ntfy_token: str | None`, `state_path: str`; más `Config.load(toml_path: str | None, env: Mapping[str, str]) -> Config` y `ConfigError`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_config.py
import pytest

from grid_watch.config import Config, ConfigError

ENV = {
    "GROWATT_USER": "someone",
    "GROWATT_PASSWORD": "s3cret",
    "NTFY_TOPIC": "topic-abc",
}


def test_defaults_derive_thresholds_from_nominal_voltage(tmp_path):
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
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'grid_watch.config'`

- [ ] **Step 3: Implementar la configuración**

```python
# grid_watch/config.py
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
    ntfy_topic: str
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
            raise ConfigError("poll_interval_s por debajo de 30 s arriesga bloqueo de Cloudflare")
        return cfg
```

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Crear los ejemplos versionados**

```toml
# config.example.toml — copia a config.toml y ajusta. Sin secretos aquí.
timezone = "America/Lima"
state_path = "/var/lib/grid-watch/state.json"

[poll]
interval_s = 180

[grid]
nominal_v = 220
# down_below y ok_above se derivan del nominal (68% / 82%) si se omiten
min_sustain_s = 300

[alerts]
soc_critical = 20
stale_after_s = 1200
blind_after_s = 3600

[ntfy]
url = "https://ntfy.sh"
```

```bash
# .env.example — copia a .env (o /etc/grid-watch.env, modo 0600) y rellena.
# Nunca subas el archivo real: el topic de ntfy funciona como contraseña.
GROWATT_USER=
GROWATT_PASSWORD=
GROWATT_PLANT_ID=
NTFY_TOPIC=
NTFY_TOKEN=
```

- [ ] **Step 6: Commit**

```bash
git add grid_watch/config.py tests/test_config.py config.example.toml .env.example
git commit -m "feat: configuración TOML + entorno con validación y redacción de secretos"
```

---

### Task 5: Detector — caída y restablecimiento de la red

**Files:**
- Create: `grid_watch/detector.py`
- Create: `tests/test_detector_grid.py`

**Interfaces:**
- Consumes: `Reading`, `State`, `Event`, `GRID_DOWN`, `GRID_RESTORED` de `models`; `Config` de `config`.
- Produces: `detect(state: State, reading: Reading, now: datetime, cfg: Config) -> tuple[State, list[Event]]`. Devuelve un `State` nuevo (no muta el recibido) y la lista de eventos generados en esta llamada. En esta tarea solo emite `grid_down` y `grid_restored`; las demás familias se añaden en la Tarea 6.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_detector_grid.py
from datetime import datetime, timedelta, timezone

from grid_watch.config import Config
from grid_watch.detector import detect
from grid_watch.models import GRID_DOWN, GRID_RESTORED, Reading, State

ENV = {"GROWATT_USER": "u", "GROWATT_PASSWORD": "p", "NTFY_TOPIC": "t"}
CFG = Config.load(None, ENV)
T0 = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)


def reading(volts, minutes, soc=80.0):
    return Reading(sample_time=T0 + timedelta(minutes=minutes), grid_v=volts,
                   grid_hz=60.0 if volts else 0.0, bat_soc=soc, load_power=900.0)


def feed(state, samples):
    """Aplica una lista de (reading, offset_min) y acumula los eventos."""
    events = []
    for r, offset in samples:
        state, new = detect(state, r, T0 + timedelta(minutes=offset), CFG)
        events.extend(new)
    return state, events


def test_first_ok_sample_sets_grid_ok_without_events():
    state, events = feed(State(), [(reading(220, 0), 0)])
    assert state.grid == "ok"
    assert state.seen_grid_ok is True
    assert events == []


def test_outage_needs_two_distinct_samples_before_alerting():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    state, events = feed(state, [(reading(0, 5), 5)])
    assert events == [], "una sola muestra caída no debe alertar"
    assert state.grid == "ok"
    state, events = feed(state, [(reading(0, 10), 10)])
    assert [e.kind for e in events] == [GRID_DOWN]
    assert state.grid == "down"
    assert state.outage_started_at == T0 + timedelta(minutes=5)


def test_repeated_identical_sample_does_not_confirm():
    """La nube repite la misma muestra: no puede contar como segunda."""
    state, _ = feed(State(), [(reading(220, 0), 0)])
    same = reading(0, 5)
    state, events = feed(state, [(same, 5), (same, 8)])
    assert events == []
    assert state.grid == "ok"


def test_min_sustain_confirms_when_sample_time_never_advances():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    stuck = reading(0, 5)
    state, events = feed(state, [(stuck, 5)])
    assert events == []
    state, events = feed(state, [(stuck, 11)])   # 6 min > min_sustain 300 s
    assert [e.kind for e in events] == [GRID_DOWN]


def test_micro_outage_recovering_before_confirmation_is_silent():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    state, events = feed(state, [(reading(0, 5), 5), (reading(220, 10), 10)])
    assert events == []
    assert state.grid == "ok"
    assert state.pending_kind is None


def test_restore_reports_outage_duration():
    state, _ = feed(State(), [(reading(220, 0), 0), (reading(0, 5), 5),
                              (reading(0, 10), 10)])
    state, events = feed(state, [(reading(220, 15), 15), (reading(220, 20), 20)])
    assert [e.kind for e in events] == [GRID_RESTORED]
    assert events[0].detail["outage_minutes"] == 10
    assert state.grid == "ok"
    assert state.outage_started_at is None


def test_hysteresis_band_does_not_restore():
    """160 V está bajo ok_above (180.4): sigue siendo corte."""
    state, _ = feed(State(), [(reading(220, 0), 0), (reading(0, 5), 5),
                              (reading(0, 10), 10)])
    state, events = feed(state, [(reading(160, 15), 15), (reading(160, 20), 20)])
    assert events == []
    assert state.grid == "down"


def test_failed_reading_never_produces_grid_down():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    state, events = feed(state, [(Reading.failed("timeout"), 5),
                                 (Reading.failed("timeout"), 10)])
    assert events == []
    assert state.grid == "ok"


def test_cold_start_without_validated_signal_stays_silent():
    """Sin haber visto nunca la red arriba, no alerta (señal no validada)."""
    state, events = feed(State(), [(reading(0, 0), 0), (reading(0, 5), 5)])
    assert events == []
    # Una vez validada la señal, sí alerta.
    state, _ = feed(State(), [(reading(220, 0), 0)])
    state, events = feed(state, [(reading(0, 5), 5), (reading(0, 10), 10)])
    assert [e.kind for e in events] == [GRID_DOWN]
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `.venv/bin/pytest tests/test_detector_grid.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'grid_watch.detector'`

- [ ] **Step 3: Implementar el detector**

```python
# grid_watch/detector.py
"""Lógica de detección. Función pura: sin red, sin disco, sin reloj propio."""
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


def detect(state: State, reading: Reading, now: datetime, cfg: Config) -> tuple[State, list[Event]]:
    s = dataclasses.replace(state, queue=list(state.queue))
    events: list[Event] = []

    if not reading.ok:
        # Una lectura fallida nunca toca el estado de la red: "no sé" no es
        # "no hay luz". Solo el reloj de obsolescencia avanza (Tarea 6).
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
```

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `.venv/bin/pytest tests/test_detector_grid.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add grid_watch/detector.py tests/test_detector_grid.py
git commit -m "feat: detector de caída y restablecimiento de red con histéresis"
```

---

### Task 6: Detector — batería crítica, silencio del inversor y monitor ciego

**Files:**
- Modify: `grid_watch/detector.py`
- Create: `tests/test_detector_alerts.py`

**Interfaces:**
- Consumes: todo lo de la Tarea 5.
- Produces: `detect()` amplía su salida con `battery_critical`, `inverter_silent`, `inverter_reporting` y `monitor_blind`. Firma sin cambios.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_detector_alerts.py
from datetime import datetime, timedelta, timezone

from grid_watch.config import Config
from grid_watch.detector import detect
from grid_watch.models import (BATTERY_CRITICAL, INVERTER_REPORTING,
                               INVERTER_SILENT, MONITOR_BLIND, Reading, State)

CFG = Config.load(None, {"GROWATT_USER": "u", "GROWATT_PASSWORD": "p", "NTFY_TOPIC": "t"})
T0 = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)


def reading(volts, minutes, soc=80.0):
    return Reading(sample_time=T0 + timedelta(minutes=minutes), grid_v=volts,
                   bat_soc=soc, load_power=900.0)


def feed(state, samples):
    events = []
    for r, offset in samples:
        state, new = detect(state, r, T0 + timedelta(minutes=offset), CFG)
        events.extend(new)
    return state, events


def in_outage():
    state, _ = feed(State(), [(reading(220, 0), 0), (reading(0, 5), 5),
                              (reading(0, 10), 10)])
    assert state.grid == "down"
    return state


def test_battery_critical_fires_once_during_outage():
    state = in_outage()
    state, events = feed(state, [(reading(0, 15, soc=18.0), 15)])
    assert [e.kind for e in events] == [BATTERY_CRITICAL]
    assert events[0].detail["bat_soc"] == 18.0
    state, events = feed(state, [(reading(0, 20, soc=17.0), 20)])
    assert events == [], "no debe repetirse dentro del mismo apagón"


def test_battery_critical_not_fired_with_grid_present():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    state, events = feed(state, [(reading(220, 5, soc=10.0), 5)])
    assert events == []


def test_battery_alert_rearms_after_grid_returns():
    state = in_outage()
    state, _ = feed(state, [(reading(0, 15, soc=18.0), 15)])
    state, _ = feed(state, [(reading(220, 20), 20), (reading(220, 25), 25)])
    assert state.battery_alerted is False


def test_silent_fires_when_sample_stops_advancing():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    stuck = reading(220, 0)
    state, events = feed(state, [(stuck, 21)])   # 21 min > stale_after 1200 s
    assert [e.kind for e in events] == [INVERTER_SILENT]
    assert state.silent is True
    state, events = feed(state, [(stuck, 25)])
    assert events == [], "solo una vez"


def test_reporting_again_closes_the_silence():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    state, _ = feed(state, [(reading(220, 0), 21)])
    state, events = feed(state, [(reading(220, 25), 26)])
    assert [e.kind for e in events] == [INVERTER_REPORTING]
    assert state.silent is False
    assert events[0].detail["silent_minutes"] == 26


def test_monitor_blind_after_an_hour_of_failed_reads():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    state, events = feed(state, [(Reading.failed("timeout"), 30)])
    assert events == []
    state, events = feed(state, [(Reading.failed("timeout"), 61)])
    assert [e.kind for e in events] == [MONITOR_BLIND]
    assert state.blind_alerted is True
    state, events = feed(state, [(Reading.failed("timeout"), 90)])
    assert events == [], "solo una vez"


def test_blind_flag_clears_on_successful_read():
    state, _ = feed(State(), [(reading(220, 0), 0)])
    state, _ = feed(state, [(Reading.failed("boom"), 61)])
    state, _ = feed(state, [(reading(220, 65), 65)])
    assert state.blind_alerted is False
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `.venv/bin/pytest tests/test_detector_alerts.py -v`
Expected: FAIL — `ImportError: cannot import name 'BATTERY_CRITICAL'` no; los símbolos ya existen desde la Tarea 3, así que fallará en los asserts: `assert [] == ['battery_critical']`

- [ ] **Step 3: Ampliar el detector**

Sustituir el bloque inicial de `detect()` que atiende la lectura fallida por esta versión, e insertar el bloque de batería justo antes del `return` final:

```python
    if not reading.ok:
        # Una lectura fallida nunca toca el estado de la red. Solo puede
        # producir el aviso de "monitor ciego".
        if s.last_ok_read_at is not None and not s.blind_alerted:
            blind_s = (now - s.last_ok_read_at).total_seconds()
            if blind_s >= cfg.blind_after_s:
                s.blind_alerted = True
                events.append(_event(MONITOR_BLIND, now, None, {
                    "blind_minutes": int(blind_s // 60),
                    "error": reading.error,
                }))
        return s, events

    s.last_ok_read_at = now
    s.blind_alerted = False
    fresh = _is_new_sample(s, reading)

    # Silencio del inversor: la muestra deja de avanzar. El tiempo se mide
    # SIEMPRE con `last_sample_seen_at` (nuestro reloj), nunca con
    # `last_sample_time` (reloj naive local del inversor).
    if s.last_sample_seen_at is not None:
        age = 0.0 if fresh else (now - s.last_sample_seen_at).total_seconds()
        if not fresh and age >= cfg.stale_after_s and not s.silent:
            s.silent = True
            events.append(_event(INVERTER_SILENT, now, reading.sample_time, {
                "silent_minutes": int(age // 60),
                "grid_v": reading.grid_v,
                "bat_soc": reading.bat_soc,
            }))
        elif fresh and s.silent:
            silent_s = (now - s.last_sample_seen_at).total_seconds()
            s.silent = False
            events.append(_event(INVERTER_REPORTING, now, reading.sample_time, {
                "silent_minutes": int(silent_s // 60),
            }))
```

Y antes del `return s, events` final:

```python
    # Batería crítica: solo durante un corte, una vez por apagón.
    if (s.grid == "down" and reading.bat_soc is not None
            and reading.bat_soc <= cfg.soc_critical and not s.battery_alerted):
        s.battery_alerted = True
        elapsed = 0
        if s.outage_started_at is not None:
            elapsed = int((now - s.outage_started_at).total_seconds() // 60)
        events.append(_event(BATTERY_CRITICAL, now, reading.sample_time, {
            "bat_soc": reading.bat_soc,
            "load_power": reading.load_power,
            "outage_minutes": elapsed,
        }))
```

Actualizar el import de `models` para incluir `BATTERY_CRITICAL`, `INVERTER_SILENT`, `INVERTER_REPORTING` y `MONITOR_BLIND`.

- [ ] **Step 4: Ejecutar toda la batería del detector**

Run: `.venv/bin/pytest tests/test_detector_grid.py tests/test_detector_alerts.py -v`
Expected: PASS (16 tests). Los 9 de la Tarea 5 deben seguir verdes.

- [ ] **Step 5: Commit**

```bash
git add grid_watch/detector.py tests/test_detector_alerts.py
git commit -m "feat: alertas de batería crítica, inversor mudo y monitor ciego"
```

---

### Task 7: Persistencia atómica del estado

**Files:**
- Create: `grid_watch/state.py`
- Create: `tests/test_state.py`

**Interfaces:**
- Consumes: `State` de `models`.
- Produces: `load(path: str) -> State` (devuelve `State()` si el archivo no existe o está corrupto) y `save(path: str, state: State) -> None` (escritura atómica: temporal + `os.replace`).

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_state.py
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
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `.venv/bin/pytest tests/test_state.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'grid_watch.state'`

- [ ] **Step 3: Implementar la persistencia**

```python
# grid_watch/state.py
"""Carga y guardado atómico del estado en JSON."""
from __future__ import annotations

import json
import os
import tempfile

from .models import State


def load(path: str) -> State:
    """Estado guardado, o uno nuevo si no existe o no se puede interpretar."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return State.from_dict(json.load(fh))
    except (OSError, ValueError, KeyError):
        return State()


def save(path: str, state: State) -> None:
    """Escritura atómica: un corte a media escritura no deja basura."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state.to_dict(), fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
```

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `.venv/bin/pytest tests/test_state.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add grid_watch/state.py tests/test_state.py
git commit -m "feat: persistencia atómica del estado"
```

---

### Task 8: Notificador ntfy

**Files:**
- Create: `grid_watch/notifier.py`
- Create: `tests/test_notifier.py`

**Interfaces:**
- Consumes: `Event` y las constantes de tipo de `models`; `Config`.
- Produces:
  - `format_message(event: Event, cfg: Config) -> tuple[str, str, int, list[str]]` que devuelve `(title, body, priority, tags)`.
  - `NtfySink(cfg: Config, post=requests.post)` con `send(event: Event) -> None`, que lanza `DeliveryError` si la entrega falla.
  - `DeliveryError(Exception)`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_notifier.py
from datetime import datetime, timezone

import pytest

from grid_watch.config import Config
from grid_watch.models import (BATTERY_CRITICAL, GRID_DOWN, GRID_RESTORED,
                               Event, MONITOR_BLIND)
from grid_watch.notifier import DeliveryError, NtfySink, format_message

CFG = Config.load(None, {"GROWATT_USER": "u", "GROWATT_PASSWORD": "p",
                         "NTFY_TOPIC": "secret-topic"})
NOW = datetime(2026, 7, 26, 15, 0, tzinfo=timezone.utc)


def ev(kind, detail):
    return Event(kind=kind, event_id=f"{kind}@x", created_at=NOW, detail=detail)


def test_grid_down_is_urgent():
    title, body, priority, tags = format_message(
        ev(GRID_DOWN, {"grid_v": 0.0, "bat_soc": 87.0, "load_power": 900.0,
                       "pv_power": 1200.0}), CFG)
    assert priority == 5
    assert "87" in body and "900" in body


def test_grid_restored_is_normal_and_shows_duration():
    title, body, priority, tags = format_message(
        ev(GRID_RESTORED, {"outage_minutes": 42, "bat_soc": 61.0}), CFG)
    assert priority == 3
    assert "42" in body


def test_battery_critical_is_urgent():
    _, _, priority, _ = format_message(
        ev(BATTERY_CRITICAL, {"bat_soc": 18.0, "load_power": 800.0,
                              "outage_minutes": 90}), CFG)
    assert priority == 5


def test_monitor_blind_is_high():
    _, _, priority, _ = format_message(
        ev(MONITOR_BLIND, {"blind_minutes": 65, "error": "timeout"}), CFG)
    assert priority == 4


def test_delayed_event_declares_the_delay():
    old = Event(kind=GRID_DOWN, event_id="x",
                created_at=datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc),
                detail={"grid_v": 0.0, "bat_soc": 80.0, "load_power": 500.0,
                        "pv_power": 0.0})
    _, body, _, _ = format_message(old, CFG, now=NOW)
    assert "retraso" in body.lower()


def test_send_posts_to_topic_url_with_headers():
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured.update(url=url, data=data, headers=headers)

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

        return R()

    NtfySink(CFG, post=fake_post).send(ev(GRID_DOWN, {
        "grid_v": 0.0, "bat_soc": 80.0, "load_power": 500.0, "pv_power": 0.0}))
    assert captured["url"] == "https://ntfy.sh/secret-topic"
    assert captured["headers"]["Priority"] == "5"


def test_send_raises_delivery_error_on_failure():
    def failing_post(*args, **kwargs):
        raise OSError("network down")

    with pytest.raises(DeliveryError):
        NtfySink(CFG, post=failing_post).send(ev(GRID_DOWN, {
            "grid_v": 0.0, "bat_soc": 80.0, "load_power": 500.0, "pv_power": 0.0}))
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `.venv/bin/pytest tests/test_notifier.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'grid_watch.notifier'`

- [ ] **Step 3: Implementar el notificador**

```python
# grid_watch/notifier.py
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
        return f"{local} · el corte duró {d['outage_minutes']} min · batería {d['bat_soc']:.0f}%"
    if event.kind == BATTERY_CRITICAL:
        return (f"{local} · batería {d['bat_soc']:.0f}% con {d['load_power']:.0f} W "
                f"de consumo\nLlevas {d['outage_minutes']} min sin red")
    if event.kind == INVERTER_SILENT:
        return f"{local} · sin datos nuevos desde hace {d['silent_minutes']} min"
    if event.kind == INVERTER_REPORTING:
        return f"{local} · estuvo {d['silent_minutes']} min en silencio"
    return f"{local} · {d.get('blind_minutes', 0)} min sin lecturas válidas ({d.get('error')})"


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
```

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `.venv/bin/pytest tests/test_notifier.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add grid_watch/notifier.py tests/test_notifier.py
git commit -m "feat: notificador ntfy con prioridades y aviso de retraso"
```

---

### Task 9: Fuente de datos Growatt

**Files:**
- Create: `grid_watch/source.py`
- Create: `tests/test_source.py`
- Modify: `docs/api-notes.md` (usar el nombre real del campo de timestamp hallado en la Tarea 2)

**Interfaces:**
- Consumes: `Reading` de `models`; `Config`; `build_session_api` y `patch_session_timeout` de `probe`.
- Produces:
  - `Source` protocolo con `read() -> Reading`.
  - `GrowattCloudSource(cfg: Config, api_factory=probe.build_session_api)` con `read() -> Reading`.
  - `parse_storage_bean(bean: dict) -> Reading` — conversión pura y testeable sin red.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_source.py
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
    r = parse_storage_bean({"vGrid": "220", "lastUpdateTime": "2026-07-26 15:04:05"})
    assert r.sample_time == datetime(2026, 7, 26, 15, 4, 5)


def test_missing_timestamp_leaves_sample_time_none():
    r = parse_storage_bean({"vGrid": "220"})
    assert r.sample_time is None


def test_missing_soc_is_none_not_zero():
    """0% y 'sin dato' no significan lo mismo para la alerta de batería."""
    r = parse_storage_bean({"vGrid": "220"})
    assert r.bat_soc is None
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `.venv/bin/pytest tests/test_source.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'grid_watch.source'`

- [ ] **Step 3: Implementar la fuente**

```python
# grid_watch/source.py
"""Nube de Growatt → Reading."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from . import probe
from .config import Config
from .models import Reading

_UNITS = ("MWh", "kWh", "Wh", "kW", "W", "%", "V", "Hz", "VA")
# Campos candidatos a marca de tiempo del dispositivo, en orden de preferencia.
# Confirmar el real con la sonda de la Tarea 2 y dejar solo ese si se conoce.
# Confirmado en la Tarea 2: el campo es `time`, con formato
# "%Y-%m-%d %H:%M:%S" y en hora LOCAL NAIVE del inversor (no UTC). Se usa solo
# para comparar muestras entre sí; nunca se resta de nuestro reloj.
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
```

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `.venv/bin/pytest tests/test_source.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Añadir el test del status del inversor**

La Tarea 2 descubrió que el inversor declara su propio modo. Aún no sabemos qué valor toma durante un corte, así que de momento solo se expone, sin usarlo para decidir:

```python
# añadir a tests/test_source.py
def test_status_text_is_exposed_for_future_use():
    r = parse_storage_bean({"vGrid": "218.3", "statusText": "Bypass",
                            "SPF5000StatusText": "Grid Bypass"})
    assert r.status_text == "Grid Bypass"


def test_status_text_falls_back_to_generic_field():
    r = parse_storage_bean({"vGrid": "218.3", "statusText": "Bypass"})
    assert r.status_text == "Bypass"
```

El campo `status_text` ya está declarado en `Reading` (Tarea 3); aquí solo hay que rellenarlo en `parse_storage_bean` con `bean.get("SPF5000StatusText") or bean.get("statusText") or ""`.

Run: `.venv/bin/pytest tests/test_source.py -v`
Expected: PASS (7 tests)

Cuando ocurra el primer corte real, anotar en `docs/api-notes.md` qué valor toma el status y evaluar promoverlo a señal principal con `vGrid` de respaldo.

- [ ] **Step 6: Commit**

```bash
git add grid_watch/source.py tests/test_source.py docs/api-notes.md
git commit -m "feat: fuente Growatt con parseo puro del storageDetailBean"
```

---

### Task 10: Loop principal y CLI

**Files:**
- Create: `grid_watch/__main__.py`
- Create: `tests/test_loop.py`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces:
  - `tick(state: State, source: Source, sink, cfg: Config, now: datetime) -> State` — una iteración completa: leer, detectar, encolar, intentar entregar. No duerme ni persiste.
  - `run(cfg: Config, source=None, sink=None, sleep=time.sleep, iterations=None) -> int` — el bucle.
  - `main(argv: list[str] | None = None) -> int` con `--config`, `--diagnose` y `--test-notify`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_loop.py
from datetime import datetime, timedelta, timezone

from grid_watch.__main__ import tick
from grid_watch.config import Config
from grid_watch.models import GRID_DOWN, Reading, State
from grid_watch.notifier import DeliveryError

CFG = Config.load(None, {"GROWATT_USER": "u", "GROWATT_PASSWORD": "p", "NTFY_TOPIC": "t"})
T0 = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)


class FakeSource:
    def __init__(self, readings):
        self._readings = list(readings)

    def read(self):
        return self._readings.pop(0)


class RecordingSink:
    def __init__(self, fail_times=0):
        self.sent = []
        self._fail_times = fail_times

    def send(self, event):
        if self._fail_times > 0:
            self._fail_times -= 1
            raise DeliveryError("boom")
        self.sent.append(event)


def reading(volts, minutes):
    return Reading(sample_time=T0 + timedelta(minutes=minutes), grid_v=volts,
                   bat_soc=80.0, load_power=900.0, pv_power=0.0)


def run_ticks(source, sink, offsets, state=None):
    state = state or State()
    for offset in offsets:
        state, _read_ok = tick(state, source, sink, CFG,
                               T0 + timedelta(minutes=offset))
    return state


def test_outage_produces_one_delivered_notification():
    source = FakeSource([reading(220, 0), reading(0, 5), reading(0, 10)])
    sink = RecordingSink()
    state = run_ticks(source, sink, [0, 5, 10])
    assert [e.kind for e in sink.sent] == [GRID_DOWN]
    assert state.queue == []


def test_failed_delivery_is_queued_and_retried_later():
    source = FakeSource([reading(220, 0), reading(0, 5), reading(0, 10),
                         reading(0, 15)])
    sink = RecordingSink(fail_times=1)
    state = run_ticks(source, sink, [0, 5, 10])
    assert sink.sent == [], "la primera entrega falla"
    assert len(state.queue) == 1
    state = run_ticks(source, sink, [15], state=state)
    assert [e.kind for e in sink.sent] == [GRID_DOWN]
    assert state.queue == []


def test_queued_event_is_not_duplicated_on_replay():
    source = FakeSource([reading(220, 0), reading(0, 5), reading(0, 10),
                         reading(0, 15), reading(0, 20)])
    sink = RecordingSink(fail_times=2)
    state = run_ticks(source, sink, [0, 5, 10, 15, 20])
    ids = [e.event_id for e in sink.sent]
    assert len(ids) == len(set(ids)) == 1


def test_failed_reads_back_off_exponentially_up_to_the_cap():
    """§10 del diseño: reintentos con backoff, techo de 30 min."""
    from grid_watch.__main__ import next_delay

    base = CFG.poll_interval_s
    assert next_delay(0, CFG) == base
    assert next_delay(1, CFG) == base * 2
    assert next_delay(2, CFG) == base * 4
    assert next_delay(20, CFG) == 1800, "el techo son 30 min"
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `.venv/bin/pytest tests/test_loop.py -v`
Expected: FAIL con `ImportError: cannot import name 'tick' from 'grid_watch.__main__'`

- [ ] **Step 3: Implementar el loop y la CLI**

```python
# grid_watch/__main__.py
"""Loop principal y línea de comandos."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from . import probe, state as state_mod
from .config import Config, ConfigError
from .detector import detect
from .models import GRID_DOWN, Event, State
from .notifier import DeliveryError, NtfySink
from .source import GrowattCloudSource


def _log(message: str) -> None:
    print(f"[grid-watch] {message}", flush=True)


POLL_MAX_S = 1800   # techo del backoff: 30 min (§10 del diseño)


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
```

- [ ] **Step 4: Ejecutar toda la suite**

Run: `.venv/bin/pytest -v`
Expected: PASS (todos: smoke 1 + probe 2 + models 4 + config 5 + detector_grid 9
+ detector_alerts 7 + state 4 + notifier 7 + source 7 + loop 4 = **50 tests**)

- [ ] **Step 5: Commit**

```bash
git add grid_watch/__main__.py tests/test_loop.py
git commit -m "feat: loop principal con cola de reintentos y CLI de diagnóstico"
```

---

### Task 11: Despliegue y documentación

**Files:**
- Create: `deploy/grid-watch.service`
- Create: `deploy/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: el comando `grid-watch` declarado en `pyproject.toml`.
- Produces: unidad systemd instalable y documentación de puesta en marcha.

- [ ] **Step 1: Escribir la unidad systemd**

```ini
# deploy/grid-watch.service
[Unit]
Description=grid-watch — alerta de corte de red pública
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=grid-watch
Group=grid-watch
WorkingDirectory=/opt/grid-watch
EnvironmentFile=/etc/grid-watch.env
ExecStart=/opt/grid-watch/.venv/bin/grid-watch --config /etc/grid-watch/config.toml
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

# Endurecimiento: el servicio solo necesita leer su config y escribir su estado.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
StateDirectory=grid-watch

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Escribir la guía de despliegue**

Crear `deploy/README.md` con estos pasos, en inglés (documentación pública):

```bash
sudo useradd --system --home /opt/grid-watch --shell /usr/sbin/nologin grid-watch
sudo mkdir -p /opt/grid-watch /etc/grid-watch
sudo chown grid-watch:grid-watch /opt/grid-watch

sudo -u grid-watch git clone https://github.com/dlocmot/grid-watch /opt/grid-watch
sudo -u grid-watch python3 -m venv /opt/grid-watch/.venv
sudo -u grid-watch /opt/grid-watch/.venv/bin/pip install /opt/grid-watch

sudo cp /opt/grid-watch/config.example.toml /etc/grid-watch/config.toml
sudo cp /opt/grid-watch/.env.example /etc/grid-watch.env
sudo chmod 600 /etc/grid-watch.env      # contiene credenciales y el topic
sudo editor /etc/grid-watch.env

# Validar ANTES de habilitar el servicio:
sudo -u grid-watch env $(cat /etc/grid-watch.env | xargs) \
    /opt/grid-watch/.venv/bin/grid-watch --config /etc/grid-watch/config.toml --diagnose
sudo -u grid-watch env $(cat /etc/grid-watch.env | xargs) \
    /opt/grid-watch/.venv/bin/grid-watch --config /etc/grid-watch/config.toml --test-notify

sudo cp /opt/grid-watch/deploy/grid-watch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now grid-watch
journalctl -u grid-watch -f
```

Incluir la advertencia de validación: correr `--diagnose` con la red presente y comprobar que `grid_v` está por encima de `grid_ok_above`. Si marca 0 con corriente en la calle, la señal no sirve en esa instalación y hay que revisar el modo de salida del inversor antes de fiarse de las alertas (§7.1 del diseño).

- [ ] **Step 3: Actualizar el README**

Borrar del `README.md` el bloque que empieza por `> **Status: design phase.**` y sustituirlo por:

```markdown
## Install

Requires Python 3.11+.

```bash
git clone https://github.com/dlocmot/grid-watch && cd grid-watch
python3 -m venv .venv && .venv/bin/pip install .
cp config.example.toml config.toml
cp .env.example .env      # fill in your Growatt login and ntfy topic
```

Validate the signal before trusting it — with the grid up, `grid_v` must read
above your `ok_above` threshold:

```bash
set -a && . ./.env && set +a
.venv/bin/grid-watch --config config.toml --diagnose
.venv/bin/grid-watch --config config.toml --test-notify
```

Then run it for real, or install it as a service — see
[`deploy/README.md`](deploy/README.md).

## Configuration

Thresholds and timings live in `config.toml`; secrets only ever come from the
environment (`GROWATT_USER`, `GROWATT_PASSWORD`, `NTFY_TOPIC`, `NTFY_TOKEN`).
Your ntfy topic acts as a password — anyone who knows it can read your alerts,
so keep it out of version control.
```

Mantener intacta la sección "Design notes worth stealing": es lo que hace útil el repo a un desconocido.

- [ ] **Step 4: Verificar que la unidad y la suite están sanas**

Run: `systemd-analyze verify deploy/grid-watch.service && .venv/bin/pytest -q`
Expected: sin advertencias de systemd y todos los tests en verde.

- [ ] **Step 5: Commit**

```bash
git add deploy/ README.md
git commit -m "docs: unidad systemd y guía de despliegue"
```

---

## Verificación final

- [ ] `.venv/bin/pytest -v` en verde y CI verde en GitHub.
- [ ] `grid-watch --diagnose` devuelve una lectura con `grid_v` coherente con la red presente.
- [ ] `grid-watch --test-notify` llega al celular con prioridad urgente.
- [ ] `git grep -iE "GROWATT_PASSWORD=.|NTFY_TOPIC=."` no encuentra ningún valor real en archivos versionados.
- [ ] El servicio sobrevive a un reinicio sin duplicar alertas: `systemctl restart grid-watch` con un corte activo no reenvía el aviso.
