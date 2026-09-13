"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    UNCONFIRMED_COUNTRIES,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet, or an unconfirmed country
# that finally returned a populated payload.
#
# The ``?template=`` parameter matters: without it the link opens a blank
# form, and the report comes back missing the version and the log line we
# need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-gofo/issues/new"
    "?template=unrecognised_status.yml"
)

# Confirmed on both transports 2026-09-13 (US, IT, FR real parcels): a single
# processCode vocabulary shared across every country. 208/205 are the only
# terminal-adjacent/terminal codes seen on the original happy-path capture;
# "Returned" (and its processCode) is still unseen on any transport and must
# stay unknown.
#
# 2026-09-13: 204/206/LS004 confirmed live on a real, recipient-authorised FR
# parcel (IT for 204/206) that hit an exception branch — "back to sorting
# center" and "delivery failed: incorrect address" are both PROBLEM, not
# RETURNING, since neither says the parcel is actually headed back to the
# sender (both ask the recipient/sender to act, e.g. confirm a new delivery
# or correct the address). LS004 ("Arrived at pickup facility") is
# AT_PICKUP_POINT; its processLocation is a city name, not a named pickup
# point, so pickup_point stays unpopulated per CAPABILITIES.
_PROCESS_CODE_MAP: dict[str, ParcelStatus] = {
    "100": ParcelStatus.REGISTERED,
    "200": ParcelStatus.IN_TRANSIT,
    "201": ParcelStatus.IN_TRANSIT,
    "202": ParcelStatus.IN_TRANSIT,
    "203": ParcelStatus.IN_TRANSIT,
    "204": ParcelStatus.PROBLEM,
    "206": ParcelStatus.PROBLEM,
    "208": ParcelStatus.OUT_FOR_DELIVERY,
    "LS004": ParcelStatus.AT_PICKUP_POINT,
    "205": ParcelStatus.DELIVERED,
}

# Item-level ``status`` is a coarse UI fallback, only consulted when the
# latest event's processCode is missing or unmapped. Only "Delivered"
# (US/FR) and "Transit" (IT) have ever been observed live; "Processing",
# "Alert" and "Returned" are unseen on either transport and must stay
# unknown, same as an unmapped processCode.
_ITEM_STATUS_MAP: dict[str, ParcelStatus] = {
    "Delivered": ParcelStatus.DELIVERED,
    "Transit": ParcelStatus.IN_TRANSIT,
}

# Status codes/text we have already warned about, so each unmapped value is
# logged only once per HA session instead of on every poll.
_unmapped_logged: set[str] = set()
_unconfirmed_country_logged: set[str] = set()
_eta_field_logged = False
_weight_shape_logged = False

# The four fields observed to always be null on every payload seen so far,
# on either transport. ``normalize_parcel`` never reads these into
# ``planned_from``/``planned_to`` — a delivery window has never been proven
# to have a stable format, so wiring it up would be a guess. This list only
# drives the one-shot detection warning below.
_ETA_FIELDS = (
    "estimatedArrivalTime",  # transport A
    "expectedDeliveryTime",  # transport B
    "edtStartTime",  # transport B
    "edtEndTime",  # transport B
)


def _warn_unmapped(kind: str, value: str) -> None:
    """Log an unmapped processCode/status once, with a copy-paste issue link."""
    key = f"{kind}:{value}"
    if key in _unmapped_logged:
        return
    _unmapped_logged.add(key)
    _LOGGER.warning(
        "Unrecognised GOFO Express %s — help us map it. Open an issue and "
        "paste this line: %s\n  %s=%s -> reported as 'unknown'",
        kind,
        NEW_ISSUE_URL,
        kind,
        value,
    )


def warn_unconfirmed_country(country: str) -> None:
    """Log once per country: a payload arrived for a transport-only-confirmed market.

    CA, ES and NL have only ever returned a fictitious code's not-found
    envelope — their status map and item-field optionality are unverified
    until a real parcel actually comes back. This fires the first time one
    does, so the map can be confirmed from an issue report rather than
    assumed correct. Privacy-safe: logs only the country, never the tracking
    code.
    """
    if country in _unconfirmed_country_logged:
        return
    _unconfirmed_country_logged.add(country)
    _LOGGER.warning(
        "GOFO Express returned a populated payload for %s, a country whose "
        "transport was confirmed only against a fictitious code before now. "
        "Its status map and item fields are unverified — please open an "
        "issue and paste this line so they can be confirmed: %s",
        country,
        NEW_ISSUE_URL,
    )


