"""GOFO Express public tracking API client.

GOFO is a country-split code carrier: a tracking code is
``GF<CC><digits>``, and ``<CC>`` picks one of two mutually exclusive,
keyless JSON transports (see ``const.py`` for the full write-up):

* Transport A ("cnee-api", US/CA) — envelope
  ``{success, code, msg, failCode, failReason, data: {success: [...], error: {...}}}``.
  An empty ``data.success`` with a populated ``data.error`` (keyed by country
  prefix) is track-not-found; a code otherwise absent from ``data.success``
  is treated the same way.
* Transport B ("queryTrackV2", IT/FR/ES/NL) — flatter envelope
  ``{msg, code, data: [...]}`` with ``data`` a direct array. An empty array is
  track-not-found.

Both transports are called with a single-item ``numberList`` even though the
UI supports up to 100 — this integration polls one tracking code at a time.
Non-JSON bodies, malformed envelopes and any 4xx/5xx raise
:class:`GOFOExpressApiError`; network errors propagate as
``aiohttp.ClientError`` for the coordinator to wrap.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import (
    TRANSPORT_A_COUNTRIES,
    TRANSPORT_A_TIME_ZONE,
    TRANSPORT_A_URL,
    TRANSPORT_B_COUNTRIES,
    TRANSPORT_B_LANG,
    TRANSPORT_B_URL,
)

_LOGGER = logging.getLogger(__name__)


class GOFOExpressApiError(Exception):
    """Raised when a GOFO Express API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"GOFO Express API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


def extract_country(tracking_code: str) -> str | None:
    """Return the two-letter country right after the ``GF`` prefix, or ``None``.

    A code that doesn't match ``GF<CC>…`` (wrong prefix, or fewer than two
    letters following it) can't be routed to a transport at all — the caller
    reports it as not-found rather than guessing. This is a structural
    extraction, not the "speculative full regex" the suite avoids: we never
    validate the digits that follow.
    """
    if not tracking_code or len(tracking_code) < 4 or tracking_code[:2] != "GF":
        return None
    cc = tracking_code[2:4]
    return cc if cc.isalpha() else None


class GOFOExpressApiClient:
    """Client for GOFO Express's two country-split tracking transports."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details.

        Returns the raw per-parcel item dict for a known parcel, or ``None``
        when the code is unrouteable, unknown or not yet scanned. Any other
        failure envelope, non-2xx status or unparseable body raises
        :class:`GOFOExpressApiError`; network errors propagate as
        ``aiohttp.ClientError``.
        """
        country = extract_country(tracking_code)
        if country in TRANSPORT_A_COUNTRIES:
            return await self._async_get_transport_a(tracking_code, country)
        if country in TRANSPORT_B_COUNTRIES:
            return await self._async_get_transport_b(tracking_code, country)
        # No known country prefix — cannot pick a transport. Not an error:
        # the code is simply not one GOFO can be asked about.
        _LOGGER.warning(
            "GOFO Express tracking code has no recognised country prefix; "
            "cannot pick a transport"
        )
        return None

    async def _async_post(
        self, url: str, tracking_code: str, headers: dict[str, str]
    ) -> dict[str, Any]:
        """POST the single-item ``numberList`` body and return the parsed JSON."""
        async with self._session.post(
            url,
            json={"numberList": [tracking_code]},
            headers=headers,
        ) as response:
            if response.status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = float(retry_after_header) if retry_after_header else None
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds; let the caller's own backoff handle it
                raise GOFOExpressApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if response.status != 200:
                raise GOFOExpressApiError(
                    f"HTTP {response.status}", status_code=response.status
                )
            try:
                # content_type=None: both stacks reply with a JSON body but
                # not always an application/json content type.
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise GOFOExpressApiError(f"unparseable body ({err})") from err

        if not isinstance(payload, dict):
            raise GOFOExpressApiError("unexpected body (not a JSON object)")
        return payload

    async def _async_get_transport_a(
        self, tracking_code: str, country: str
    ) -> dict[str, Any] | None:
        """Query the Nuxt ``cnee-api`` transport (US/CA)."""
        url = TRANSPORT_A_URL.format(cc=country.lower())
        headers = {
            "Content-Type": "application/json",
            "User-Time-Zone": TRANSPORT_A_TIME_ZONE.get(country, "UTC"),
        }
        payload = await self._async_post(url, tracking_code, headers)

        if payload.get("success") not in (True, 1):
            data = payload.get("data")
            error = (data or {}).get("error") if isinstance(data, dict) else None
            if error is not None:
                # Populated data.error keyed by country prefix: semantic
                # not-found, never an HTTP-level failure.
                return None
            raise GOFOExpressApiError(str(payload.get("msg") or "unknown error envelope"))

        data = payload.get("data") or {}
        success_items = data.get("success")
        if not isinstance(success_items, list) or not success_items:
            # data.success empty (with data.error populated) is the
            # documented not-found branch even inside a "successful" envelope.
            return None
        for item in success_items:
            if isinstance(item, dict) and item.get("waybillNo") == tracking_code:
                return item
        # Requested code absent from data.success — treat the same as
        # not-found rather than returning an unrelated item.
        return None

    async def _async_get_transport_b(
        self, tracking_code: str, country: str
    ) -> dict[str, Any] | None:
        """Query the WordPress ``queryTrackV2`` transport (IT/FR/ES/NL)."""
        url = TRANSPORT_B_URL.format(cc=country.lower())
        headers = {
            "Content-Type": "application/json",
            "lang": TRANSPORT_B_LANG.get(country, "en"),
        }
        payload = await self._async_post(url, tracking_code, headers)

        if payload.get("code") != 200:
            raise GOFOExpressApiError(str(payload.get("msg") or "unknown error envelope"))

        data = payload.get("data")
        if not isinstance(data, list) or not data:
            # Confirmed live: {"msg":"Operation successful","code":200,"data":[]}
            return None
        for item in data:
            if isinstance(item, dict) and item.get("waybillNo") == tracking_code:
                return item
        return None
