"""Tesla FleetAPI integration for real-time Powerwall status.

Requires TESLA_FLEET_TOKEN environment variable.
Users authenticate with their own Tesla account.
"""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

FLEET_API_BASE = "https://fleet-api.prd.na.vn.cloud.tesla.com"


def get_powerwall_status() -> dict:
    """
    Get real-time Powerwall status from Tesla FleetAPI.

    Returns battery SOC, power flow, grid status, and operating mode.
    Returns an error dict with setup instructions if not configured.
    """
    token = os.environ.get("TESLA_FLEET_TOKEN")
    if not token:
        return {
            "error": "not_configured",
            "message": (
                "TESLA_FLEET_TOKEN environment variable not set. "
                "To enable Powerwall status:\n"
                "1. Go to https://developer.tesla.com and create an app\n"
                "2. Complete OAuth2 flow to get an access token\n"
                "3. Set TESLA_FLEET_TOKEN=<your_token> in your environment\n"
                "4. The token must have energy_device_data scope"
            ),
        }

    try:
        # Step 1: Get energy site ID
        sites = _fleet_get("/api/1/products", token)
        energy_site = None
        for product in sites.get("response", []):
            if "energy_site_id" in product:
                energy_site = product
                break

        if not energy_site:
            return {"error": "no_energy_site", "message": "No Powerwall found on this Tesla account"}

        site_id = energy_site["energy_site_id"]

        # Step 2: Get live status
        live = _fleet_get(f"/api/1/energy_sites/{site_id}/live_status", token)
        status = live.get("response", {})

        return {
            "site_id": site_id,
            "battery_pct": status.get("percentage_charged", 0),
            "power_flow": {
                "solar_w": status.get("solar_power", 0),
                "battery_w": status.get("battery_power", 0),
                "grid_w": status.get("grid_power", 0),
                "home_w": status.get("load_power", 0),
            },
            "grid_status": status.get("grid_status", "Unknown"),
            "backup_capable": status.get("backup_capable", False),
            "storm_mode": status.get("storm_mode_active", False),
        }

    except HTTPError as e:
        if e.code == 401:
            return {
                "error": "auth_expired",
                "message": "Tesla token expired or invalid. Regenerate via OAuth2 flow.",
            }
        return {"error": "api_error", "message": f"Tesla API error: {e.code} {e.reason}"}
    except URLError as e:
        return {"error": "network_error", "message": f"Cannot reach Tesla API: {e.reason}"}
    except Exception as e:
        return {"error": "unexpected", "message": str(e)}


def _fleet_get(path: str, token: str) -> dict:
    """Make authenticated GET request to Tesla FleetAPI."""
    url = FLEET_API_BASE + path
    req = Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())