def warn_eta_field_arrived(keys: list[str]) -> None:
    """Log once, ever: a delivery-window field finally came back non-null.

    Every ETA-capable field has been null on every payload observed so far,
    on both transports, so ``planned_from``/``planned_to`` are never
    populated from them. This fires the first time that stops being true, so
    the field's format can be confirmed from an issue report before wiring it
    up. Privacy-safe: logs only which keys arrived, never their values (a
    delivery window can narrow down a home address).
    """
    global _eta_field_logged
    if _eta_field_logged:
        return
    _eta_field_logged = True
    _LOGGER.warning(
        "GOFO Express returned a non-null delivery-window field (%s) for the "
        "first time — planned_from/planned_to are not populated from it yet. "
        "Please open an issue and paste this line (without the value) so the "
        "format can be confirmed: %s",
        ", ".join(keys),
        NEW_ISSUE_URL,
    )


def normalize_weight(value: object) -> float | None:
    """Coerce the raw ``weight`` field to a float, or ``None``.

    Kilograms-as-float is confirmed on US, IT and FR real payloads
    (``0.6030``, ``0.5900``); CA/ES/NL have never returned a real parcel so
    their weight unit/type is assumed, not confirmed — see
    ``UNCONFIRMED_COUNTRIES``. Coerce defensively rather than trust the type,
    and warn once if a value shows up that isn't already a plain number.
    """
    global _weight_shape_logged
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not _weight_shape_logged:
        _weight_shape_logged = True
        _LOGGER.warning(
            "GOFO Express returned a non-numeric weight value — help us "
            "confirm the shape. Open an issue and paste this line: %s\n"
            "  weight type=%s -> reported as unknown/dropped",
            NEW_ISSUE_URL,
            type(value).__name__,
        )
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def map_process_code(code: str | None) -> ParcelStatus | None:
    """Map a ``processCode`` to a canonical status, or ``None`` if unmapped."""
    if not code:
        return None
    mapped = _PROCESS_CODE_MAP.get(str(code))
    if mapped is not None:
        return mapped
    _warn_unmapped("processCode", str(code))
    return None


def map_item_status(status: str | None) -> ParcelStatus:
    """Map the coarse item ``status`` fallback, warning once if unrecognised."""
    if not status:
        return ParcelStatus.UNKNOWN
    mapped = _ITEM_STATUS_MAP.get(status)
    if mapped is not None:
        return mapped
    _warn_unmapped("status", status)
    return ParcelStatus.UNKNOWN


def resolve_status(raw: dict) -> tuple[ParcelStatus, str | None]:
    """Return ``(status, raw_status)`` for a parcel item.

    ``processCode`` on the latest event (``lastTrackEvent``, falling back to
    the first entry of ``trackEventList``) is the primary, cross-country key;
    the coarse item-level ``status`` string is only a fallback when no event
    carries a mapped processCode — see the module docstring's map for what
    has and hasn't been observed live.
    """
    last_event = raw.get("lastTrackEvent")
    if not isinstance(last_event, dict):
        events = raw.get("trackEventList")
        last_event = events[0] if isinstance(events, list) and events else {}

    process_code = last_event.get("processCode") if isinstance(last_event, dict) else None
    mapped = map_process_code(process_code)
    if mapped is not None:
        raw_status = last_event.get("processContent") or process_code
        return mapped, raw_status

    item_status = raw.get("status")
    return map_item_status(item_status), item_status


