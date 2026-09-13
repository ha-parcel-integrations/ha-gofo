"""Tests for the GOFO Express API client — both transports, both envelopes."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.gofo.api import (
    GOFOExpressApiClient,
    GOFOExpressApiError,
    extract_country,
)

US_CODE = "GFUS01011884214464"
IT_CODE = "GFIT26085094392887"


def _session_returning(status: int, body: object = None) -> MagicMock:
    response = AsyncMock()
    response.status = status
    response.headers = {}
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post = MagicMock(return_value=ctx)
    return session


# ---------------------------------------------------------------------------
# extract_country — the GF<CC> routing key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("GFUS01011884214464", "US"),
        ("GFIT26085094392887", "IT"),
        ("GFCA0001", "CA"),
    ],
)
def test_extract_country_known_prefixes(code, expected):
    # Codes are always stored upper-case (config_flow.normalize_tracking_code
    # runs before anything reaches the API client), so extract_country only
    # ever has to handle upper-case input.
    assert extract_country(code) == expected


def test_extract_country_rejects_non_gofo_shapes():
    assert extract_country("") is None
    assert extract_country(None) is None
    assert extract_country("GF1") is None  # too short
    assert extract_country("GF12345") is None  # digits, not letters, after GF
    assert extract_country("NOTGOFOSHAPED") is None


# ---------------------------------------------------------------------------
# Transport A (cnee-api, US/CA)
# ---------------------------------------------------------------------------


def _transport_a_envelope(items: list[dict] | None = None, *, error: dict | None = None) -> dict:
    return {
        "success": 1,
        "code": 200,
        "msg": "The operation was successful",
        "failCode": None,
        "failReason": None,
        "data": {
            "success": items or [],
            "error": error if error is not None else ({} if items else {"errorCount": 1, "us": [US_CODE]}),
        },
    }


async def test_transport_a_returns_item_on_success():
    item = {"waybillNo": US_CODE, "status": "Delivered"}
    session = _session_returning(200, _transport_a_envelope([item]))
    client = GOFOExpressApiClient(session)

    parcel = await client.async_get_parcel(US_CODE)

    assert parcel == item
    url, kwargs = session.post.call_args
    assert url[0] == "https://www.gofo.com/us/cnee-api/consignee/track/query"
    assert kwargs["json"] == {"numberList": [US_CODE]}
    assert kwargs["headers"]["User-Time-Zone"]


async def test_transport_a_not_found_via_populated_error():
    """Confirmed live shape: empty data.success with a populated data.error."""
    session = _session_returning(200, _transport_a_envelope([]))
    client = GOFOExpressApiClient(session)
    assert await client.async_get_parcel(US_CODE) is None


async def test_transport_a_item_absent_from_success_list_is_not_found():
    other_item = {"waybillNo": "GFUSOTHERCODE00", "status": "Delivered"}
    session = _session_returning(200, _transport_a_envelope([other_item], error={}))
    client = GOFOExpressApiClient(session)
    assert await client.async_get_parcel(US_CODE) is None


async def test_transport_a_raises_on_unsuccessful_envelope_without_error():
    envelope = {
        "success": 0,
        "code": 500,
        "msg": "backend exploded",
        "data": {"success": [], "error": None},
    }
    session = _session_returning(200, envelope)
    client = GOFOExpressApiClient(session)
    with pytest.raises(GOFOExpressApiError) as err:
        await client.async_get_parcel(US_CODE)
    assert "backend exploded" in str(err.value)


# ---------------------------------------------------------------------------
# Transport B (queryTrackV2, IT/FR/ES/NL)
# ---------------------------------------------------------------------------


async def test_transport_b_returns_item_on_success():
    item = {"waybillNo": IT_CODE, "status": "Transit"}
    session = _session_returning(
        200, {"msg": "Operare con successo", "code": 200, "data": [item]}
    )
    client = GOFOExpressApiClient(session)

    parcel = await client.async_get_parcel(IT_CODE)

    assert parcel == item
    url, kwargs = session.post.call_args
    assert url[0] == "https://www.gofo.com/it/open-api/official/track/queryTrackV2"
    assert kwargs["json"] == {"numberList": [IT_CODE]}
    assert kwargs["headers"]["lang"] == "it"


async def test_transport_b_empty_data_array_is_not_found():
    """Confirmed live: {"msg":"Operation successful","code":200,"data":[]}."""
    session = _session_returning(
        200, {"msg": "Operation successful", "code": 200, "data": []}
    )
    client = GOFOExpressApiClient(session)
    assert await client.async_get_parcel(IT_CODE) is None


async def test_transport_b_item_absent_from_data_is_not_found():
    other_item = {"waybillNo": "GFITOTHERCODE00", "status": "Transit"}
    session = _session_returning(
        200, {"msg": "Operation successful", "code": 200, "data": [other_item]}
    )
    client = GOFOExpressApiClient(session)
    assert await client.async_get_parcel(IT_CODE) is None


async def test_transport_b_raises_on_non_200_application_code():
    session = _session_returning(
        200, {"msg": "internal error", "code": 500, "data": None}
    )
    client = GOFOExpressApiClient(session)
    with pytest.raises(GOFOExpressApiError) as err:
        await client.async_get_parcel(IT_CODE)
    assert "internal error" in str(err.value)


# ---------------------------------------------------------------------------
# Routing / unroutable codes
# ---------------------------------------------------------------------------


async def test_unroutable_code_returns_none_without_a_request():
    session = MagicMock()
    client = GOFOExpressApiClient(session)
    assert await client.async_get_parcel("NOTGOFOSHAPED") is None
    session.post.assert_not_called()


async def test_ca_code_routes_to_transport_a():
    session = _session_returning(200, _transport_a_envelope([{"waybillNo": "GFCA0001"}], error={}))
    client = GOFOExpressApiClient(session)
    await client.async_get_parcel("GFCA0001")
    assert "cnee-api" in session.post.call_args[0][0]
    assert "/ca/" in session.post.call_args[0][0]


async def test_es_code_routes_to_transport_b():
    session = _session_returning(
        200, {"msg": "ok", "code": 200, "data": [{"waybillNo": "GFES0001"}]}
    )
    client = GOFOExpressApiClient(session)
    await client.async_get_parcel("GFES0001")
    assert "queryTrackV2" in session.post.call_args[0][0]
    assert "/es/" in session.post.call_args[0][0]


# ---------------------------------------------------------------------------
# Shared transport failure handling (HTTP layer, body parsing, rate limits)
# ---------------------------------------------------------------------------


async def test_raises_on_http_error_status():
    client = GOFOExpressApiClient(_session_returning(500, {}))
    with pytest.raises(GOFOExpressApiError) as err:
        await client.async_get_parcel(US_CODE)
    assert err.value.status_code == 500


async def test_raises_on_unparseable_body():
    client = GOFOExpressApiClient(_session_returning(200, "not json"))
    with pytest.raises(GOFOExpressApiError):
        await client.async_get_parcel(US_CODE)


async def test_raises_on_non_object_body():
    client = GOFOExpressApiClient(_session_returning(200, ["not", "a", "dict"]))
    with pytest.raises(GOFOExpressApiError):
        await client.async_get_parcel(US_CODE)


async def test_429_carries_retry_after_seconds():
    response = AsyncMock()
    response.status = 429
    response.headers = {"Retry-After": "30"}
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post = MagicMock(return_value=ctx)
    client = GOFOExpressApiClient(session)

    with pytest.raises(GOFOExpressApiError) as err:
        await client.async_get_parcel(US_CODE)
    assert err.value.status_code == 429
    assert err.value.retry_after == 30.0


async def test_429_with_http_date_retry_after_is_none():
    response = AsyncMock()
    response.status = 429
    response.headers = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post = MagicMock(return_value=ctx)
    client = GOFOExpressApiClient(session)

    with pytest.raises(GOFOExpressApiError) as err:
        await client.async_get_parcel(US_CODE)
    assert err.value.retry_after is None


async def test_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.post = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = GOFOExpressApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(US_CODE)
