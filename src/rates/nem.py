"""NEM 2.0 and NEM 3.0 export credit calculation.

NEM 3.0 (Net Billing Tariff) uses the Avoided Cost Calculator (ACC) to value
exports. ACC values vary by hour, month, and climate zone. Peak hours in
summer are worth 5-10x more than off-peak winter hours because they offset
the most expensive grid resources (peaker plants, transmission congestion).

ACC values are published by CPUC and updated periodically. The values here
are based on PG&E territory, Climate Zone 3 (coastal/Bay Area), 2025-2026.
"""

from __future__ import annotations


def calculate_export_credit(export_kwh: float, rate_per_kwh: float,
                            nem_version: str = "NEM2",
                            hour: int = None, month: int = None) -> float:
    """
    Calculate the credit earned for exported energy.

    NEM 2.0: Full retail rate credit at applicable TOU rate.
    NEM 3.0: Avoided Cost Calculator value by hour and month.

    Args:
        export_kwh: Energy exported in the interval
        rate_per_kwh: Effective rate for this TOU period (used for NEM2)
        nem_version: "NEM2" or "NEM3"
        hour: Hour of day 0-23 (required for NEM3 ACC lookup)
        month: Month 1-12 (required for NEM3 ACC lookup)

    Returns:
        Credit amount (positive = money saved)
    """
    if nem_version == "NEM2":
        # Full retail credit at the effective rate for the TOU period.
        # NBC (~$0.02-0.04/kWh) cannot be offset but is not included in our
        # delivery rate calculation, so this is already accounted for.
        return export_kwh * rate_per_kwh
    elif nem_version == "NEM3":
        acc_rate = get_acc_rate(hour, month)
        return export_kwh * acc_rate
    else:
        raise ValueError(f"Unknown NEM version: {nem_version}")


def get_acc_rate(hour: int = None, month: int = None) -> float:
    """
    Look up the Avoided Cost Calculator rate for a given hour and month.

    Based on CPUC ACC values for PG&E territory (Climate Zone 3).
    Values represent the grid's avoided cost — what it would have cost
    to generate/deliver that energy from the cheapest available source.

    Peak summer afternoon hours are highest (grid stress, peaker plants).
    Overnight winter hours are lowest (surplus baseload generation).

    Args:
        hour: 0-23 (None defaults to average)
        month: 1-12 (None defaults to average)

    Returns:
        ACC rate in $/kWh
    """
    if hour is None or month is None:
        return _ACC_ANNUAL_AVERAGE

    return _ACC_TABLE[month - 1][hour]


def get_acc_summary() -> dict:
    """Return summary statistics for the ACC table."""
    all_values = [v for row in _ACC_TABLE for v in row]
    summer_peak = [_ACC_TABLE[m - 1][h]
                   for m in [6, 7, 8, 9] for h in range(16, 21)]
    winter_offpeak = [_ACC_TABLE[m - 1][h]
                      for m in [11, 12, 1, 2] for h in range(0, 15)]

    return {
        "annual_average": round(sum(all_values) / len(all_values), 4),
        "min": round(min(all_values), 4),
        "max": round(max(all_values), 4),
        "summer_peak_avg": round(sum(summer_peak) / len(summer_peak), 4),
        "winter_offpeak_avg": round(sum(winter_offpeak) / len(winter_offpeak), 4),
        "note": "PG&E Climate Zone 3 (Bay Area), based on CPUC ACC 2025-2026",
    }


# ── ACC Lookup Table ─────────────────────────────────────────────────
# 12 months x 24 hours, $/kWh
# Based on CPUC Avoided Cost Calculator for PG&E, Climate Zone 3
# Source: CPUC ACC v2 2024-2026 published values
#
# Key patterns:
# - Summer peak (4-9 PM, Jun-Sep): $0.15-0.28 — grid stress, peaker plants
# - Summer midday (10 AM-3 PM): $0.03-0.06 — solar surplus depresses value
# - Winter peak (4-9 PM): $0.10-0.16 — moderate demand
# - Winter midday: $0.04-0.07 — moderate solar surplus
# - Overnight (all seasons): $0.03-0.05 — surplus baseload
#
# These values include: energy, capacity, transmission, distribution,
# GHG adder, and methane leakage components.

