"""PG&E Share My Data (Green Button Connect) API integration.

Implements OAuth2 authorization and ESPI interval data retrieval via PG&E's
Green Button Connect My Data API. Users authorize access to their PG&E
usage data, and we fetch hourly interval data in the same format as
manual Green Button CSV exports.

Requires environment variables:
    PGE_CLIENT_ID      — OAuth2 client ID from PG&E developer portal
    PGE_CLIENT_SECRET  — OAuth2 client secret
    PGE_REDIRECT_URI   — Registered redirect URI (optional, defaults to urn:ietf:wg:oauth:2.0:oob)
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlencode, quote
from datetime import datetime, timezone

# Use httpx if available, fall back to urllib
try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

from src.integrations.espi_parser import parse_espi_xml

# PG&E API endpoints
PGE_AUTH_URL = "https://api.pge.com/datacustodian/oauth/v2/authorize"
PGE_TOKEN_URL = "https://api.pge.com/datacustodian/oauth/v2/token"
PGE_API_BASE = "https://api.pge.com/GreenButtonConnect"

# Scope covering interval data (usage, demand, cost, power quality, billing)
DEFAULT_SCOPE = "FB=1_3_4_5_13_14_39"

# Default redirect for CLI/desktop (out-of-band)
DEFAULT_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"


def _get_client_id() -> str | None:
    return os.environ.get("PGE_CLIENT_ID")


def _get_client_secret() -> str | None:
    return os.environ.get("PGE_CLIENT_SECRET")


def _get_redirect_uri() -> str:
    return os.environ.get("PGE_REDIRECT_URI", DEFAULT_REDIRECT_URI)


def _not_configured_error() -> dict:
    return {
        "error": "not_configured",
        "message": (
            "PG&E Share My Data API credentials not configured. "
            "To enable automatic data fetching:\n"
            "1. Register as a third-party at https://developer.pge.com\n"
            "2. Create a Share My Data application\n"
            "3. Set these environment variables:\n"
            "   PGE_CLIENT_ID=<your_client_id>\n"
            "   PGE_CLIENT_SECRET=<your_client_secret>\n"
            "   PGE_REDIRECT_URI=<your_redirect_uri>  (optional)\n"
            "4. Your app must be approved by PG&E for production access"
        ),
    }


def generate_auth_url(config_id: str, redirect_uri: str = None) -> dict:
    """
    Build PG&E OAuth authorization URL for a user to authorize data sharing.

    Args:
        config_id: User's config ID (used as state parameter)
        redirect_uri: Override redirect URI (uses env var or default)

    Returns:
        Dict with auth_url and instructions, or error if not configured.
    """
    client_id = _get_client_id()
    if not client_id:
        return _not_configured_error()

    uri = redirect_uri or _get_redirect_uri()

    params = {
        "client_id": client_id,
        "redirect_uri": uri,
        "response_type": "code",
        "scope": DEFAULT_SCOPE,
        "state": config_id,
    }

    auth_url = f"{PGE_AUTH_URL}?{urlencode(params)}"

    return {
        "auth_url": auth_url,
        "instructions": (
            "To connect your PG&E account:\n"
            "1. Open the authorization URL in your browser\n"
            "2. Log in to your PG&E account\n"
            "3. Authorize data sharing for your electricity service\n"
            "4. Copy the authorization code from the redirect\n"
            "5. Use complete_pge_connection with the code to finish setup"
        ),
        "config_id": config_id,
        "redirect_uri": uri,
    }


def exchange_code(code: str, redirect_uri: str = None) -> dict:
    """
    Exchange an OAuth authorization code for access and refresh tokens.

    Args:
        code: Authorization code from PG&E OAuth redirect
        redirect_uri: Must match the URI used in generate_auth_url

    Returns:
        Token dict with access_token, refresh_token, expires_in, scope,
        subscription_id, or error dict.
    """
    client_id = _get_client_id()
    client_secret = _get_client_secret()
    if not client_id or not client_secret:
        return _not_configured_error()

    uri = redirect_uri or _get_redirect_uri()

    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }

    try:
        response = _http_post(PGE_TOKEN_URL, data=token_data)

        if "error" in response:
            return {
                "error": "token_exchange_failed",
                "message": f"PG&E rejected the authorization code: {response.get('error_description', response.get('error'))}",
            }

        # PG&E returns subscription_id in the response or as part of resourceURI
        subscription_id = response.get("subscription_id", "")
        if not subscription_id and "resourceURI" in response:
            # Extract from URI like .../Subscription/{id}
            parts = response["resourceURI"].rstrip("/").split("/")
            if "Subscription" in parts:
                idx = parts.index("Subscription")
                if idx + 1 < len(parts):
                    subscription_id = parts[idx + 1]

        return {
            "access_token": response["access_token"],
            "refresh_token": response.get("refresh_token", ""),
            "expires_in": response.get("expires_in", 3600),
            "token_type": response.get("token_type", "Bearer"),
            "scope": response.get("scope", DEFAULT_SCOPE),
            "subscription_id": subscription_id,
        }

    except Exception as e:
        return {"error": "token_exchange_error", "message": str(e)}


def refresh_access_token(refresh_token: str) -> dict:
    """
    Refresh an expired PG&E access token.

    Args:
        refresh_token: The refresh token from the original exchange

    Returns:
        New token dict with access_token, refresh_token, expires_in, or error.
    """
    client_id = _get_client_id()
    client_secret = _get_client_secret()
    if not client_id or not client_secret:
        return _not_configured_error()

    token_data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }

    try:
        response = _http_post(PGE_TOKEN_URL, data=token_data)

        if "error" in response:
            return {
                "error": "refresh_failed",
                "message": f"Token refresh failed: {response.get('error_description', response.get('error'))}",
            }

        return {
            "access_token": response["access_token"],
            "refresh_token": response.get("refresh_token", refresh_token),
            "expires_in": response.get("expires_in", 3600),
            "token_type": response.get("token_type", "Bearer"),
            "scope": response.get("scope", DEFAULT_SCOPE),
        }

    except Exception as e:
        return {"error": "refresh_error", "message": str(e)}


def fetch_usage_data(access_token: str, subscription_id: str,
                     start_date: str, end_date: str) -> dict:
    """
    Fetch interval usage data from PG&E ESPI API.

    Returns data in the same format as parse_green_button so downstream
    tools (compare_plans, usage_profile, etc.) work with either source.

    Args:
        access_token: Valid OAuth access token
        subscription_id: PG&E subscription/usage point ID
        start_date: ISO date string (YYYY-MM-DD)
        end_date: ISO date string (YYYY-MM-DD)

    Returns:
        Dict matching green_button.parse() output format, or error dict.
    """
    if not access_token:
        return {"error": "no_token", "message": "No access token provided"}
    if not subscription_id:
        return {"error": "no_subscription", "message": "No subscription ID provided"}

    # Convert dates to epoch for ESPI query params
    try:
        start_epoch = int(datetime.strptime(start_date, "%Y-%m-%d")
                         .replace(tzinfo=timezone.utc).timestamp())
        end_epoch = int(datetime.strptime(end_date, "%Y-%m-%d")
                       .replace(tzinfo=timezone.utc).timestamp())
    except ValueError as e:
        return {"error": "invalid_date", "message": f"Date format error: {e}. Use YYYY-MM-DD."}

    # ESPI endpoint for interval data
    url = (
        f"{PGE_API_BASE}/espi/1_1/resource/Subscription/{subscription_id}"
        f"/UsagePoint/01/MeterReading/01/IntervalBlock"
        f"?published-min={start_epoch}&published-max={end_epoch}"
    )

    try:
        xml_content = _http_get(url, access_token)
        result = parse_espi_xml(xml_content)
        result["metadata"]["source"] = "pge_share_my_data"
        result["metadata"]["subscription_id"] = subscription_id
        result["metadata"]["request"] = {
            "start_date": start_date,
            "end_date": end_date,
        }
        return result

    except Exception as e:
        error_str = str(e)
        if "401" in error_str or "Unauthorized" in error_str:
            return {
                "error": "token_expired",
                "message": "PG&E access token expired. Use refresh to get a new token.",
            }
        if "403" in error_str:
            return {
                "error": "access_denied",
                "message": "Access denied. The user may need to re-authorize data sharing.",
            }
        if "404" in error_str:
            return {
                "error": "not_found",
                "message": (
                    "Subscription or usage data not found. Verify subscription_id "
                    "and that the date range has data available."
                ),
            }
        return {"error": "fetch_error", "message": f"Failed to fetch usage data: {error_str}"}


# ── HTTP helpers (httpx with urllib fallback) ────────────────────────


def _http_post(url: str, data: dict) -> dict:
    """POST form data and return JSON response."""
    if _HAS_HTTPX:
        resp = httpx.post(url, data=data, timeout=15)
        resp.raise_for_status()
        return resp.json()
    else:
        encoded = urlencode(data).encode("utf-8")
        req = Request(url, data=encoded, method="POST", headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        })
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())


def _http_get(url: str, access_token: str) -> str:
    """GET with Bearer auth, return response body as string."""
    if _HAS_HTTPX:
        resp = httpx.get(url, headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/xml",
        }, timeout=30)
        resp.raise_for_status()
        return resp.text
    else:
        req = Request(url, headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/xml",
        })
        with urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
