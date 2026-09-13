"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gofo.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.gofo.parcels import (
    apply_delivered_filter,
    build_history,
    map_item_status,
    map_process_code,
    normalize_parcel,
    parse_iso,
    resolve_status,
    sort_parcels_by_ts,
    to_iso_timestamp,
)

from .payloads import (
    ACTIVE_CODE,
    DELIVERED_CODE,
    active_sample,
    delivered_sample,
    fr_delivered_sample,
    it_transit_sample,
    us_event,
)

# ---------------------------------------------------------------------------
# map_process_code / map_item_status — the single cross-country vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("100", ParcelStatus.REGISTERED),
        ("200", ParcelStatus.IN_TRANSIT),
        ("201", ParcelStatus.IN_TRANSIT),
        ("202", ParcelStatus.IN_TRANSIT),
        ("203", ParcelStatus.IN_TRANSIT),
        ("208", ParcelStatus.OUT_FOR_DELIVERY),
        ("205", ParcelStatus.DELIVERED),
    ],
)
def test_map_process_code_known(code, expected):
    assert map_process_code(code) == expected


def test_map_process_code_missing_is_none():
    """None/absent code means no mapping at all, never a silent 'unknown'."""
    assert map_process_code(None) is None
    assert map_process_code("") is None


def test_map_process_code_unmapped_warns_once(caplog):
    """'Alert'/'Returned' processCodes are unseen and must stay unmapped."""
    assert map_process_code("999") is None
    assert map_process_code("999") is None
    assert caplog.text.count("999") == 1
    assert "issues/new" in caplog.text


def test_map_item_status_known_values():
    """Only Delivered (US/FR) and Transit (IT) have ever been observed live."""
    assert map_item_status("Delivered") == ParcelStatus.DELIVERED
    assert map_item_status("Transit") == ParcelStatus.IN_TRANSIT


def test_map_item_status_unseen_values_stay_unknown_with_warning(caplog):
    """Processing/Alert/Returned are unseen on either transport."""
    assert map_item_status("Alert") == ParcelStatus.UNKNOWN
    assert "Alert" in caplog.text
    assert "issues/new" in caplog.text


def test_map_item_status_missing_is_unknown_silently(caplog):
    assert map_item_status(None) == ParcelStatus.UNKNOWN
    assert caplog.text == ""


def test_resolve_status_prefers_process_code_over_item_status():
    """processCode is the primary, cross-country key — item status is only a fallback."""
    raw = active_sample()
    # lastTrackEvent's processCode (208) must win even though item ``status``
    # says the coarser "Transit".
    status, raw_status = resolve_status(raw)
    assert status == ParcelStatus.OUT_FOR_DELIVERY
    assert raw_status == "Out for Delivery"


def test_resolve_status_falls_back_to_item_status_when_process_code_unmapped():
    raw = active_sample()
    raw["lastTrackEvent"]["processCode"] = "999"  # unseen/unmapped
    raw["status"] = "Transit"
    status, raw_status = resolve_status(raw)
    assert status == ParcelStatus.IN_TRANSIT
    assert raw_status == "Transit"


def test_resolve_status_falls_back_to_first_event_without_last_track_event():
    raw = active_sample()
    del raw["lastTrackEvent"]
    status, _ = resolve_status(raw)
    # trackEventList[0] is still the "Out for Delivery" (208) entry.
    assert status == ParcelStatus.OUT_FOR_DELIVERY


def test_resolve_status_placeholder_has_no_events():
    status, raw_status = resolve_status({"waybillNo": "GFUS0"})
    assert status == ParcelStatus.UNKNOWN
    assert raw_status is None


# ---------------------------------------------------------------------------
# timestamp helpers — offset-aware, both transports' offset styles
# ---------------------------------------------------------------------------


def test_parse_iso_handles_us_and_eu_offsets():
    us = parse_iso("2025-10-16T13:54:05.000-0700")
    eu = parse_iso("2026-03-31T12:28:21.000+0200")
    assert us.utcoffset() == timedelta(hours=-7)
    assert eu.utcoffset() == timedelta(hours=2)


def test_parse_iso_naive_assumed_utc():
    assert parse_iso("2026-03-31T12:28:21").tzinfo == timezone.utc


def test_parse_iso_garbage_and_none():
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_round_trips():
    assert to_iso_timestamp("2025-10-16T13:54:05.000-0700") is not None
    assert to_iso_timestamp(None) is None


# ---------------------------------------------------------------------------
# build_history — the newest-first -> oldest-first trap
# ---------------------------------------------------------------------------


