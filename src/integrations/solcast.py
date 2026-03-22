"""Solcast API integration for solar production forecasts.

Requires SOLCAST_API_KEY environment variable.
Hobbyist tier: 10 API calls/day — cache aggressively.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

SOLCAST_API_BASE = "https://api.solcast.com.au"
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache"
CACHE_FILE = CACHE_DIR / "solcast_forecast.json"
CACHE_TTL_SECONDS = 4 * 3600  # 4 hours — fits within 10 calls/day


def get_solar_forecast(latitude: float = 37.68, longitude: float = -122.40,
                       capacity_kw: float = 7.668) -> dict:
    """
    Get solar production forecast from Solcast.

    Caches responses for 4 hours to stay within Hobbyist rate limits.
    Returns hourly forecast and daily totals.
    """
    api_key = os.environ.get("SOLCAST_API_KEY")
    if not api_key:
        return {
            "error": "not_configured",
            "message": (
                "SOLCAST_API_KEY environment variable not set. "
                "To enable solar forecasts:\n"
                "1. Sign up at https://solcast.com (Hobbyist tier is free)\n"
                "2. Create a rooftop site for your location\n"
                "3. Set SOLCAST_API_KEY=<your_api_key> in your environment\n"
                "Note: Hobbyist tier allows 10 API calls/day. "
                "Forecasts are cached for 4 hours."
            ),
        }

    # Check cache first
    cached = _load_cache(latitude, longitude, capacity_kw)
    if cached:
        cached["from_cache"] = True
        return cached

    try:
        result = _fetch_forecast(api_key, latitude, longitude, capacity_kw)
        _save_cache(result, latitude, longitude, capacity_kw)
        result["from_cache"] = False
        return result

    except HTTPError as e:
        if e.code == 429:
            return {
                "error": "rate_limited",
                "message": "Solcast daily limit reached (10 calls/day on Hobbyist). Try again tomorrow.",
            }
        if e.code == 401:
            return {
                "error": "auth_error",
                "message": "Invalid Solcast API key. Check SOLCAST_API_KEY.",
            }
        return {"error": "api_error", "message": f"Solcast API error: {e.code} {e.reason}"}
    except URLError as e:
        return {"error": "network_error", "message": f"Cannot reach Solcast API: {e.reason}"}
    except Exception as e:
        return {"error": "unexpected", "message": str(e)}


def _fetch_forecast(api_key: str, lat: float, lon: float,
                    capacity_kw: float) -> dict:
    """Fetch forecast from Solcast world API."""
    # Use the rooftop forecast endpoint for estimated output
    url = (
        f"{SOLCAST_API_BASE}/world/estimated_actuals/radiation.json"
        f"?latitude={lat}&longitude={lon}&hours=48&format=json"
    )
    req = Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    })

    with urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    # Also get forecast
    forecast_url = (
        f"{SOLCAST_API_BASE}/world/radiation/forecasts.json"
        f"?latitude={lat}&longitude={lon}&hours=48&format=json"
    )
    forecast_req = Request(forecast_url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    })

    with urlopen(forecast_req, timeout=15) as resp:
        forecast_data = json.loads(resp.read())

    # Process into hourly production estimates
    hourly = []
    daily_totals = {}

    for period in forecast_data.get("forecasts", []):
        ghi = period.get("ghi", 0)  # Global horizontal irradiance (W/m²)
        period_end = period.get("period_end", "")
        # Rough conversion: production ≈ capacity × (GHI / 1000) × performance_ratio
        production_kw = capacity_kw * (ghi / 1000) * 0.85
        # Each period is 30 min, so kWh = kW * 0.5
        production_kwh = production_kw * 0.5

        hourly.append({
            "period_end": period_end,
            "ghi_w_m2": ghi,
            "estimated_kwh": round(production_kwh, 3),
        })

        # Aggregate daily
        day = period_end[:10] if period_end else ""
        if day:
            daily_totals[day] = daily_totals.get(day, 0) + production_kwh

    return {
        "location": {"latitude": lat, "longitude": lon},
        "capacity_kw": capacity_kw,
        "hourly_forecast": hourly,
        "daily_totals": [
            {"date": day, "estimated_kwh": round(kwh, 1)}
            for day, kwh in sorted(daily_totals.items())
        ],
        "api_calls_note": "Hobbyist tier: 10 calls/day. Cached for 4 hours.",
    }


def _load_cache(lat: float, lon: float, capacity_kw: float) -> dict | None:
    """Load cached forecast if still valid."""
    if not CACHE_FILE.exists():
        return None

    try:
        with open(CACHE_FILE) as f:
            cached = json.load(f)

        # Check TTL
        if time.time() - cached.get("_cached_at", 0) > CACHE_TTL_SECONDS:
            return None

        # Check same params
        if (cached.get("_params", {}).get("lat") != lat or
            cached.get("_params", {}).get("lon") != lon or
            cached.get("_params", {}).get("capacity_kw") != capacity_kw):
            return None

        # Remove internal cache fields before returning
        result = {k: v for k, v in cached.items() if not k.startswith("_")}
        return result

    except (json.JSONDecodeError, KeyError):
        return None


def _save_cache(data: dict, lat: float, lon: float, capacity_kw: float):
    """Save forecast to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = dict(data)
    cached["_cached_at"] = time.time()
    cached["_params"] = {"lat": lat, "lon": lon, "capacity_kw": capacity_kw}

    with open(CACHE_FILE, "w") as f:
        json.dump(cached, f, indent=2)