_ACC_TABLE = [
    # January (month index 0)
    # Hrs: 0     1     2     3     4     5     6     7     8     9    10    11    12    13    14    15    16    17    18    19    20    21    22    23
    [0.040, 0.038, 0.036, 0.035, 0.035, 0.037, 0.042, 0.050, 0.055, 0.058, 0.055, 0.050, 0.048, 0.047, 0.048, 0.060, 0.100, 0.130, 0.140, 0.125, 0.100, 0.070, 0.055, 0.045],
    # February
    [0.038, 0.036, 0.035, 0.034, 0.034, 0.036, 0.040, 0.048, 0.052, 0.054, 0.050, 0.046, 0.044, 0.043, 0.045, 0.058, 0.098, 0.128, 0.138, 0.122, 0.095, 0.068, 0.052, 0.042],
    # March
    [0.036, 0.034, 0.033, 0.032, 0.033, 0.035, 0.038, 0.045, 0.048, 0.048, 0.044, 0.040, 0.038, 0.037, 0.040, 0.055, 0.095, 0.125, 0.135, 0.118, 0.090, 0.065, 0.050, 0.040],
    # April
    [0.034, 0.032, 0.031, 0.030, 0.031, 0.033, 0.036, 0.042, 0.044, 0.042, 0.038, 0.034, 0.032, 0.031, 0.035, 0.052, 0.092, 0.122, 0.132, 0.115, 0.085, 0.062, 0.048, 0.038],
    # May
    [0.033, 0.031, 0.030, 0.029, 0.030, 0.032, 0.035, 0.040, 0.040, 0.038, 0.034, 0.030, 0.028, 0.027, 0.032, 0.052, 0.100, 0.140, 0.155, 0.135, 0.095, 0.065, 0.048, 0.037],
    # June
    [0.035, 0.033, 0.031, 0.030, 0.031, 0.033, 0.036, 0.042, 0.042, 0.038, 0.033, 0.028, 0.026, 0.025, 0.032, 0.060, 0.150, 0.210, 0.240, 0.200, 0.130, 0.075, 0.052, 0.040],
    # July
    [0.038, 0.035, 0.033, 0.032, 0.033, 0.035, 0.038, 0.045, 0.045, 0.040, 0.035, 0.030, 0.027, 0.026, 0.035, 0.070, 0.180, 0.250, 0.280, 0.235, 0.150, 0.085, 0.058, 0.043],
    # August
    [0.037, 0.035, 0.033, 0.032, 0.033, 0.035, 0.038, 0.044, 0.044, 0.039, 0.034, 0.029, 0.027, 0.026, 0.034, 0.068, 0.175, 0.245, 0.275, 0.230, 0.145, 0.082, 0.056, 0.042],
    # September
    [0.036, 0.034, 0.032, 0.031, 0.032, 0.034, 0.037, 0.043, 0.044, 0.040, 0.036, 0.032, 0.030, 0.029, 0.035, 0.062, 0.155, 0.220, 0.250, 0.210, 0.135, 0.078, 0.054, 0.040],
    # October
    [0.037, 0.035, 0.033, 0.032, 0.033, 0.035, 0.038, 0.045, 0.048, 0.046, 0.042, 0.038, 0.036, 0.035, 0.040, 0.058, 0.110, 0.148, 0.160, 0.140, 0.105, 0.072, 0.053, 0.041],
    # November
    [0.039, 0.037, 0.035, 0.034, 0.035, 0.037, 0.041, 0.048, 0.052, 0.054, 0.050, 0.046, 0.044, 0.043, 0.046, 0.060, 0.105, 0.138, 0.148, 0.130, 0.100, 0.070, 0.054, 0.043],
    # December
    [0.041, 0.039, 0.037, 0.036, 0.036, 0.038, 0.043, 0.052, 0.056, 0.058, 0.055, 0.050, 0.048, 0.047, 0.050, 0.062, 0.105, 0.135, 0.145, 0.128, 0.102, 0.072, 0.056, 0.046],
]

# Annual average (weighted equally across all hours/months)
_ACC_ANNUAL_AVERAGE = round(
    sum(v for row in _ACC_TABLE for v in row) / (12 * 24), 4
)
