"""Tests for GOFO Express diagnostics."""
from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.gofo.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "EXAMPLE123456"}]}
    entry.runtime_data.coordinator.current_tier_minutes = 15
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=15)
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "EXAMPLE123456",
            "sender": None,
            "receiver": None,
            "status": "out_for_delivery",
            "raw": {
                "waybillNo": "EXAMPLE123456",
                "trackingNumber": "BG-2603249D000000",
                "trackEventList": [
                    {
                        "processCode": "208",
                        "processContent": "Out for Delivery",
                        "processCity": "Newark",
                        "processProvince": "CA",
                        "processLocation": "Newark",
                    }
                ],
                "podImgList": ["https://example.test/pod.jpg"],
                "sign": "abcdef",
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.delivered_codes = set()

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {
        "incoming_active": 1,
        "delivered": 0,
        "skipped_from_fetch": 0,
    }
    assert result["polling"] == {
        "tier_minutes": 15,
        "update_interval_seconds": 900.0,
        "suspended": False,
    }
    # tracking codes and payload PII are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["waybillNo"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["trackingNumber"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["trackEventList"][0]["processCity"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["trackEventList"][0]["processProvince"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["trackEventList"][0]["processLocation"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["podImgList"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["sign"] == "**REDACTED**"
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "out_for_delivery"
    assert result["incoming"][0]["raw"]["trackEventList"][0]["processCode"] == "208"


async def test_diagnostics_reports_suspended_polling(hass):
    """update_interval None (Section 2.1's full stop) must be visible, not just absent."""
    entry = MagicMock()
    entry.options = {"parcels": []}
    entry.runtime_data.coordinator.current_tier_minutes = None
    entry.runtime_data.coordinator.update_interval = None
    entry.runtime_data.coordinator.data = []
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["polling"] == {
        "tier_minutes": None,
        "update_interval_seconds": None,
        "suspended": True,
    }
