# grid-watch

Push a notification to your phone when the **public grid goes down** — detected
through your off-grid solar inverter, which keeps the lights on and therefore
makes the outage invisible.

Built for a **Growatt SPF 5000 ES** read through the Growatt cloud, with a
one-method data-source interface so a local Modbus reader can replace the cloud
later without touching anything else.

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

## What it does

Watches the grid voltage the inverter reports and sends a phone notification
(via [ntfy](https://ntfy.sh)) on four events:

- **Grid down** — the public grid failed; the house is running on battery/solar.
- **Grid restored** — with the total outage duration.
- **Battery critical** — SOC dropped below a threshold while still on battery.
- **Inverter silent** — no fresh data, which may mean the outage also took down
  your internet.

Designed to run on a small VPS, so a house-wide internet failure is detected
rather than blinding the monitor.

## Design notes worth stealing

If you are writing anything against the Growatt cloud, these are documented in
full in [`docs/api-notes.md`](docs/api-notes.md), with the real field names and
values observed on an SPF 5000 ES:

- The library's default User-Agent gets a **403 from Cloudflare** — send a
  browser one.
- `growattServer` uses a `requests.Session` **without a timeout**; a half-open
  socket hangs your process forever and your backoff never fires.
- When the API answers with something that is not JSON, the session is dead —
  force a re-login instead of retrying the call.
- The device timestamp lives in `time`, and it is **naive local time of the
  inverter**, not UTC. Never subtract it from your own clock; use it only to
  tell one sample from the next.

## License

MIT — see [`LICENSE`](LICENSE).
