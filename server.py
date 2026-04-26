"""
pge-energy-mcp — MCP server for PG&E solar + battery energy analysis.

Tools help PG&E residential customers answer:
- Am I on the right rate plan?
- How is my solar+battery system performing?
- What upgrades would save me the most money?
"""

from fastmcp import FastMCP
from mcp.types import Icon
from typing import Optional
import base64
import json
from pathlib import Path

from src.storage.config_store import get_store
from src.data.system_config import SystemConfig

_icon_bytes = (Path(__file__).parent / "assets" / "icon.svg").read_bytes()
_icon_data_uri = f"data:image/svg+xml;base64,{base64.b64encode(_icon_bytes).decode()}"

mcp = FastMCP(
    "PG&E Energy Analyzer",
    icons=[Icon(src=_icon_data_uri, mimeType="image/svg+xml")],
)


@mcp.tool(annotations={"title": "Parse Green Button (hourly)", "readOnlyHint": True, "openWorldHint": False})
async def parse_green_button(csv_content: str) -> dict:
    """
    Parse PG&E Green Button CSV with HOURLY interval data (~8,760 rows/year).

    This is for the "Export My Data" download (hourly intervals), NOT "Export Bill
    Totals" (monthly summaries). If the CSV has START DATE/END DATE columns with
    monthly rows, use parse_billing_data instead.

    The CSV has rows like: Electric usage,2025-03-20,00:00,00:59,2.94,0.00,$1.02
    Each row is one hour with import kWh, export kWh, and cost.

    Args:
        csv_content: Raw CSV text from PG&E Green Button "Export My Data"

    Returns:
        Dict with 'intervals' (hourly records with date, hour, month, day_of_week,
        import_kwh, export_kwh, cost), 'summary' (totals), 'metadata' (account info)
    """
    from src.parsers.green_button import parse
    return parse(csv_content)


@mcp.tool(annotations={"title": "Parse Green Button (bill totals)", "readOnlyHint": True, "openWorldHint": False})
async def parse_billing_data(csv_content: str) -> dict:
    """
    Parse PG&E Green Button "Bill Totals" CSV export into monthly billing data.

    Use this when a user uploads PG&E billing data (monthly bill summaries).
    Handles both electric and gas billing exports. Auto-detects format.

    This data shows monthly totals (import/export kWh and cost per billing period),
    NOT hourly intervals. Use this to analyze billing trends, detect true-up cycles,
    identify seasonal patterns, and compare year-over-year costs.

    The CSV typically starts with metadata (Name, Address, Account) followed by
    rows with TYPE, START DATE, END DATE, IMPORT (kWh), EXPORT (kWh), COST.

    Args:
        csv_content: Raw CSV text from PG&E Green Button "Export Bill Totals"

    Returns:
        Dict with 'bills' (monthly records), 'summary' (totals, true-up detection,
        seasonal patterns, year-over-year), and 'metadata' (account info)
    """
    from src.parsers.billing import parse
    return parse(csv_content)


@mcp.tool(annotations={"title": "Parse Tesla monthly export", "readOnlyHint": True, "openWorldHint": False})
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


@mcp.tool(annotations={"title": "Parse Tesla 5-min power data", "readOnlyHint": True, "openWorldHint": False})
async def parse_tesla_power(csv_content: str) -> dict:
    """
    Parse Tesla 5-minute power data from tesla-solar-download tool.

    This is for granular power data (5-minute intervals with watts for solar,
    battery, grid, and home) downloaded using the tesla-solar-download tool
    (https://github.com/netzero-labs/tesla-solar-download).

    Much more detailed than the Tesla app CSV export — shows exactly when your
    battery charges/discharges, solar ramps, and grid import spikes happen.

    The CSV has columns: timestamp, solar_power, battery_power, grid_power, load_power
    All values in watts. You can paste one day's file or concatenate multiple days.

    Args:
        csv_content: Raw CSV text from tesla-solar-download output

    Returns:
        Dict with 5-min intervals, hourly aggregates, daily summaries,
        battery health metrics, self-consumption %, and analysis suggestions
    """
    from src.parsers.tesla_power import parse
    return parse(csv_content)


