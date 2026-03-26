"""Tesla Powerwall integration via pypowerwall.

Supports three connection modes:
- Local: Direct to Gateway IP (read-only, fastest, no internet needed)
- Cloud: Tesla account login (read + control)
- FleetAPI: Official Tesla API (read + control, requires developer account)

Control operations (set_reserve, set_mode, etc.) only work in Cloud/FleetAPI modes.
"""

from __future__ import annotations

import os


def _get_connection():
    """Get or create pypowerwall connection based on environment config.

    Environment variables:
        PW_HOST: Gateway IP for local mode (e.g., "192.168.1.50")
        PW_PASSWORD: Customer password for local mode
        PW_EMAIL: Tesla account email
        PW_TIMEZONE: Timezone (default: America/Los_Angeles)
        PW_MODE: "local", "cloud", or "fleetapi" (default: auto-detect)
        PW_AUTH_PATH: Path to auth cache file (default: .pypowerwall)
    """
    try:
        import pypowerwall
    except ImportError:
        return None, "pypowerwall not installed. Run: pip install pypowerwall"

    host = os.environ.get("PW_HOST", "")
    password = os.environ.get("PW_PASSWORD", "")
    email = os.environ.get("PW_EMAIL", "")
    timezone = os.environ.get("PW_TIMEZONE", "America/Los_Angeles")
    mode = os.environ.get("PW_MODE", "").lower()
    auth_path = os.environ.get("PW_AUTH_PATH", "")

    if not email and not host:
        return None, (
            "Powerwall not configured. Set these environment variables:\n\n"
            "For local access (read-only, fastest):\n"
            "  PW_HOST=<gateway_ip>  (run 'python -m pypowerwall scan' to find it)\n"
            "  PW_PASSWORD=<customer_password>\n"
            "  PW_EMAIL=<tesla_email>\n\n"
            "For cloud access (read + control):\n"
            "  PW_EMAIL=<tesla_email>\n"
            "  PW_MODE=cloud\n"
            "  Then run: python -m pypowerwall setup\n\n"
            "For FleetAPI (read + control, official):\n"
            "  PW_EMAIL=<tesla_email>\n"
            "  PW_MODE=fleetapi\n"
            "  Then run: python -m pypowerwall fleetapi"
        )

    cloud_mode = mode == "cloud" or (not host and mode != "fleetapi")
    fleet_mode = mode == "fleetapi"

    try:
        pw = pypowerwall.Powerwall(
            host=host,
            password=password,
            email=email,
            timezone=timezone,
            cloudmode=cloud_mode,
            fleetapi=fleet_mode,
            authpath=auth_path or "",
            auto_select=mode == "",
        )
        if not pw.is_connected():
            pw.connect()
        return pw, None
    except Exception as e:
        return None, f"Failed to connect to Powerwall: {e}"


def get_live_status() -> dict:
    """Get real-time Powerwall status: power flow, battery level, grid status.

    Returns comprehensive snapshot including:
    - Power flow (solar, battery, grid, home) in watts
    - Battery state of charge (%)
    - Grid status (UP/DOWN/SYNCING)
    - Backup time remaining
    - Operating mode and reserve level
    - Alerts and firmware version
    """
    pw, error = _get_connection()
    if error:
        return {"error": "not_configured", "message": error}

    try:
        power = pw.power()
        level = pw.level()
        grid_status = pw.grid_status()
        version = pw.version()
        uptime = pw.uptime()

        result = {
            "power_flow": {
                "solar_w": power.get("solar", 0),
                "battery_w": power.get("battery", 0),
                "grid_w": power.get("site", 0),
                "home_w": power.get("load", 0),
            },
            "battery_pct": round(level, 1) if level else 0,
            "grid_status": grid_status or "Unknown",
            "firmware": version,
            "uptime": uptime,
        }

        # Try to get additional info (may not be available in all modes)
        try:
            result["backup_time_remaining_hrs"] = round(pw.get_time_remaining(), 1)
        except Exception:
            pass

        try:
            result["operating_mode"] = pw.get_mode()
        except Exception:
            pass

        try:
            result["backup_reserve_pct"] = round(pw.get_reserve(), 1)
        except Exception:
            pass

        try:
            result["grid_charging"] = pw.get_grid_charging()
        except Exception:
            pass

        try:
            result["grid_export"] = pw.get_grid_export()
        except Exception:
            pass

        try:
            alerts = pw.alerts(alertsonly=True)
            if alerts:
                result["alerts"] = alerts
        except Exception:
            pass

        return result

    except Exception as e:
        return {"error": "read_failed", "message": str(e)}


