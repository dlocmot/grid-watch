# Growatt cloud API — field notes

Observed against a **Growatt SPF 5000 ES** (off-grid storage inverter) through
`server.growatt.com`, using the `growattServer` library, on 2026-07-26.

These are the answers to the two open questions in the design spec (§7), plus
what we learned along the way. Field names come from
`storage_params(sn)["storageDetailBean"]`.

## 1. Does `vGrid` actually reflect the public grid? — **Yes**

With the grid present, the bean reported:

| Field | Value | Meaning |
|---|---|---|
| `vGrid` | `218.3` | grid voltage, V |
| `freqGrid` | `59.93` | grid frequency, Hz |
| `pAcInPut` | `3273` | power drawn from the grid, W |
| `pacToGrid` | `0` | power exported, W |

So the primary signal works on this model, and the concern about a legitimate
`vGrid = 0` did not materialise in this installation. The safeguard stays in
the detector anyway: other output-mode configurations may behave differently.

### A better signal exists: the inverter states its own mode

The bean carries an explicit status triplet:

| Field | Value | Meaning |
|---|---|---|
| `status` | `11` | numeric status code |
| `statusText` | `"Bypass"` | short status |
| `SPF5000StatusText` | `"Grid Bypass"` | model-specific status |

While the grid is up and feeding the loads, the inverter reports **Grid
Bypass**. This is a categorical statement from the device rather than a
threshold we invented, so it is a strong corroborating signal. Its values
during an actual outage have not been observed yet — capture them the first
time the grid drops, then consider using status as the primary signal with
`vGrid` as backup.

## 2. Is there a device-side timestamp? — **Yes, the `time` field**

```
"time": "2026-07-26 20:30:29"
```

Format is `%Y-%m-%d %H:%M:%S`. Notes:

- The field is **`time`**, not `lastUpdateTime`. There is also a `calendar`
  object (a serialised Java `Calendar`) carrying the same instant in a much
  more awkward shape — ignore it and read `time`.
- **The timestamp is naive local time of the inverter, not UTC.** Observed
  `20:30:29` while the wall clock in the same timezone read `20:33:02`
  (UTC-5) — i.e. the sample was ~2.5 minutes old, not 5 hours into the future.

### Consequence for any implementation

Never subtract this timestamp from your own UTC clock: you would be off by the
inverter's UTC offset, and mixing naive and aware datetimes raises `TypeError`
in Python anyway. Use device timestamps **only to compare samples with each
other** (has a new sample arrived?), and measure elapsed time with your own
clock, starting from the moment you first observed a given sample. That keeps
the logic correct regardless of how the inverter's clock is configured — or how
far it has drifted.

## Other useful fields

| Field | Meaning |
|---|---|
| `capacity` | battery state of charge, % |
| `vBat` | battery voltage, V |
| `pCharge` / `pDischarge` | battery charge / discharge power, W |
| `outPutPower` | household load, W |
| `outPutVolt` | inverter output voltage, V |
| `loadPercent` | load as % of inverter rating |
| `ppv` | PV input power, W |

## Gotchas that cost real time

1. The library's default User-Agent (`Dalvik/...`) gets a **403 from
   Cloudflare**. Send a browser User-Agent instead.
2. `growattServer` builds a `requests.Session` **with no timeout**. A half-open
   socket hangs the polling thread forever, and any backoff you wrote never
   runs. Patch a default timeout into the session.
3. When the session expires, the API answers with **HTML instead of JSON**.
   Retrying the same call is useless — force a re-login.
