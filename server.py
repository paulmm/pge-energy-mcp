"""
pge-energy-mcp — MCP server for PG&E solar + battery energy analysis.

Tools help PG&E residential customers answer:
- Am I on the right rate plan?
- How is my solar+battery system performing?
- What upgrades would save me the most money?
"""

from fastmcp import FastMCP
from typing import Optional
import json

from src.storage.config_store import get_store
from src.data.system_config import SystemConfig

mcp = FastMCP(
    "PG&E Energy Analyzer",
    description="Analyze PG&E solar + battery energy usage, compare rate plans, and model system expansions.",
)


@mcp.tool()
async def parse_green_button(csv_content: str) -> dict:
    """
    Parse PG&E Green Button CSV export into structured hourly interval data.
    
    Accepts the raw CSV text from a PG&E Green Button "Download My Data" export.
    Returns structured data with hourly import/export/cost classified by TOU period.
    
    Args:
        csv_content: Raw CSV text content from PG&E Green Button export
        
    Returns:
        Dict with 'intervals' (list of hourly records), 'summary' (totals), 
        and 'metadata' (account info, date range)
    """
    from src.parsers.green_button import parse
    return parse(csv_content)


@mcp.tool()
async def parse_tesla_export(csv_content: str, export_type: str = "year") -> dict:
    """
    Parse Tesla app energy data CSV export.
    
    Handles Tesla's inconsistent unit formats (MWh vs kWh columns vary by year).
    
    Args:
        csv_content: Raw CSV text from Tesla app "Download My Data"
        export_type: "year" for yearly summary, "lifetime" for lifetime summary
        
    Returns:
        Dict with monthly breakdown of solar, grid_in, grid_out, battery, home, vehicle
    """
    from src.parsers.tesla import parse
    return parse(csv_content, export_type)


@mcp.tool()
async def get_rates(
    schedule: str,
    provider: str = "PGE_BUNDLED",
    vintage_year: int = 2016,
    income_tier: int = 3,
) -> dict:
    """
    Look up effective electricity rates for a specific plan + provider combination.
    
    Handles the complexity of CCA vs bundled billing: PG&E delivery rates + 
    CCA generation rates + vintaged PCIA.
    
    Args:
        schedule: Rate schedule — "EV2-A", "E-ELEC", "E-TOU-C", or "E-TOU-D"
        provider: Electricity provider — "PGE_BUNDLED", "PCE", "SVCE", "MCE", "SJCE", "EBCE"
        vintage_year: PCIA vintage year (year customer joined CCA). Ignored for PGE_BUNDLED.
        income_tier: 1 (CARE), 2 (FERA), or 3 (standard) for base services charge.
        
    Returns:
        Dict with effective $/kWh rates by season and TOU period, base charge,
        and component breakdown (delivery, generation, PCIA)
    """
    from src.rates.engine import lookup_rates
    return lookup_rates(schedule, provider, vintage_year, income_tier)


@mcp.tool()
async def compare_plans(
    interval_data: list[dict],
    plans: list[dict],
    nem_version: str = "NEM2",
    config_id: str = None,
) -> dict:
    """
    Compare annual cost across multiple rate plan configurations using actual usage data.

    Args:
        interval_data: Hourly records from parse_green_button (list of dicts with
                       datetime, import_kwh, export_kwh, hour, month fields)
        plans: List of plan configs, each with {schedule, provider, vintage_year, income_tier}
        nem_version: "NEM2" (full retail export credit) or "NEM3" (avoided cost export credit)
        config_id: Optional stored config ID — if provided, uses its nem_version as default

    Returns:
        Dict with annual cost per plan, savings vs baseline, breakdown by TOU period,
        and recommendation
    """
    if config_id:
        cfg = _load_config(config_id)
        if not nem_version or nem_version == "NEM2":
            nem_version = cfg.get("nem_version", nem_version)
    from src.analysis.compare import compare
    return compare(interval_data, plans, nem_version)


