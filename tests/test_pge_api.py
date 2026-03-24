"""Tests for PG&E Share My Data API integration.

Tests ESPI XML parsing, OAuth token storage, auth URL generation,
and error handling. Uses mocked HTTP responses — no real API calls.
"""

import os
import tempfile
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from src.integrations.espi_parser import parse_espi_xml
from src.integrations.pge_share_my_data import (
    generate_auth_url,
    exchange_code,
    refresh_access_token,
    fetch_usage_data,
    PGE_AUTH_URL,
    DEFAULT_SCOPE,
)
from src.storage.config_store import ConfigStore


# ── Sample ESPI XML ─────────────────────────────────────────────────

SAMPLE_ESPI_IMPORT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
  <entry>
    <content>
      <espi:ReadingType>
        <espi:flowDirection>1</espi:flowDirection>
        <espi:uom>72</espi:uom>
      </espi:ReadingType>
    </content>
  </entry>
  <entry>
    <content>
      <espi:IntervalBlock>
        <espi:IntervalReading>
          <espi:timePeriod>
            <espi:start>1711929600</espi:start>
            <espi:duration>3600</espi:duration>
          </espi:timePeriod>
          <espi:value>2940</espi:value>
        </espi:IntervalReading>
        <espi:IntervalReading>
          <espi:timePeriod>
            <espi:start>1711933200</espi:start>
            <espi:duration>3600</espi:duration>
          </espi:timePeriod>
          <espi:value>1500</espi:value>
        </espi:IntervalReading>
        <espi:IntervalReading>
          <espi:timePeriod>
            <espi:start>1711936800</espi:start>
            <espi:duration>3600</espi:duration>
          </espi:timePeriod>
          <espi:value>850</espi:value>
        </espi:IntervalReading>
      </espi:IntervalBlock>
    </content>
  </entry>
</feed>
"""

SAMPLE_ESPI_EXPORT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
  <entry>
    <content>
      <espi:ReadingType>
        <espi:flowDirection>19</espi:flowDirection>
        <espi:uom>72</espi:uom>
      </espi:ReadingType>
    </content>
  </entry>
  <entry>
    <content>
      <espi:IntervalBlock>
        <espi:IntervalReading>
          <espi:timePeriod>
            <espi:start>1711972800</espi:start>
            <espi:duration>3600</espi:duration>
          </espi:timePeriod>
          <espi:value>4200</espi:value>
        </espi:IntervalReading>
        <espi:IntervalReading>
          <espi:timePeriod>
            <espi:start>1711976400</espi:start>
            <espi:duration>3600</espi:duration>
          </espi:timePeriod>
          <espi:value>5100</espi:value>
        </espi:IntervalReading>
      </espi:IntervalBlock>
    </content>
  </entry>
</feed>
"""

