"""Sample GOFO Express API payloads shared by the test modules.

These are redacted from real, recipient-authorised captures against both
confirmed transports on 2026-09-13 (see carrier-research/gofo/api/ —
private, never copied here): a delivered US parcel (transport A, cnee-api)
and an in-transit IT parcel (transport B, queryTrackV2). Tracking/waybill
numbers, city/province and exact processLocation were replaced with
placeholders; the event count, processCode sequence and offset-aware
timestamp shapes are otherwise unchanged.

Keep fixtures in one module rather than inline in each test — when the
payload shape turns out to need adjusting, there is exactly one place to
fix it.
"""
from __future__ import annotations

ACTIVE_CODE = "GFUS01011000000001"
DELIVERED_CODE = "GFUS01011000000002"
IT_TRANSIT_CODE = "GFIT26085000000001"
FR_DELIVERED_CODE = "GFFR26083000000001"


def us_event(process_code: str, process_date: str, content: str, *, city=None) -> dict:
    """One entry of transport A's (cnee-api, US/CA) event timeline."""
    return {
        "processDate": process_date,
        "processContent": content,
        "processLocation": city,
        "processCode": process_code,
        "processProvince": "CA" if city else None,
        "processCity": city,
        "processDeptId": 220 if city else None,
        "processSecondCode": None,
        "processTimeZone": "America/Los_Angeles",
        "processTimeZoneMapping": None,
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A representative transport-A (US) response for a delivered parcel.

    Redacted from a real recipient-authorised ``GFUS…`` capture, 2026-09-13:
    9 events, "Shipping Label Created" (100) through hub scans
    (200/201/202/203) to "Out for Delivery" (208) and "Delivered" (205).
    """
    events = [
        us_event("205", "2025-10-16T13:54:05.000-0700", "Delivered, Door/Yard", city="Sunnyvale"),
        us_event("208", "2025-10-16T08:57:20.000-0700", "Out for Delivery", city="Newark"),
        us_event("203", "2025-10-16T08:52:20.000-0700", "Departed GOFO Delivery Station", city="Newark"),
        us_event("201", "2025-10-16T08:47:20.000-0700", "Arrived at GOFO Delivery Station", city="Newark"),
        us_event("200", "2025-10-16T08:42:20.000-0700", "Departed GOFO Regional Hub", city="Newark"),
        us_event("202", "2025-10-15T16:54:42.000-0700", "Arrived at GOFO Regional Hub", city="Newark"),
        us_event("200", "2025-10-15T00:04:44.000-0700", "Departed GOFO Regional Hub", city="Vernon"),
        us_event("202", "2025-10-14T19:25:06.000-0700", "Arrived at GOFO Regional Hub", city="Vernon"),
        us_event("100", "2025-10-04T15:53:59.000-0700", "Shipping Label Created"),
    ]
    return {
        "waybillNo": code,
        "trackingNumber": code,
        "status": "Delivered",
        "trackEventCount": len(events),
        "frCountry": "USA",
        "toCountry": "USA",
        "thirdNo": None,
        "serviceName": "GOFO EXPRESS",
        "weight": 0.6030,
        "intervalDays": "12.04",
        "intervalWorkdays": "5.04",
        "createTime": "2025-10-04T15:53:59.000-0700",
        "lastTrackEvent": events[0],
        "trackEventList": events,
        "estimatedArrivalTime": None,
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """An out-for-delivery transport-A parcel (processCode 208, no delivery yet)."""
    sample = delivered_sample(code)
    events = sample["trackEventList"][1:]  # drop the "Delivered" (205) entry
    sample.update(
        {
            "status": "Transit",
            "trackEventCount": len(events),
            "lastTrackEvent": events[0],
            "trackEventList": events,
        }
    )
    return sample


def it_transit_sample(code: str = IT_TRANSIT_CODE) -> dict:
    """A representative transport-B (IT) response for an in-transit parcel.

    Redacted from a real recipient-authorised ``GFIT…`` capture, 2026-09-13:
    2 events, "in preparazione" (100) then a transfer-centre scan (202). Note
    the carrier's own ``trackingNumber`` here is a *different* local id from
    ``waybillNo`` — never used as the barcode, see parcels.py.
    """
    events = [
        {
            "processDate": "2026-03-31T12:28:21.000+0200",
            "processContent": "Registrato presso il centro di trasferimento",
            "processLocation": "Milano",
            "processCode": "202",
            "mainContent": "Registrato presso il centro di trasferimento",
            "subContent": None,
            "trackStatus": "2",
        },
        {
            "processDate": "2026-03-26T06:16:22.000+0100",
            "processContent": "Il pacco e in fase di preparazione da parte del mittente",
            "processLocation": None,
            "processCode": "100",
            "mainContent": "Il pacco e in fase di preparazione da parte del mittente",
            "subContent": None,
            "trackStatus": "2",
        },
    ]
    return {
        "waybillNo": code,
        "trackingNumber": "BG-2603249D000000",
        "status": "Transit",
        "trackEventCount": len(events),
        "frCountry": "IT",
        "toCountry": "IT",
        "thirdNo": None,
        "serviceName": "CIRRO Parcel",
        "weight": 0.5900,
        "intervalDays": None,
        "intervalWorkdays": None,
        "expectedDeliveryTime": None,
        "edtType": None,
        "edtStartTime": None,
        "edtEndTime": None,
        "createTime": "2026-03-26T06:16:22.000+0100",
        "number": None,
        "sign": None,
        "podImgList": None,
        "lastTrackEvent": events[0],
        "trackEventList": events,
        "noticeContents": None,
    }


def fr_delivered_sample(code: str = FR_DELIVERED_CODE) -> dict:
    """A transport-B (FR) delivered parcel, same shape as ``it_transit_sample``."""
    sample = it_transit_sample(code)
    events = [
        {
            "processDate": "2026-03-27T09:10:00.000+0100",
            "processContent": "Livre",
            "processLocation": "Paris",
            "processCode": "205",
            "mainContent": "Livre",
            "subContent": None,
            "trackStatus": "3",
        },
        *sample["trackEventList"],
    ]
    sample.update(
        {
            "status": "Delivered",
            "frCountry": "FR",
            "toCountry": "FR",
            "trackEventCount": len(events),
            "lastTrackEvent": events[0],
            "trackEventList": events,
        }
    )
    return sample
