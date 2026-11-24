"""The parcel contract's checks, run over every normalised sample."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.gofo.const import ParcelStatus
from custom_components.gofo.parcels import (
    is_pickup,
    normalize_parcel,
    planned_for_status,
    planned_window,
)

from .payloads import (
    active_sample,
    delivered_sample,
    fr_awaiting_pickup_sample,
    fr_delivered_sample,
    fr_problem_sample,
    nl_delivered_sample,
)

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
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


def _collected() -> dict:
    """An FR pickup-facility parcel whose newest event is the delivery."""
    sample = fr_awaiting_pickup_sample()
    delivery = {
        "processDate": "2026-09-04T10:00:00.000+0200",
        "processContent": "Delivered",
        "processLocation": "Some City",
        "processCode": "205",
        "trackStatus": "3",
    }
    events = [delivery, *sample["trackEventList"]]
    sample.update(
        {"status": "Delivered", "lastTrackEvent": delivery, "trackEventList": events}
    )
    return sample


def _unmapped() -> dict:
    sample = fr_problem_sample()
    sample["status"] = "Returned"
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "nl_delivered": nl_delivered_sample,
    "fr_delivered": fr_delivered_sample,
    "active": active_sample,
    "at_pickup_point": fr_awaiting_pickup_sample,
    "collected": _collected,
    "problem": fr_problem_sample,
    "unmapped_item_status": _unmapped,
    "bare": lambda: {"waybillNo": "GFUS000000001"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_the_pickup_facility_status_is_a_pickup_parcel():
    parcel = normalize_parcel(fr_awaiting_pickup_sample())
    assert parcel["status"] is ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(_collected())
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] is not None
    assert parcel["pickup"] is True


def test_a_home_delivery_is_not_pickup():
    parcel = normalize_parcel(fr_delivered_sample())
    assert parcel["delivered"] is True
    assert parcel["pickup"] is False


def test_pickup_comes_from_the_events_without_the_history_option():
    parcel = normalize_parcel(_collected(), include_history=False)
    assert parcel["history"] is None
    assert parcel["pickup"] is True


def test_is_pickup_is_sticky_and_prefers_the_route_flag():
    assert is_pickup(True, ParcelStatus.IN_TRANSIT) is True
    assert is_pickup(None, ParcelStatus.IN_TRANSIT) is False
    assert is_pickup(None, ParcelStatus.DELIVERED, [ParcelStatus.AT_PICKUP_POINT])


@pytest.mark.parametrize(
    "status",
    [ParcelStatus.DELIVERED, ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING],
)
def test_planned_for_status_empties_the_window(status):
    assert planned_for_status(status, "2099-01-01", "2099-01-02") == (None, None)
    assert planned_for_status(ParcelStatus.IN_TRANSIT, "a", "b") == ("a", "b")


def test_a_moment_ends_at_the_end_of_that_day():
    zone = ZoneInfo("Europe/Amsterdam")
    start, end = planned_window("2099-04-29T13:00:00+02:00", None, zone=zone)
    stop = datetime.fromisoformat(end)
    assert (stop.hour, stop.minute, stop.second, stop.microsecond) == (23, 59, 59, 0)
    assert stop.date() == datetime.fromisoformat(start).date()


def test_a_date_covers_the_whole_day():
    zone = ZoneInfo("Europe/Amsterdam")
    start, end = planned_window("2099-04-29", None, zone=zone)
    first = datetime.fromisoformat(start)
    last = datetime.fromisoformat(end)
    assert (first.hour, first.minute, first.second) == (0, 0, 0)
    assert (last.hour, last.minute, last.second, last.microsecond) == (23, 59, 59, 0)