@mcp.tool(annotations={"title": "Extract PG&E bill details", "readOnlyHint": True, "openWorldHint": False})
async def extract_bill_details(
    schedule: str,
    provider: str = "PGE_BUNDLED",
    vintage_year: int | None = None,
    income_tier: int = 3,
    nem_version: str = "NEM2",
    true_up_month: int | None = None,
) -> dict:
    """
    Validate and look up rates from details extracted from a PG&E bill.

    After the user uploads a PG&E bill (PDF or screenshot), extract these details
    from the bill and pass them to this tool for validation and rate lookup:

    How to find each field on the bill:
    - schedule: Look for "Rate Schedule" or "Schedule" (e.g., "EV2-A", "E-ELEC")
    - provider: If bill has a separate CCA section (e.g., "Peninsula Clean Energy"),
      use that CCA code. If only PG&E charges, use "PGE_BUNDLED"
    - vintage_year: Found in the PCIA line item (e.g., "Vintage 2016"). Only for CCA.
    - income_tier: "Tier 1" = CARE, "Tier 2" = FERA, "Tier 3" = standard. Look at
      the Base Services Charge line.
    - nem_version: "Net Energy Metering" section = NEM2. "Net Billing" = NEM3.
      If no NEM section, customer doesn't have solar.
    - true_up_month: Look for "True-Up Date" or "Anniversary Date" on the NEM page.

    Args:
        schedule: Rate schedule from bill (EV2-A, E-ELEC, E-TOU-C, E-TOU-D)
        provider: PGE_BUNDLED, PCE, SVCE, MCE, SJCE, or EBCE
        vintage_year: PCIA vintage year (CCA customers only, None for bundled)
        income_tier: 1 (CARE), 2 (FERA), or 3 (standard)
        nem_version: NEM2 or NEM3
        true_up_month: Month number of annual true-up (1-12)

    Returns:
        Validated rate details with effective rates, plan summary, and suggested
        next analysis steps based on the customer's specific configuration.
    """
    from src.rates.engine import lookup_rates

    if provider == "PGE_BUNDLED":
        vintage_year = None

    try:
        rates = lookup_rates(
            schedule, provider,
            vintage_year=vintage_year or 2016,
            income_tier=income_tier,
        )
    except ValueError as e:
        return {"error": str(e)}

    plan_summary = {
        "schedule": schedule,
        "provider": provider,
        "vintage_year": vintage_year,
        "income_tier": income_tier,
        "nem_version": nem_version,
        "true_up_month": true_up_month,
        "base_services_charge_daily": rates["base_services_charge_daily"],
        "base_services_charge_monthly": round(rates["base_services_charge_daily"] * 30.4, 2),
    }

    suggestions = []
    if nem_version == "NEM2":
        suggestions.append(
            "You're on NEM 2.0 — your export credits are at full retail rate. "
            "We can project what happens when grandfathering expires using compare_nem_versions."
        )
    elif nem_version == "NEM3":
        suggestions.append(
            "You're on NEM 3.0 — export credits are based on the hourly Avoided Cost Calculator. "
            "Self-consumption is worth 5-15x more than exporting. Battery optimization is key."
        )

    if schedule != "EV2-A":
        suggestions.append(
            f"You're on {schedule}. We can compare this against EV2-A and other plans "
            f"using your actual usage data to see if switching would save money."
        )

    if income_tier == 3:
        bsc = rates["base_services_charge_daily"]
        suggestions.append(
            f"Standard tier BSC is ${bsc:.2f}/day (${bsc * 365:.0f}/yr). "
            f"If you qualify for CARE or FERA, the savings are significant."
        )

    return {
        "plan": plan_summary,
        "effective_rates": rates["effective_rates"],
        "components": rates["components"],
        "tou_windows": rates["tou_windows"],
        "suggestions": suggestions,
    }


@mcp.tool(annotations={"title": "Get PG&E rate lookup", "readOnlyHint": True, "openWorldHint": False})
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


@mcp.tool(annotations={"title": "Compare rate plans", "readOnlyHint": True, "openWorldHint": False})
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


@mcp.tool(annotations={"title": "Profile energy usage", "readOnlyHint": True, "openWorldHint": False})
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


@mcp.tool(annotations={"title": "Simulate system expansion", "readOnlyHint": True, "openWorldHint": False})
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


@mcp.tool(annotations={"title": "Seasonal strategy advisor", "readOnlyHint": True, "openWorldHint": False})
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


@mcp.tool(annotations={"title": "Project NEM true-up", "readOnlyHint": True, "openWorldHint": False})
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


@mcp.tool(annotations={"title": "Compare NEM 2.0 vs 3.0", "readOnlyHint": True, "openWorldHint": False})
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


