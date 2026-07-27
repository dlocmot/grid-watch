# Deploying grid-watch

Any host that stays online works — a small VPS is the best choice, because it
keeps watching even if the outage also takes down the internet at the site being
monitored. Two options below: **Docker** (simplest) or **systemd**.

Either way, validate before you trust it (step 3 in both).

---

# Option A — Docker

```bash
git clone https://github.com/dlocmot/grid-watch /opt/grid-watch
cd /opt/grid-watch
cp config.example.toml config.toml
cp .env.example .env
chmod 600 .env
editor .env          # Growatt login + a long random ntfy topic
```

Point the state file at the volume, in `config.toml`:

```toml
state_path = "/var/lib/grid-watch/state.json"
```

The container runs as a non-root user, so `config.toml` must be world-readable.
On a hardened host (`umask 027`) the `cp` above creates it mode 640 and the
container fails with `Permission denied` — it holds no secrets, so just:

```bash
chmod 644 config.toml
```

`.env` stays `600`: the Docker CLI reads it on the host as root, never the
container.

Validate the signal and the notification path **before** starting the service —
`--rm` so these one-shot runs leave nothing behind:

```bash
docker compose run --rm grid-watch --config /etc/grid-watch/config.toml --diagnose
docker compose run --rm grid-watch --config /etc/grid-watch/config.toml --test-notify
```

`grid_v` must read above your `ok_above` threshold while the grid is up, and
your phone must buzz. Then:

```bash
docker compose up -d
docker compose logs -f
```

The state volume (`grid-watch-state`) holds the outage state and any alerts that
could not be delivered yet, so recreating the container never loses an alert nor
re-sends one. Logs are capped at 3 × 5 MB.

To upgrade: `git pull && docker compose up -d --build`.

---

# Option B — systemd

## 1. Install

```bash
sudo useradd --system --home /opt/grid-watch --shell /usr/sbin/nologin grid-watch
sudo mkdir -p /opt/grid-watch /etc/grid-watch
sudo chown grid-watch:grid-watch /opt/grid-watch

sudo -u grid-watch git clone https://github.com/dlocmot/grid-watch /opt/grid-watch
sudo -u grid-watch python3 -m venv /opt/grid-watch/.venv
sudo -u grid-watch /opt/grid-watch/.venv/bin/pip install /opt/grid-watch
```

## 2. Configure

```bash
sudo cp /opt/grid-watch/config.example.toml /etc/grid-watch/config.toml
sudo cp /opt/grid-watch/.env.example /etc/grid-watch.env
sudo chmod 600 /etc/grid-watch.env     # holds credentials and the ntfy topic
sudo chown grid-watch:grid-watch /etc/grid-watch.env
sudo editor /etc/grid-watch.env
```

Pick a long, random ntfy topic — anyone who knows it can read your alerts and
publish to them. Install the ntfy app on your phone and subscribe to that same
topic.

## 3. Validate before enabling the service

Do not skip this. Run it **while the grid is up**:

```bash
sudo -u grid-watch env $(sudo cat /etc/grid-watch.env | grep -v '^#' | xargs) \
    /opt/grid-watch/.venv/bin/grid-watch --config /etc/grid-watch/config.toml --diagnose
```

Check that `grid_v` reads above your `ok_above` threshold (printed on stderr).
If it reads 0 while the street clearly has power, the inverter's output mode is
hiding the grid from the API and the alerts would fire forever — fix that before
going further. `status_text` should say something like `Grid Bypass`.

Then confirm the notification path end to end:

```bash
sudo -u grid-watch env $(sudo cat /etc/grid-watch.env | grep -v '^#' | xargs) \
    /opt/grid-watch/.venv/bin/grid-watch --config /etc/grid-watch/config.toml --test-notify
```

Your phone should buzz with an urgent-priority alert.

## 4. Enable the service

```bash
sudo cp /opt/grid-watch/deploy/grid-watch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now grid-watch
journalctl -u grid-watch -f
```

## Notes

- State lives in `/var/lib/grid-watch/state.json` (`StateDirectory=` creates it).
  Point `state_path` in `config.toml` there.
- The service restarts on failure every 30 s and keeps undelivered alerts queued
  in the state file, so a restart never loses an alert nor sends it twice.
- To upgrade: `sudo -u grid-watch git -C /opt/grid-watch pull && sudo systemctl
  restart grid-watch`.