@mcp.tool()
async def usage_profile(interval_data: list[dict], config_id: str = None) -> dict:
    """
    Generate a comprehensive usage profile from hourly interval data.

    Args:
        interval_data: Hourly records from parse_green_button
        config_id: Optional stored config ID (reserved for future per-user context)

    Returns:
        Dict with: self_consumption_ratio, grid_dependency_by_season,
        peak_hour_exposure_pct, overnight_baseload_kwh, weekday_vs_weekend,
        monthly_trends, top_import_days
    """
    from src.analysis.usage import profile
    return profile(interval_data)


@mcp.tool()
async def simulate_system(
    interval_data: list[dict],
    system_config: dict,
    rate_config: dict,
    nem_version: str = "NEM2",
    config_id: str = None,
) -> dict:
    """
    Compare current vs proposed solar+battery system using actual usage data.

    Simulates both systems through the same model so errors cancel in the
    savings estimate. Supports adding panels, adding/fixing batteries, and
    changing dispatch strategy (self_powered vs tou_optimized).

    Args:
        interval_data: Hourly records from parse_green_button
        system_config: {
            "current_system": {
                "arrays": [{panels, panel_watts, inverter_watts_ac, type, ac_watts}],
                "batteries": [{kwh, kw, efficiency, status}],
                "strategy": "self_powered" or "tou_optimized"
            },
            "proposed_system": {same shape as current_system},
            "psh_by_month": {"Jan": 3.2, ...}  // optional, peak sun hours
        }
        rate_config: Rate plan config from get_rates output
        nem_version: "NEM2" or "NEM3"

    Returns:
        Dict with: estimated_savings (sim-vs-sim, model errors cancel),
        current_simulated and proposed cost breakdowns, TOU period detail,
        monthly breakdown, green_button_baseline for context
        config_id: Optional stored config ID — if provided, uses its nem_version as default
    """
    if config_id:
        cfg = _load_config(config_id)
        if not nem_version or nem_version == "NEM2":
            nem_version = cfg.get("nem_version", nem_version)
    from src.analysis.simulator import simulate
    return simulate(interval_data, system_config, rate_config, nem_version)


@mcp.tool()
async def seasonal_strategy(
    interval_data: list[dict],
    rate_config: dict,
    system_config: dict = None,
    config_id: str = None,
) -> dict:
    """
    Generate seasonal optimization recommendations based on usage and rates.

    Analyzes usage patterns against rate structure to recommend battery dispatch
    strategy, EV charging timing, load shifting, and solar expansion priorities.

    Args:
        interval_data: Hourly records from parse_green_button
        rate_config: Rate plan config from get_rates output
        system_config: Optional system info (batteries, arrays) for context

    Returns:
        Dict with seasonal analysis, rate spreads, monthly trends,
        and prioritized recommendations
        config_id: Optional stored config ID — if provided and system_config not given,
                   loads system info from stored config
    """
    if config_id and system_config is None:
        cfg = _load_config(config_id)
        system_config = cfg
    from src.analysis.strategy import seasonal_strategy as compute
    return compute(interval_data, rate_config, system_config)


@mcp.tool()
async def nem_projection(
    interval_data: list[dict],
    plan: dict,
    nem_version: str = "NEM2",
    true_up_month: int = 1,
    config_id: str = None,
) -> dict:
    """
    Project NEM true-up bill from interval data.

    Accumulates monthly NEM balances (import cost - export credit) and shows
    how they build toward the annual true-up settlement. Under NEM 2.0,
    monthly bills are just the Base Services Charge — NEM charges settle
    at the annual true-up date.

    Args:
        interval_data: Hourly records from parse_green_button
        plan: Rate plan config {schedule, provider, vintage_year, income_tier}
        nem_version: "NEM2" or "NEM3"
        true_up_month: Month of annual true-up (1=January)

    Returns:
        Dict with monthly_balances (NEM balance, cumulative, BSC per month),
        summary (annual_total, true_up_balance, total_bsc), worst/best months,
        and human-readable insights.
        config_id: Optional stored config ID — if provided, uses its nem_version and true_up_month
    """
    if config_id:
        cfg = _load_config(config_id)
        if nem_version == "NEM2":
            nem_version = cfg.get("nem_version", nem_version)
        if true_up_month == 1:
            true_up_month = cfg.get("true_up_month", true_up_month)
    from src.analysis.trueup import project_trueup
    return project_trueup(interval_data, plan, nem_version, true_up_month)