SAMPLE_ESPI_EMPTY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
</feed>
"""


# ── ESPI Parser Tests ───────────────────────────────────────────────


class TestEspiParser:
    def test_parse_import_readings(self):
        """Parse import (flowDirection=1) interval readings."""
        result = parse_espi_xml(SAMPLE_ESPI_IMPORT_XML)

        assert "intervals" in result
        assert "summary" in result
        assert "metadata" in result

        intervals = result["intervals"]
        assert len(intervals) == 3

        # Check first reading: 2940 Wh = 2.94 kWh
        first = intervals[0]
        assert first["import_kwh"] == 2.94
        assert first["export_kwh"] == 0.0
        assert "date" in first
        assert "hour" in first
        assert "month" in first
        assert "day_of_week" in first

    def test_parse_export_readings(self):
        """Parse export (flowDirection=19) interval readings."""
        result = parse_espi_xml(SAMPLE_ESPI_EXPORT_XML)

        intervals = result["intervals"]
        assert len(intervals) == 2

        # Export readings should be in export_kwh, not import_kwh
        for iv in intervals:
            assert iv["import_kwh"] == 0.0
            assert iv["export_kwh"] > 0.0

        # 4200 Wh = 4.2 kWh
        assert intervals[0]["export_kwh"] == 4.2

    def test_wh_to_kwh_conversion(self):
        """Values in Wh should be converted to kWh (divide by 1000)."""
        result = parse_espi_xml(SAMPLE_ESPI_IMPORT_XML)
        intervals = result["intervals"]

        assert intervals[0]["import_kwh"] == 2.94    # 2940 Wh
        assert intervals[1]["import_kwh"] == 1.5      # 1500 Wh
        assert intervals[2]["import_kwh"] == 0.85     # 850 Wh

    def test_summary_totals(self):
        """Summary should contain correct totals."""
        result = parse_espi_xml(SAMPLE_ESPI_IMPORT_XML)
        summary = result["summary"]

        assert summary["total_import_kwh"] == 5.29  # 2.94 + 1.5 + 0.85
        assert summary["total_export_kwh"] == 0.0
        assert summary["num_intervals"] == 3

    def test_date_range_in_metadata(self):
        """Metadata should include date range."""
        result = parse_espi_xml(SAMPLE_ESPI_IMPORT_XML)
        assert result["metadata"]["date_range"] is not None
        assert "start" in result["metadata"]["date_range"]
        assert "end" in result["metadata"]["date_range"]

    def test_empty_feed(self):
        """Empty feed should return empty intervals."""
        result = parse_espi_xml(SAMPLE_ESPI_EMPTY_XML)
        assert result["intervals"] == []
        assert result["summary"]["num_intervals"] == 0
        assert result["summary"]["total_import_kwh"] == 0.0

    def test_output_format_matches_green_button(self):
        """Interval dicts should have the same keys as green_button parser output."""
        result = parse_espi_xml(SAMPLE_ESPI_IMPORT_XML)
        required_keys = {"date", "hour", "month", "day_of_week", "import_kwh", "export_kwh"}
        for iv in result["intervals"]:
            assert required_keys.issubset(iv.keys())


# ── OAuth Token Storage Tests ───────────────────────────────────────


class TestOAuthTokenStorage:
    @pytest.fixture
    def store(self, tmp_path):
        """Create a ConfigStore with a temp database."""
        return ConfigStore(db_dir=str(tmp_path))

    def test_save_and_get_token(self, store):
        """Save and retrieve OAuth token."""
        token_data = {
            "access_token": "test_access_123",
            "refresh_token": "test_refresh_456",
            "expires_in": 3600,
            "scope": "FB=1_3_4_5_13_14_39",
            "subscription_id": "sub_789",
        }
        result = store.save_oauth_token("my-config", "pge", token_data)
        assert result["status"] == "saved"

        retrieved = store.get_oauth_token("my-config", "pge")
        assert retrieved is not None
        assert retrieved["access_token"] == "test_access_123"
        assert retrieved["refresh_token"] == "test_refresh_456"
        assert retrieved["subscription_id"] == "sub_789"
        assert retrieved["scope"] == "FB=1_3_4_5_13_14_39"

    def test_get_nonexistent_token(self, store):
        """Getting a non-existent token returns None."""
        result = store.get_oauth_token("no-such-config", "pge")
        assert result is None

    def test_update_token(self, store):
        """Saving with same config_id+provider overwrites."""
        store.save_oauth_token("my-config", "pge", {
            "access_token": "old_token",
            "refresh_token": "old_refresh",
            "expires_in": 3600,
        })
        store.save_oauth_token("my-config", "pge", {
            "access_token": "new_token",
            "refresh_token": "new_refresh",
            "expires_in": 7200,
        })

        retrieved = store.get_oauth_token("my-config", "pge")
        assert retrieved["access_token"] == "new_token"
        assert retrieved["refresh_token"] == "new_refresh"

    def test_delete_token(self, store):
        """Delete stored token."""
        store.save_oauth_token("my-config", "pge", {
            "access_token": "token",
            "refresh_token": "refresh",
            "expires_in": 3600,
        })
        result = store.delete_oauth_token("my-config", "pge")
        assert result["status"] == "deleted"
        assert store.get_oauth_token("my-config", "pge") is None

    def test_delete_nonexistent_raises(self, store):
        """Deleting a non-existent token raises ValueError."""
        with pytest.raises(ValueError, match="No OAuth token found"):
            store.delete_oauth_token("no-config", "pge")

    def test_multiple_providers(self, store):
        """Can store tokens for different providers under same config."""
        store.save_oauth_token("my-config", "pge", {
            "access_token": "pge_token",
            "refresh_token": "pge_refresh",
            "expires_in": 3600,
        })
        store.save_oauth_token("my-config", "tesla", {
            "access_token": "tesla_token",
            "refresh_token": "tesla_refresh",
            "expires_in": 7200,
        })

        pge = store.get_oauth_token("my-config", "pge")
        tesla = store.get_oauth_token("my-config", "tesla")
        assert pge["access_token"] == "pge_token"
        assert tesla["access_token"] == "tesla_token"

    def test_is_token_expired_missing(self, store):
        """Missing token should be considered expired."""
        assert store.is_token_expired("no-config", "pge") is True

    def test_is_token_expired_fresh(self, store):
        """Freshly saved token should not be expired."""
        store.save_oauth_token("my-config", "pge", {
            "access_token": "token",
            "refresh_token": "refresh",
            "expires_in": 3600,
        })
        assert store.is_token_expired("my-config", "pge") is False

    def test_save_requires_config_id(self, store):
        """Empty config_id should raise ValueError."""
        with pytest.raises(ValueError):
            store.save_oauth_token("", "pge", {"access_token": "x"})


# ── Auth URL Generation Tests ───────────────────────────────────────


class TestAuthUrlGeneration:
    def test_returns_error_when_not_configured(self):
        """Without PGE_CLIENT_ID, should return not_configured error."""
        old = os.environ.pop("PGE_CLIENT_ID", None)
        try:
            result = generate_auth_url("test-config")
            assert result["error"] == "not_configured"
            assert "PGE_CLIENT_ID" in result["message"]
            assert "sharemydata.pge.com" in result["message"]
        finally:
            if old is not None:
                os.environ["PGE_CLIENT_ID"] = old

    @patch.dict(os.environ, {"PGE_CLIENT_ID": "test_client_123"})
    def test_generates_correct_url(self):
        """Auth URL should contain correct parameters."""
        result = generate_auth_url("my-config")

        assert "auth_url" in result
        assert "instructions" in result
        url = result["auth_url"]

        assert PGE_AUTH_URL in url
        assert "client_id=test_client_123" in url
        assert "response_type=code" in url
        assert "state=my-config" in url
        assert "scope=" in url

    @patch.dict(os.environ, {"PGE_CLIENT_ID": "test_client_123"})
    def test_custom_redirect_uri(self):
        """Should use custom redirect_uri when provided."""
        result = generate_auth_url("my-config", redirect_uri="https://myapp.com/callback")
        url = result["auth_url"]
        assert "redirect_uri=https" in url
        assert result["redirect_uri"] == "https://myapp.com/callback"

    @patch.dict(os.environ, {"PGE_CLIENT_ID": "test_client_123"})
    def test_returns_config_id(self):
        """Result should include the config_id."""
        result = generate_auth_url("brisbane-home")
        assert result["config_id"] == "brisbane-home"


# ── Token Exchange Tests ────────────────────────────────────────────


class TestTokenExchange:
    def test_exchange_not_configured(self):
        """Without credentials, exchange should return not_configured."""
        old_id = os.environ.pop("PGE_CLIENT_ID", None)
        old_secret = os.environ.pop("PGE_CLIENT_SECRET", None)
        try:
            result = exchange_code("some_code")
            assert result["error"] == "not_configured"
        finally:
            if old_id is not None:
                os.environ["PGE_CLIENT_ID"] = old_id
            if old_secret is not None:
                os.environ["PGE_CLIENT_SECRET"] = old_secret

    @patch.dict(os.environ, {
        "PGE_CLIENT_ID": "test_id",
        "PGE_CLIENT_SECRET": "test_secret",
    })
    @patch("src.integrations.pge_share_my_data._http_post")
    def test_successful_exchange(self, mock_post):
        """Successful token exchange returns access and refresh tokens."""
        mock_post.return_value = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": DEFAULT_SCOPE,
            "resourceURI": "https://api.pge.com/.../Subscription/12345",
        }

        result = exchange_code("auth_code_abc")
        assert result["access_token"] == "new_access_token"
        assert result["refresh_token"] == "new_refresh_token"
        assert result["subscription_id"] == "12345"

    @patch.dict(os.environ, {
        "PGE_CLIENT_ID": "test_id",
        "PGE_CLIENT_SECRET": "test_secret",
    })
    @patch("src.integrations.pge_share_my_data._http_post")
    def test_exchange_error_response(self, mock_post):
        """API error during exchange returns descriptive error."""
        mock_post.return_value = {
            "error": "invalid_grant",
            "error_description": "Authorization code expired",
        }

        result = exchange_code("expired_code")
        assert result["error"] == "token_exchange_failed"
        assert "expired" in result["message"].lower()

    @patch.dict(os.environ, {
        "PGE_CLIENT_ID": "test_id",
        "PGE_CLIENT_SECRET": "test_secret",
    })
    @patch("src.integrations.pge_share_my_data._http_post")
    def test_exchange_network_error(self, mock_post):
        """Network error during exchange returns error dict."""
        mock_post.side_effect = Exception("Connection refused")

        result = exchange_code("some_code")
        assert result["error"] == "token_exchange_error"
        assert "Connection refused" in result["message"]


# ── Token Refresh Tests ─────────────────────────────────────────────


class TestTokenRefresh:
    def test_refresh_not_configured(self):
        """Without credentials, refresh should return not_configured."""
        old_id = os.environ.pop("PGE_CLIENT_ID", None)
        old_secret = os.environ.pop("PGE_CLIENT_SECRET", None)
        try:
            result = refresh_access_token("some_refresh_token")
            assert result["error"] == "not_configured"
        finally:
            if old_id is not None:
                os.environ["PGE_CLIENT_ID"] = old_id
            if old_secret is not None:
                os.environ["PGE_CLIENT_SECRET"] = old_secret

    @patch.dict(os.environ, {
        "PGE_CLIENT_ID": "test_id",
        "PGE_CLIENT_SECRET": "test_secret",
    })
    @patch("src.integrations.pge_share_my_data._http_post")
    def test_successful_refresh(self, mock_post):
        """Successful refresh returns new access token."""
        mock_post.return_value = {
            "access_token": "refreshed_token",
            "refresh_token": "new_refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

        result = refresh_access_token("old_refresh_token")
        assert result["access_token"] == "refreshed_token"
        assert "error" not in result


# ── Fetch Usage Data Tests ──────────────────────────────────────────


class TestFetchUsageData:
    def test_missing_token(self):
        """No access token should return error."""
        result = fetch_usage_data("", "sub_123", "2025-01-01", "2025-02-01")
        assert result["error"] == "no_token"

    def test_missing_subscription(self):
        """No subscription_id should return error."""
        result = fetch_usage_data("token_abc", "", "2025-01-01", "2025-02-01")
        assert result["error"] == "no_subscription"

    def test_invalid_date_format(self):
        """Bad date format should return error."""
        result = fetch_usage_data("token", "sub", "not-a-date", "2025-02-01")
        assert result["error"] == "invalid_date"

    @patch("src.integrations.pge_share_my_data._http_get")
    def test_successful_fetch(self, mock_get):
        """Successful fetch returns parsed interval data."""
        mock_get.return_value = SAMPLE_ESPI_IMPORT_XML

        result = fetch_usage_data("valid_token", "sub_123", "2024-04-01", "2024-04-02")

        assert "intervals" in result
        assert "summary" in result
        assert result["metadata"]["source"] == "pge_share_my_data"
        assert result["metadata"]["subscription_id"] == "sub_123"

    @patch("src.integrations.pge_share_my_data._http_get")
    def test_expired_token_error(self, mock_get):
        """401 error should indicate token expiry."""
        mock_get.side_effect = Exception("HTTP 401 Unauthorized")

        result = fetch_usage_data("expired_token", "sub_123", "2025-01-01", "2025-02-01")
        assert result["error"] == "token_expired"

    @patch("src.integrations.pge_share_my_data._http_get")
    def test_not_found_error(self, mock_get):
        """404 error should indicate subscription not found."""
        mock_get.side_effect = Exception("HTTP 404 Not Found")

        result = fetch_usage_data("token", "bad_sub", "2025-01-01", "2025-02-01")
        assert result["error"] == "not_found"
