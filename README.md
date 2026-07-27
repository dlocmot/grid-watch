# grid-watch

Push a notification to your phone when the **public grid goes down** — detected
through your off-grid solar inverter, which keeps the lights on and therefore
makes the outage invisible.

Built for a **Growatt SPF 5000 ES** read through the Growatt cloud, with a
one-method data-source interface so a local Modbus reader can replace the cloud
later without touching anything else.

> **Status: design phase.** No implementation yet — the design is written and
> approved in
> [`docs/superpowers/specs/2026-07-26-grid-watch-design.md`](docs/superpowers/specs/2026-07-26-grid-watch-design.md)
> (in Spanish). Code lands next.

## What it will do

Watch the grid voltage the inverter reports and send a phone notification (via
[ntfy](https://ntfy.sh)) on four events:

- **Grid down** — the public grid failed; the house is running on battery/solar.
- **Grid restored** — with the total outage duration.
- **Battery critical** — SOC dropped below a threshold while still on battery.
- **Inverter silent** — no fresh data, which may mean the outage also took down
  your internet.

Designed to run on a small VPS, so a house-wide internet failure is detected
rather than blinding the monitor.

## Design notes worth stealing

If you are writing anything against the Growatt cloud, three hard-won details
are documented in the spec and will be in the code:

- The library's default User-Agent gets a **403 from Cloudflare** — send a
  browser one.
- `growattServer` uses a `requests.Session` **without a timeout**; a half-open
  socket hangs your process forever and your backoff never fires.
- When the API answers with something that is not JSON, the session is dead —
  force a re-login instead of retrying the call.

## License

MIT — see [`LICENSE`](LICENSE).