def get_battery_details() -> dict:
    """Get per-battery block details: SOC, temps, serial numbers."""
    pw, error = _get_connection()
    if error:
        return {"error": "not_configured", "message": error}

    try:
        blocks = pw.battery_blocks()
        temps = pw.temps()
        vitals = pw.vitals()

        return {
            "battery_blocks": blocks,
            "temperatures": temps,
            "vitals": vitals,
        }
    except Exception as e:
        return {"error": "read_failed", "message": str(e)}


def get_solar_strings() -> dict:
    """Get per-string solar data: voltage, current, power per string."""
    pw, error = _get_connection()
    if error:
        return {"error": "not_configured", "message": error}

    try:
        strings = pw.strings()
        return {"strings": strings}
    except Exception as e:
        return {"error": "read_failed", "message": str(e)}


def set_mode(mode: str) -> dict:
    """Set Powerwall operating mode.

    Args:
        mode: "self_consumption", "autonomous", or "backup"
              - self_consumption: Prioritize self-use, minimal grid interaction
              - autonomous: Optimize for TOU cost savings (charge off-peak, discharge peak)
              - backup: Maximize reserve for outages

    Returns:
        Success/error dict. Only works in Cloud/FleetAPI modes.
    """
    valid_modes = ("self_consumption", "autonomous", "backup")
    if mode not in valid_modes:
        return {"error": "invalid_mode", "message": f"Mode must be one of: {valid_modes}"}

    pw, error = _get_connection()
    if error:
        return {"error": "not_configured", "message": error}

    try:
        result = pw.set_mode(mode)
        if result is None:
            return {
                "error": "control_unavailable",
                "message": "Control not available in local mode. Use Cloud or FleetAPI mode.",
            }
        return {"success": True, "mode": mode, "response": result}
    except Exception as e:
        return {"error": "control_failed", "message": str(e)}


def set_reserve(level: float) -> dict:
    """Set backup reserve percentage.

    Args:
        level: Reserve percentage (0-100). Higher = more backup buffer.

    Returns:
        Success/error dict. Only works in Cloud/FleetAPI modes.
    """
    if not 0 <= level <= 100:
        return {"error": "invalid_level", "message": "Reserve must be 0-100%"}

    pw, error = _get_connection()
    if error:
        return {"error": "not_configured", "message": error}

    try:
        result = pw.set_reserve(level)
        if result is None:
            return {
                "error": "control_unavailable",
                "message": "Control not available in local mode. Use Cloud or FleetAPI mode.",
            }
        return {"success": True, "reserve_pct": level, "response": result}
    except Exception as e:
        return {"error": "control_failed", "message": str(e)}


def set_grid_charging(enabled: bool) -> dict:
    """Enable or disable grid charging.

    Args:
        enabled: True to allow battery charging from grid, False to disable.
                 Grid charging is key for TOU arbitrage (charge off-peak, discharge peak).

    Returns:
        Success/error dict. Only works in Cloud/FleetAPI modes.
    """
    pw, error = _get_connection()
    if error:
        return {"error": "not_configured", "message": error}

    try:
        result = pw.set_grid_charging(enabled)
        if result is None:
            return {
                "error": "control_unavailable",
                "message": "Control not available in local mode. Use Cloud or FleetAPI mode.",
            }
        return {"success": True, "grid_charging": enabled, "response": result}
    except Exception as e:
        return {"error": "control_failed", "message": str(e)}


def set_grid_export(mode: str) -> dict:
    """Configure grid export behavior.

    Args:
        mode: "battery_ok" (export when charged), "pv_only" (solar only), or "never"

    Returns:
        Success/error dict. Only works in Cloud/FleetAPI modes.
    """
    valid_modes = ("battery_ok", "pv_only", "never")
    if mode not in valid_modes:
        return {"error": "invalid_mode", "message": f"Export mode must be one of: {valid_modes}"}

    pw, error = _get_connection()
    if error:
        return {"error": "not_configured", "message": error}

    try:
        result = pw.set_grid_export(mode)
        if result is None:
            return {
                "error": "control_unavailable",
                "message": "Control not available in local mode. Use Cloud or FleetAPI mode.",
            }
        return {"success": True, "grid_export": mode, "response": result}
    except Exception as e:
        return {"error": "control_failed", "message": str(e)}