@mcp.tool()
async def compare_nem_versions(
    interval_data: list[dict],
    plan: dict,
    config_id: str = None,
) -> dict:
    """
    Compare annual cost under NEM 2.0 vs NEM 3.0 for the same plan and usage.

    Shows the financial impact of transitioning between NEM versions:
    - NEM 2 customers: what happens when grandfathering expires
    - NEM 3 customers: what they're paying vs NEM 2
    - Prospective solar buyers: real economics under current NEM 3

    Uses hourly Avoided Cost Calculator (ACC) values for NEM 3 — export
    credits vary dramatically by time of day (summer peak $0.25 vs midday $0.03).

    Args:
        interval_data: Hourly records from parse_green_button
        plan: Rate plan config {schedule, provider, vintage_year, income_tier}
        config_id: Optional stored config ID

    Returns:
        Dict with NEM2 and NEM3 annual costs, credit loss breakdown by TOU
        period and month, transition impact summary, and actionable insights.
    """
    from src.analysis.nem_compare import compare_nem_versions as compute
    return compute(interval_data, plan)


def _load_config(config_id: str) -> dict:
    """Load a stored config dict by ID. Raises ValueError if not found."""
    store = get_store()
    result = store.get(config_id)
    if result is None:
        raise ValueError(f"Config '{config_id}' not found")
    return result["config"]


# ── System Config Persistence Tools ──────────────────────────────────


@mcp.tool()
async def save_system_config(config_id: str, config: dict) -> dict:
    """
    Save a system configuration for later use.

    Stores the full system config (arrays, batteries, rate plan, provider, etc.)
    so it can be referenced by config_id in other tools instead of re-entering.

    Args:
        config_id: Unique identifier for this config (e.g., "brisbane-home")
        config: System configuration dict matching the reference config shape.
                Must include rate_plan, provider, etc. Arrays and batteries
                are validated on save.

    Returns:
        Dict with config_id, created_at timestamp, and status
    """
    # Validate by constructing a SystemConfig (catches bad data early)
    SystemConfig.from_dict(config)
    store = get_store()
    return store.save(config_id, config)


@mcp.tool()
async def get_system_config(config_id: str) -> dict:
    """
    Retrieve a stored system configuration.

    Args:
        config_id: The config identifier to look up

    Returns:
        Dict with config_id, config (full system config), created_at, updated_at.
        Returns error message if not found.
    """
    store = get_store()
    result = store.get(config_id)
    if result is None:
        return {"error": f"Config '{config_id}' not found", "config_id": config_id}
    return result


@mcp.tool()
async def update_system_config(config_id: str, updates: dict) -> dict:
    """
    Partially update a stored system configuration.

    Merges the updates into the existing config. Use this to change rate_plan,
    add batteries, update arrays, etc. without re-sending the full config.

    Args:
        config_id: The config identifier to update
        updates: Partial dict of fields to merge (e.g., {"rate_plan": "E-ELEC"}
                 or {"batteries": [...]})

    Returns:
        Dict with config_id, updated config, updated_at timestamp, and status
    """
    store = get_store()
    result = store.update(config_id, updates)
    # Validate the merged config
    SystemConfig.from_dict(result["config"])
    return result