@mcp.tool(annotations={"title": "Save system config", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
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


@mcp.tool(annotations={"title": "Get system config", "readOnlyHint": True, "openWorldHint": False})
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


@mcp.tool(annotations={"title": "Update system config", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
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


@mcp.tool(annotations={"title": "List system configs", "readOnlyHint": True, "openWorldHint": False})
async def list_system_configs() -> dict:
    """
    List all stored system configurations.

    Returns:
        Dict with configs list (config_id, created_at, updated_at per entry)
    """
    store = get_store()
    return {"configs": store.list_all()}


@mcp.tool(annotations={"title": "Delete system config", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False})
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


@mcp.tool(annotations={"title": "Powerwall live status", "readOnlyHint": True, "openWorldHint": True})
async def powerwall_live() -> dict:
    """
    Get real-time Powerwall status: power flow, battery level, grid status.

    Shows what's happening right now — solar production, battery charge/discharge,
    grid import/export, home consumption, operating mode, and backup reserve.

    Requires pypowerwall configuration (see error message for setup instructions).
    Supports local Gateway connection (read-only) or Cloud/FleetAPI (read + control).

    Returns:
        Dict with power_flow (watts), battery_pct, grid_status, operating_mode,
        backup_reserve_pct, alerts, firmware version, or setup instructions.
    """
    from src.integrations.powerwall import get_live_status
    return get_live_status()


@mcp.tool(annotations={"title": "Powerwall battery details", "readOnlyHint": True, "openWorldHint": True})
async def powerwall_details() -> dict:
    """
    Get detailed Powerwall diagnostics: per-battery health, temps, string data.

    Shows per-battery block state of charge, temperatures, and solar string
    performance (voltage, current, power per string). Useful for diagnosing
    issues like a non-functioning battery or underperforming solar strings.

    Returns:
        Dict with battery_blocks, temperatures, vitals, or error if not configured.
    """
    from src.integrations.powerwall import get_battery_details
    return get_battery_details()


@mcp.tool(annotations={"title": "Set Powerwall operating mode", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
async def set_powerwall_mode(mode: str) -> dict:
    """
    Change Powerwall operating mode.

    Modes:
    - "self_consumption" — Use solar+battery first, minimize grid interaction
    - "autonomous" — Optimize for TOU savings (charge off-peak, discharge peak)
    - "backup" — Maximize battery reserve for power outages

    For TOU rate arbitrage (charge cheap overnight, discharge during peak 4-9 PM),
    use "autonomous" mode with grid charging enabled.

    Only works with Cloud or FleetAPI connection (not local Gateway).

    Args:
        mode: "self_consumption", "autonomous", or "backup"

    Returns:
        Success confirmation or error with setup instructions.
    """
    from src.integrations.powerwall import set_mode
    return set_mode(mode)


@mcp.tool(annotations={"title": "Set Powerwall backup reserve", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
async def set_powerwall_reserve(level: float) -> dict:
    """
    Set Powerwall backup reserve percentage.

    Controls how much battery capacity is held in reserve for power outages.
    - 0% = Use all battery for TOU optimization (no outage backup)
    - 20% = Keep 2.7 kWh reserved per Powerwall 2 for outages
    - 100% = Full backup mode, no TOU optimization

    For maximum cost savings, set reserve to 0-20% and let the optimizer
    handle TOU dispatch. Increase before storms or planned outages.

    Only works with Cloud or FleetAPI connection.

    Args:
        level: Reserve percentage (0-100)

    Returns:
        Success confirmation or error.
    """
    from src.integrations.powerwall import set_reserve
    return set_reserve(level)


@mcp.tool(annotations={"title": "Set Powerwall grid charging", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
async def set_powerwall_grid_charging(enabled: bool) -> dict:
    """
    Enable or disable Powerwall charging from the grid.

    When enabled, the Powerwall can charge from grid power (typically overnight
    during off-peak TOU hours) and discharge during peak hours. This is the key
    unlock for TOU arbitrage — the battery buys cheap power and sells it back
    during expensive hours.

    When disabled, the battery only charges from solar.

    Only works with Cloud or FleetAPI connection.

    Args:
        enabled: True to allow grid charging, False for solar-only charging

    Returns:
        Success confirmation or error.
    """
    from src.integrations.powerwall import set_grid_charging
    return set_grid_charging(enabled)


@mcp.tool(annotations={"title": "Set Powerwall grid export", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
async def set_powerwall_grid_export(mode: str) -> dict:
    """
    Configure how the Powerwall exports to the grid.

    Modes:
    - "battery_ok" — Export from battery when charged (default)
    - "pv_only" — Only export excess solar, never battery
    - "never" — No grid export at all

    Under NEM 2.0, "battery_ok" makes sense (full retail credit for exports).
    Under NEM 3.0, "pv_only" or "never" may be better since export credits
    are much lower — keep battery energy for self-consumption instead.

    Only works with Cloud or FleetAPI connection.

    Args:
        mode: "battery_ok", "pv_only", or "never"

    Returns:
        Success confirmation or error.
    """
    from src.integrations.powerwall import set_grid_export
    return set_grid_export(mode)


@mcp.tool(annotations={"title": "Solar production forecast", "readOnlyHint": True, "openWorldHint": True})
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


@mcp.tool(annotations={"title": "Optimize battery dispatch", "readOnlyHint": True, "openWorldHint": False})
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


@mcp.tool(annotations={"title": "Initiate PG&E connection", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
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


@mcp.tool(annotations={"title": "Complete PG&E connection", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
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


@mcp.tool(annotations={"title": "Fetch PG&E usage data", "readOnlyHint": True, "openWorldHint": True})
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


# ASGI app for deployment (Railway, uvicorn, etc.)
app = mcp.http_app()


if __name__ == "__main__":
    import sys

    if "--web" in sys.argv:
        import uvicorn
        from web.app import create_web_app

        web = create_web_app()
        uvicorn.run(web, host="0.0.0.0", port=8001)
    elif "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        import os
        port = int(os.environ.get("PORT", 8000))
        mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
