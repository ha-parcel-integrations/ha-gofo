# GOFO Express Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-gofo.svg)](https://github.com/ha-parcel-integrations/ha-gofo/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-gofo/total.svg)](https://github.com/ha-parcel-integrations/ha-gofo/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [GOFO Express](https://www.gofo.com) (CIRRO Parcel) parcels. No account is needed — you enter the tracking code yourself, just like on the GOFO Express website. GOFO ships in the US, Canada, Italy, France, Spain and the Netherlands; the two-letter country right after `GF` in the tracking code (`GF<CC>…`) picks the right backend automatically.

> **Pre-1.0:** Canada, Spain and the Netherlands are confirmed to exist on the right backend, but no real parcel from those three markets has ever been checked — only a fictitious code's "not found" response. Their status mapping is unverified until a real parcel proves it; the log will warn once if this affects you. US, Italy and France are confirmed against real, delivered/in-transit parcels.

Part of the [ha-parcel-integrations](https://ha-parcel-integrations.github.io/) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of GOFO Express parcels by tracking code — no account needed
- Covers all six GOFO markets (US, CA, IT, FR, ES, NL) — the country is read straight from the tracking code, nothing to select
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `at_pickup_point` / `delivered` / `problem` / `unknown`), the carrier's own status text and a tracking deep-link
- Summary sensors: incoming parcels, next delivery, parcels awaiting pickup, recently delivered parcels
- Read-only **Deliveries** calendar (see the note below — GOFO has never returned an expected-delivery window, so this is always empty for now)
- `gofo.track_parcel` / `gofo.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

GOFO's API has never returned an estimated delivery window on any observed
parcel (US, IT or FR) — `planned_from`/`planned_to` stay empty, so the "next
delivery" sensor and the deliveries calendar have nothing to show until a
real parcel proves that field's format.

## Requirements

- Home Assistant 2024.12 or newer
- A GOFO Express parcel and its tracking code (from the shipping
  confirmation email or the missed-delivery card) — no account needed

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-gofo` as an **Integration**.
3. Install **GOFO Express** and restart Home Assistant.

### Manual

Copy `custom_components/gofo` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → GOFO Express**. There is nothing to fill in: the hub is created immediately (GOFO Express tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`gofo.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email or the missed-delivery card.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule with nothing to configure.

## Dynamic polling

Polling isn't a setting here — the integration adjusts its own cadence to
what your tracked parcels are actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM), so an overnight update is never missed.
- **Hot (every 15 minutes)** — while any tracked parcel is out for delivery
  today, starting an hour before its delivery window opens (or immediately if
  no window is known yet — this is the fallback that fires in practice for
  GOFO Express, whose tracking payload has never returned a delivery window
  on any observed market).
- **Normal (every 45 minutes)** — for anything else still on its way.
- **Fully paused** — once every tracked parcel has been delivered, or nothing
  is tracked at all, polling stops until you add a parcel back (adding one
  always triggers an immediate check, regardless of the pause).
- A small, fixed per-hub offset is added on top, so not every GOFO Express
  hub out there polls at exactly the same second.

## Removal

Standard HA removal applies: **Settings → Devices & Services → GOFO Express → ⋮ → Delete**. Nothing is stored on GOFO Express's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.gofo_express_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.gofo_express_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.gofo_express_next_delivery` | Earliest expected delivery moment across all active parcels (always unavailable today — see the note above) |
| `sensor.gofo_express_awaiting_pickup` | Number of parcels currently waiting to be collected (`at_pickup_point`), full list under the `parcels` attribute |
| `sensor.gofo_express_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.gofo_express_last_successful_update` | Diagnostic: when GOFO Express was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family. GOFO reports a single `processCode` vocabulary across every market; only these values have ever been observed:

| Status | Meaning |
|---|---|
| `registered` | Shipping label created, not yet handed to GOFO's network (`processCode` 100) |
| `in_transit` | Regional hub / delivery-station network scan (`processCode` 200/201/202/203) |
| `out_for_delivery` | On a delivery vehicle today (`processCode` 208) |
| `at_pickup_point` | Arrived at a pickup facility (`processCode` LS004) — no named pickup point is reported, only the status |
| `delivered` | Delivered (`processCode` 205) |
| `problem` | An exception that asks the recipient/sender to act, e.g. back-to-sorting-center or an incorrect address (`processCode` 204/206) |
| `unknown` | Not yet scanned, or a `processCode`/status GOFO hasn't reported yet (a "Returned" state is known to exist in the UI but has never been seen live — please [open an issue](https://github.com/ha-parcel-integrations/ha-gofo/issues/new) if you hit one) |

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the GOFO Express device):

| Event | When |
|---|---|
| `gofo_parcel_registered` | A new parcel appears in the active list |
| `gofo_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `gofo_parcel_delivered` | A parcel is delivered |
| `gofo_parcel_delivery_time_changed` | The expected delivery window changes — GOFO has never populated one on any observed parcel, so this event does not currently fire |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `gofo.track_parcel` | `tracking_code` | Start tracking a parcel |
| `gofo.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.gofo: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — GOFO Express has not scanned it yet (their API answers `not_found` until the first scan), or the code is wrong. It will pick up automatically once scanned.
- **A status logs "Unrecognised GOFO Express status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-gofo/issues/new) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by GOFO Express, Home Assistant, or any other third party referenced in this project. Please don't contact GOFO Express for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to GOFO Express' terms. Data is sent only to GOFO Express' own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration uses the same public tracking endpoints as the GOFO Express / CIRRO Parcel consumer websites.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