@mcp.tool()
async def list_system_configs() -> dict:
    """
    List all stored system configurations.

    Returns:
        Dict with configs list (config_id, created_at, updated_at per entry)
    """
    store = get_store()
    return {"configs": store.list_all()}


@mcp.tool()
async def delete_system_config(config_id: str) -> dict:
    """
    Delete a stored system configuration.

    Args:
        config_id: The config identifier to delete

    Returns:
        Dict with config_id and status
    """
    store = get_store()
    return store.delete(config_id)


@mcp.tool()
async def powerwall_status() -> dict:
    """
    Get real-time Powerwall status via Tesla FleetAPI.

    Requires TESLA_FLEET_TOKEN environment variable. Returns battery level,
    power flow, grid status, and operating mode.

    Returns:
        Dict with battery_pct, power_flow (solar, battery, grid, home watts),
        grid_status, operating_mode, or error if not configured.
    """
    from src.integrations.tesla import get_powerwall_status
    return get_powerwall_status()


@mcp.tool()
async def solar_forecast(
    latitude: float = 37.68,
    longitude: float = -122.40,
    capacity_kw: float = 7.668,
) -> dict:
    """
    Get solar production forecast from Solcast API.

    Requires SOLCAST_API_KEY environment variable. Returns hourly forecast
    for next 48 hours plus daily totals for next 7 days. Cached aggressively
    (Hobbyist tier: 10 requests/day).

    Args:
        latitude: Site latitude (default: Brisbane, CA)
        longitude: Site longitude
        capacity_kw: Total AC capacity in kW

    Returns:
        Dict with hourly_forecast, daily_totals, or error if not configured.
    """
    from src.integrations.solcast import get_solar_forecast
    return get_solar_forecast(latitude, longitude, capacity_kw)


@mcp.tool()
async def optimize_battery(
    interval_data: list[dict],
    system_config: dict,
    rate_config: dict,
    nem_version: str = "NEM2",
    horizon_days: int = 7,
) -> dict:
    """
    Find the optimal battery charge/discharge schedule using mathematical optimization.

    Uses Pyomo + CBC solver to minimize electricity cost by scheduling battery
    dispatch across TOU periods. Finds better solutions than heuristic dispatch
    by considering the full time horizon simultaneously.

    The optimizer charges batteries during cheap off-peak hours and discharges
    during expensive peak hours, accounting for round-trip efficiency losses
    and ensuring the battery isn't artificially drained at the end.

    Args:
        interval_data: Hourly records from parse_green_button
        system_config: {
            "arrays": [{panels, panel_watts, inverter_watts_ac, type, ac_watts}],
            "batteries": [{kwh, kw, efficiency, status}],
            "psh_by_month": {"Jan": 3.2, ...}  // optional
        }
        rate_config: Rate plan config from get_rates output
        nem_version: "NEM2" (full retail export credit) or "NEM3" (avoided cost)
        horizon_days: Days to optimize (default 7, max limited by data)

    Returns:
        Dict with: optimal hourly schedule (action, kw, soc_pct per hour),
        daily summary, TOU breakdown, savings vs no-battery baseline,
        or error message if Pyomo/CBC not available.
    """
    from src.optimization.battery_optimizer import optimize_dispatch
    return optimize_dispatch(interval_data, system_config, rate_config,
                             nem_version, horizon_days)


# ── PG&E Share My Data (Green Button Connect) Tools ─────────────────


@mcp.tool()
async def connect_pge(config_id: str) -> dict:
    """
    Start PG&E Share My Data OAuth connection to auto-fetch interval usage data.

    Returns an authorization URL that the user opens in their browser to
    authorize data sharing. After authorizing, use complete_pge_connection
    with the code PG&E provides.

    Args:
        config_id: System config ID to associate the connection with

    Returns:
        Dict with auth_url, instructions, or error if not configured.
    """
    from src.integrations.pge_share_my_data import generate_auth_url
    return generate_auth_url(config_id)