def test_build_history_reverses_newest_first_to_oldest_first():
    """GOFO's own trackEventList is newest-first on both transports — trap."""
    history = build_history(delivered_sample()["trackEventList"])
    assert history[0]["status"] == ParcelStatus.REGISTERED  # "100" event, oldest
    assert history[-1]["status"] == ParcelStatus.DELIVERED  # "205" event, newest


def test_build_history_caps_to_max_events():
    events = [
        us_event("200", f"2026-04-{day:02d}T10:00:00.000-0700", "moved")
        for day in range(1, 26)
    ]
    assert len(build_history(events, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history(["not-a-dict"]) == []


def test_build_history_falls_back_to_process_code_without_content():
    history = build_history(
        [us_event("100", "2025-10-04T15:53:59.000-0700", "")]
    )
    assert history[0]["raw_status"] == "100"


def test_build_history_unmapped_process_code_is_none_not_unknown(caplog):
    """History keeps ``null`` rather than ``unknown`` so a consumer can tell
    "no mapping" from "mapped to unknown"."""
    history = build_history(
        [us_event("999", "2025-10-04T15:53:59.000-0700", "mystery scan")]
    )
    assert history[0]["status"] is None


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    """Every declared CAPABILITIES entry must come true somewhere in a sample."""
    delivered = normalize_parcel(delivered_sample())
    with_history = normalize_parcel(delivered_sample(), include_history=True)

    if "weight" in CAPABILITIES:
        assert delivered["weight"] is not None
    if "url" in CAPABILITIES:
        assert delivered["url"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"] is not None
    # Never claim what GOFO's payload has never populated on either
    # transport (BUILD_PLAN.md section 3): dimensions, delivery_window and
    # pickup_point stay out of CAPABILITIES entirely.
    assert "dimensions" not in CAPABILITIES
    assert "delivery_window" not in CAPABILITIES
    assert "pickup_point" not in CAPABILITIES


def test_normalize_delivered_us_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "GOFO Express"
    assert parcel["barcode"] == DELIVERED_CODE
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Delivered, Door/Yard"
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] is not None
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["url"] == f"https://www.gofo.com/us/track?number={DELIVERED_CODE}"
    assert parcel["weight"] == 0.6030
    assert parcel["dimensions"] is None
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_history_is_opt_in_and_capped_and_ordered():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 9
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED
    assert parcel["history"][-1]["status"] == ParcelStatus.DELIVERED


def test_normalize_active_parcel_is_out_for_delivery():
    parcel = normalize_parcel(active_sample())
    assert parcel["barcode"] == ACTIVE_CODE
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_normalize_it_transit_parcel_uses_waybill_not_tracking_number():
    """A real IT capture had a *different* trackingNumber from waybillNo."""
    raw = it_transit_sample()
    parcel = normalize_parcel(raw)
    assert parcel["barcode"] == raw["waybillNo"]
    assert parcel["barcode"] != raw["trackingNumber"]
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["url"].startswith("https://www.gofo.com/it/")


def test_normalize_fr_delivered_parcel():
    parcel = normalize_parcel(fr_delivered_sample())
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["url"].startswith("https://www.gofo.com/fr/")


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned code still yields a full parcel dict."""
    parcel = normalize_parcel({"waybillNo": "GFUS00000000"})
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["weight"] is None
    assert parcel["history"] is None


def test_normalize_keeps_raw_payload():
    raw = active_sample()
    assert normalize_parcel(raw)["raw"] is raw


def test_normalize_unroutable_code_has_no_url():
    """A code with no recognised GF<CC> prefix can't build a country deep link... falls back to /us/."""
    raw = active_sample()
    raw["waybillNo"] = "NOTGOFOSHAPED"
    parcel = normalize_parcel(raw)
    assert parcel["barcode"] == "NOTGOFOSHAPED"
    assert parcel["url"] == "https://www.gofo.com/us/track?number=NOTGOFOSHAPED"


def test_normalize_warns_once_for_unconfirmed_country_payload(caplog):
    """CA/ES/NL are transport-confirmed only — a real payload must warn once."""
    raw = it_transit_sample()
    raw["waybillNo"] = "GFES26085000000099"
    normalize_parcel(raw)
    normalize_parcel(raw)
    assert caplog.text.count("ES") >= 1
    assert caplog.text.count("issues/new") == 1


def test_normalize_does_not_warn_for_confirmed_countries(caplog):
    normalize_parcel(delivered_sample())  # US
    normalize_parcel(it_transit_sample())  # IT
    assert "unverified" not in caplog.text


def test_normalize_placeholder_does_not_warn_for_unconfirmed_country(caplog):
    """A not-yet-scanned/unknown code must not trigger the payload-arrived warning."""
    normalize_parcel({"waybillNo": "GFES00000000"})
    assert caplog.text == ""


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00-0700"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00-0700"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00-0700"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00-0700"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
