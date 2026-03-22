"""Tests for API integration tools — error handling when not configured."""

import os
import pytest

from src.integrations.tesla import get_powerwall_status
from src.integrations.solcast import get_solar_forecast


class TestTeslaIntegration:
    def test_returns_error_when_no_token(self):
        """Without TESLA_FLEET_TOKEN, should return helpful error."""
        # Ensure env var not set (save/restore if it is)
        old = os.environ.pop("TESLA_FLEET_TOKEN", None)
        try:
            result = get_powerwall_status()
            assert result["error"] == "not_configured"
            assert "TESLA_FLEET_TOKEN" in result["message"]
            assert "developer.tesla.com" in result["message"]
        finally:
            if old is not None:
                os.environ["TESLA_FLEET_TOKEN"] = old

    def test_error_includes_setup_instructions(self):
        old = os.environ.pop("TESLA_FLEET_TOKEN", None)
        try:
            result = get_powerwall_status()
            assert "OAuth2" in result["message"] or "oauth" in result["message"].lower()
            assert "energy_device_data" in result["message"]
        finally:
            if old is not None:
                os.environ["TESLA_FLEET_TOKEN"] = old


class TestSolcastIntegration:
    def test_returns_error_when_no_key(self):
        """Without SOLCAST_API_KEY, should return helpful error."""
        old = os.environ.pop("SOLCAST_API_KEY", None)
        try:
            result = get_solar_forecast()
            assert result["error"] == "not_configured"
            assert "SOLCAST_API_KEY" in result["message"]
            assert "solcast.com" in result["message"]
        finally:
            if old is not None:
                os.environ["SOLCAST_API_KEY"] = old

    def test_error_mentions_hobbyist_limit(self):
        old = os.environ.pop("SOLCAST_API_KEY", None)
        try:
            result = get_solar_forecast()
            assert "10" in result["message"]  # 10 calls/day
        finally:
            if old is not None:
                os.environ["SOLCAST_API_KEY"] = old