@mcp.tool()
async def complete_pge_connection(config_id: str, auth_code: str) -> dict:
    """
    Complete PG&E Share My Data connection by exchanging the authorization code.

    After the user authorizes via the URL from connect_pge, they receive an
    authorization code. This tool exchanges it for access+refresh tokens and
    stores them for automatic data fetching.

    Args:
        config_id: System config ID (must match the one used in connect_pge)
        auth_code: Authorization code from PG&E OAuth redirect

    Returns:
        Dict with connection status, subscription_id, or error details.
    """
    from src.integrations.pge_share_my_data import exchange_code

    result = exchange_code(auth_code)
    if "error" in result:
        return result

    # Store tokens
    store = get_store()
    store.save_oauth_token(config_id, "pge", {
        "access_token": result["access_token"],
        "refresh_token": result["refresh_token"],
        "expires_in": result.get("expires_in", 3600),
        "scope": result.get("scope", ""),
        "subscription_id": result.get("subscription_id", ""),
    })

    return {
        "status": "connected",
        "config_id": config_id,
        "subscription_id": result.get("subscription_id", ""),
        "message": (
            "PG&E account connected successfully. "
            "Use fetch_pge_data to retrieve your interval usage data."
        ),
    }


@mcp.tool()
async def fetch_pge_data(config_id: str, start_date: str, end_date: str) -> dict:
    """
    Fetch PG&E interval usage data via Share My Data API.

    Automatically uses stored OAuth tokens and refreshes them if expired.
    Returns data in the same format as parse_green_button, so it works
    directly with compare_plans, usage_profile, simulate_system, etc.

    Args:
        config_id: System config ID with stored PG&E connection
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)

    Returns:
        Dict with intervals, summary, metadata — same shape as parse_green_button.
    """
    from src.integrations.pge_share_my_data import fetch_usage_data, refresh_access_token

    store = get_store()
    token_data = store.get_oauth_token(config_id, "pge")

    if token_data is None:
        return {
            "error": "not_connected",
            "message": (
                f"No PG&E connection found for config '{config_id}'. "
                "Use connect_pge to start the authorization flow."
            ),
        }

    access_token = token_data["access_token"]
    subscription_id = token_data.get("subscription_id", "")

    # Auto-refresh if expired
    if store.is_token_expired(config_id, "pge"):
        refresh_result = refresh_access_token(token_data["refresh_token"])
        if "error" in refresh_result:
            # If refresh fails, user needs to re-authorize
            if refresh_result["error"] == "refresh_failed":
                return {
                    "error": "reauth_required",
                    "message": (
                        "PG&E token refresh failed. The connection may have been revoked. "
                        "Use connect_pge to re-authorize."
                    ),
                    "detail": refresh_result.get("message", ""),
                }
            return refresh_result

        # Update stored tokens
        store.save_oauth_token(config_id, "pge", {
            "access_token": refresh_result["access_token"],
            "refresh_token": refresh_result.get("refresh_token", token_data["refresh_token"]),
            "expires_in": refresh_result.get("expires_in", 3600),
            "scope": token_data.get("scope", ""),
            "subscription_id": subscription_id,
        })
        access_token = refresh_result["access_token"]

    return fetch_usage_data(access_token, subscription_id, start_date, end_date)


def create_combined_app():
    """Create a combined ASGI app with both the MCP server and web interface.

    The web app is mounted at /web, and the MCP server remains at the root.
    """
    from fastapi import FastAPI
    from web.app import create_web_app

    root = FastAPI(title="PG&E Energy MCP + Web")
    web_app = create_web_app()
    root.mount("/web", web_app)

    return root


if __name__ == "__main__":
    import sys

    if "--web" in sys.argv:
        import uvicorn
        from web.app import create_web_app

        app = create_web_app()
        uvicorn.run(app, host="0.0.0.0", port=8001)
    else:
        mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