def parse_iso(value: str | None) -> datetime | None:
    """Parse GOFO's ``processDate``/``createTime`` (ISO with an explicit offset).

    Both transports stamp with an explicit numeric offset (``-0700`` on
    transport A, ``+0100``/``+0200`` on transport B) rather than a ``Z`` or a
    colon-separated offset — ``datetime.fromisoformat`` handles both forms
    directly on Python's current parser. Naive values (should not occur, but
    never trust a carrier absolutely) are treated as UTC so a mixed list
    always sorts without crashing.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_iso_timestamp(value: str | None) -> str | None:
    """Return an ISO 8601 string for a GOFO offset-aware timestamp field."""
    parsed = parse_iso(value)
    return parsed.isoformat() if parsed else None


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from ``trackEventList``.

    Each entry is ``{timestamp, status, raw_status}``. GOFO's own event list
    is newest-first on both transports (confirmed live) — this reverses it to
    the suite's oldest-to-newest contract before capping to the most recent
    ``max_events``.
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("processDate"))
        entry = {
            "timestamp": timestamp,
            "status": map_process_code(event.get("processCode")),
            "raw_status": event.get("processContent") or event.get("processCode"),
        }
        if timestamp is None:
            unparseable.append(entry)
        else:
            parseable.append((parse_iso(event.get("processDate")), entry))
    # GOFO's own list is newest-first; sorting by parsed timestamp both
    # reverses it to oldest-first and tolerates a carrier that ever ships it
    # out of order.
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def tracking_url(tracking_code: str | None, country: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel.

    Falls back to ``www`` when the country can't be determined (should not
    happen for a parcel that made it through normalize_parcel at all).
    """
    if not tracking_code:
        return None
    return TRACKING_URL.format(cc=(country or "us").lower(), tracking_code=tracking_code)


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    ``waybillNo`` is the tracking code the user entered — api.py already
    filters both transports' result arrays down to the item whose
    ``waybillNo`` matches, so this is always the right barcode. GOFO's own
    ``trackingNumber`` field is a *different*, carrier-internal identifier on
    transport B (observed distinct from ``waybillNo`` on a real IT parcel)
    and is never used here — it is redacted from diagnostics instead.

    ETA fields (``estimatedArrivalTime`` on transport A;
    ``expectedDeliveryTime``/``edtStartTime``/``edtEndTime`` on transport B)
    were null on every observed parcel, so ``planned_from``/``planned_to``
    stay unpopulated — do not wire them up until a fixture actually proves
    their format. The first time any of those fields comes back non-null,
    :func:`warn_eta_field_arrived` logs a one-shot warning so that fixture
    can be requested.
    """
    from .api import extract_country  # local import: avoids a module cycle

    tracking_code = raw.get("waybillNo")
    country = extract_country(tracking_code) if tracking_code else None
    has_payload = bool(raw.get("trackEventList") or raw.get("lastTrackEvent"))
    if country in UNCONFIRMED_COUNTRIES and has_payload:
        warn_unconfirmed_country(country)

    arrived_eta_fields = [key for key in _ETA_FIELDS if raw.get(key) is not None]
    if arrived_eta_fields:
        warn_eta_field_arrived(arrived_eta_fields)

    status, raw_status = resolve_status(raw)
    delivered = status is ParcelStatus.DELIVERED

    return {
        "carrier": "GOFO Express",
        "barcode": tracking_code,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": raw_status,
        "delivered": delivered,
        "delivered_at": to_iso_timestamp(_delivered_event_date(raw)) if delivered else None,
        "planned_from": None,
        "planned_to": None,
        "pickup": status is ParcelStatus.AT_PICKUP_POINT,
        "pickup_point": None,
        "url": tracking_url(tracking_code, country),
        "weight": normalize_weight(raw.get("weight")),
        "dimensions": None,
        "history": build_history(raw.get("trackEventList")) if include_history else None,
        "raw": raw,
    }


def _delivered_event_date(raw: dict) -> str | None:
    """Return the ``processDate`` of the event that carries processCode 205.

    Falls back to ``lastTrackEvent``'s date (the delivered call already
    established the latest event *is* the delivery) when no event in the
    list carries the terminal code — e.g. a payload with only
    ``lastTrackEvent`` and no ``trackEventList``.
    """
    for event in raw.get("trackEventList") or []:
        if isinstance(event, dict) and str(event.get("processCode")) == "205":
            return event.get("processDate")
    last_event = raw.get("lastTrackEvent")
    if isinstance(last_event, dict):
        return last_event.get("processDate")
    return None


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
