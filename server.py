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
) -> dict:
    """
    Compare annual cost across multiple rate plan configurations using actual usage data.
    
    Args:
        interval_data: Hourly records from parse_green_button (list of dicts with
                       datetime, import_kwh, export_kwh, hour, month fields)
        plans: List of plan configs, each with {schedule, provider, vintage_year, income_tier}
        nem_version: "NEM2" (full retail export credit) or "NEM3" (avoided cost export credit)
        
    Returns:
        Dict with annual cost per plan, savings vs baseline, breakdown by TOU period,
        and recommendation
    """
    from src.analysis.compare import compare
    return compare(interval_data, plans, nem_version)


@mcp.tool()
async def usage_profile(interval_data: list[dict]) -> dict:
    """
    Generate a comprehensive usage profile from hourly interval data.
    
    Args:
        interval_data: Hourly records from parse_green_button
        
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
    """
    from src.analysis.simulator import simulate
    return simulate(interval_data, system_config, rate_config, nem_version)


@mcp.tool()
async def seasonal_strategy(
    interval_data: list[dict],
    rate_config: dict,
    system_config: dict = None,
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
    """
    from src.analysis.strategy import seasonal_strategy as compute
    return compute(interval_data, rate_config, system_config)


@mcp.tool()
async def nem_projection(
    interval_data: list[dict],
    plan: dict,
    nem_version: str = "NEM2",
    true_up_month: int = 1,
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
    """
    from src.analysis.trueup import project_trueup
    return project_trueup(interval_data, plan, nem_version, true_up_month)


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


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
