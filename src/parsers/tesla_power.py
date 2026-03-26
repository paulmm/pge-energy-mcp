"""Parse Tesla 5-minute power data from tesla-solar-download tool.

tesla-solar-download (https://github.com/netzero-labs/tesla-solar-download)
produces daily CSV files with 5-minute interval power readings:

    timestamp, solar_power, battery_power, grid_power, load_power

All values in watts. One file per day, ~288 rows per file.
This parser handles single files or concatenated multi-day data.
"""

import csv
import io
from collections import defaultdict
from datetime import datetime


def parse(csv_content: str) -> dict:
    """
    Parse Tesla 5-minute power CSV into structured interval data.

    Args:
        csv_content: Raw CSV text (single day or concatenated multi-day)

    Returns:
        {
            "intervals": [{timestamp, solar_w, battery_w, grid_w, home_w, ...}],
            "hourly": [{hour aggregates with avg/max watts and kwh}],
            "daily": [{daily summaries}],
            "summary": {totals and key metrics},
            "next_steps": [guided analysis suggestions]
        }
    """
    if csv_content.startswith("\ufeff"):
        csv_content = csv_content[1:]

    lines = csv_content.strip().split("\n")
    if not lines:
        raise ValueError("Empty CSV content")

    # Normalize headers (tesla-solar-download uses space-separated with underscores)
    header = lines[0].strip()
    header = _normalize_header(header)
    lines[0] = header

    reader = csv.DictReader(lines)
    intervals = []
    hourly_buckets = defaultdict(lambda: {
        "solar_w": [], "battery_w": [], "grid_w": [], "home_w": [],
    })
    daily_buckets = defaultdict(lambda: {
        "solar_wh": 0.0, "battery_in_wh": 0.0, "battery_out_wh": 0.0,
        "grid_in_wh": 0.0, "grid_out_wh": 0.0, "home_wh": 0.0,
        "intervals": 0, "peak_solar_w": 0.0, "peak_home_w": 0.0,
    })

    for raw_row in reader:
        # Strip whitespace from keys (CSV may have "timestamp, solar_power, ...")
        row = {k.strip(): v for k, v in raw_row.items()}
        ts = row.get("timestamp", "").strip()
        if not ts:
            continue

        dt = _parse_timestamp(ts)
        if dt is None:
            continue

        solar = _to_float(row.get("solar_power", "0"))
        battery = _to_float(row.get("battery_power", "0"))
        grid = _to_float(row.get("grid_power", "0"))
        home = _to_float(row.get("load_power", "0"))

        interval = {
            "timestamp": ts,
            "date": dt.strftime("%Y-%m-%d"),
            "hour": dt.hour,
            "minute": dt.minute,
            "solar_w": round(solar, 1),
            "battery_w": round(battery, 1),  # positive = discharging, negative = charging
            "grid_w": round(grid, 1),  # positive = importing, negative = exporting
            "home_w": round(home, 1),
        }
        intervals.append(interval)

        # Hourly buckets
        hour_key = f"{dt.strftime('%Y-%m-%d')}_{dt.hour:02d}"
        hourly_buckets[hour_key]["solar_w"].append(solar)
        hourly_buckets[hour_key]["battery_w"].append(battery)
        hourly_buckets[hour_key]["grid_w"].append(grid)
        hourly_buckets[hour_key]["home_w"].append(home)

        # Daily buckets (5-min interval = 1/12 hour)
        day_key = dt.strftime("%Y-%m-%d")
        wh_factor = 5.0 / 60.0  # 5-minute interval to hours
        daily_buckets[day_key]["solar_wh"] += solar * wh_factor
        daily_buckets[day_key]["home_wh"] += home * wh_factor
        daily_buckets[day_key]["intervals"] += 1
        daily_buckets[day_key]["peak_solar_w"] = max(
            daily_buckets[day_key]["peak_solar_w"], solar)
        daily_buckets[day_key]["peak_home_w"] = max(
            daily_buckets[day_key]["peak_home_w"], home)

        if battery > 0:  # discharging
            daily_buckets[day_key]["battery_out_wh"] += battery * wh_factor
        else:  # charging
            daily_buckets[day_key]["battery_in_wh"] += abs(battery) * wh_factor

        if grid > 0:  # importing
            daily_buckets[day_key]["grid_in_wh"] += grid * wh_factor
        else:  # exporting
            daily_buckets[day_key]["grid_out_wh"] += abs(grid) * wh_factor

    # Build hourly summaries
    hourly = []
    for key in sorted(hourly_buckets.keys()):
        bucket = hourly_buckets[key]
        date_str, hour_str = key.rsplit("_", 1)
        n = len(bucket["solar_w"]) or 1
        wh_factor = 5.0 / 60.0

        hourly.append({
            "date": date_str,
            "hour": int(hour_str),
            "solar_avg_w": round(sum(bucket["solar_w"]) / n, 1),
            "solar_kwh": round(sum(bucket["solar_w"]) * wh_factor / 1000, 3),
            "battery_avg_w": round(sum(bucket["battery_w"]) / n, 1),
            "grid_avg_w": round(sum(bucket["grid_w"]) / n, 1),
            "grid_kwh": round(sum(bucket["grid_w"]) * wh_factor / 1000, 3),
            "home_avg_w": round(sum(bucket["home_w"]) / n, 1),
            "home_kwh": round(sum(bucket["home_w"]) * wh_factor / 1000, 3),
        })

    # Build daily summaries
    daily = []
    for day_key in sorted(daily_buckets.keys()):
        d = daily_buckets[day_key]
        daily.append({
            "date": day_key,
            "solar_kwh": round(d["solar_wh"] / 1000, 2),
            "home_kwh": round(d["home_wh"] / 1000, 2),
            "grid_in_kwh": round(d["grid_in_wh"] / 1000, 2),
            "grid_out_kwh": round(d["grid_out_wh"] / 1000, 2),
            "battery_in_kwh": round(d["battery_in_wh"] / 1000, 2),
            "battery_out_kwh": round(d["battery_out_wh"] / 1000, 2),
            "self_consumption_pct": round(
                (1 - d["grid_in_wh"] / d["home_wh"]) * 100, 1
            ) if d["home_wh"] > 0 else 0,
            "peak_solar_w": round(d["peak_solar_w"], 0),
            "peak_home_w": round(d["peak_home_w"], 0),
            "intervals": d["intervals"],
        })

    # Summary
    total_solar = sum(d["solar_kwh"] for d in daily)
    total_home = sum(d["home_kwh"] for d in daily)
    total_grid_in = sum(d["grid_in_kwh"] for d in daily)
    total_grid_out = sum(d["grid_out_kwh"] for d in daily)
    total_batt_in = sum(d["battery_in_kwh"] for d in daily)
    total_batt_out = sum(d["battery_out_kwh"] for d in daily)
    num_days = len(daily)

    summary = {
        "num_intervals": len(intervals),
        "num_days": num_days,
        "date_range": {
            "start": daily[0]["date"] if daily else None,
            "end": daily[-1]["date"] if daily else None,
        },
        "total_solar_kwh": round(total_solar, 2),
        "total_home_kwh": round(total_home, 2),
        "total_grid_in_kwh": round(total_grid_in, 2),
        "total_grid_out_kwh": round(total_grid_out, 2),
        "total_battery_in_kwh": round(total_batt_in, 2),
        "total_battery_out_kwh": round(total_batt_out, 2),
        "avg_daily_solar_kwh": round(total_solar / num_days, 2) if num_days else 0,
        "avg_daily_home_kwh": round(total_home / num_days, 2) if num_days else 0,
        "self_consumption_pct": round(
            (1 - total_grid_in / total_home) * 100, 1
        ) if total_home > 0 else 0,
        "battery_efficiency_pct": round(
            total_batt_out / total_batt_in * 100, 1
        ) if total_batt_in > 0 else 0,
    }

    # Battery health indicators
    if total_batt_in > 0:
        summary["battery_health"] = {
            "roundtrip_efficiency": summary["battery_efficiency_pct"],
            "avg_daily_cycles": round(
                (total_batt_in / num_days) / 13.5, 2  # 13.5 kWh per PW2 cycle
            ) if num_days else 0,
            "note": "Healthy PW2 efficiency is 88-92%. Below 85% may indicate degradation.",
        }

    next_steps = _suggest_next_steps(summary, daily)

    return {
        "intervals": intervals,
        "hourly": hourly,
        "daily": daily,
        "summary": summary,
        "next_steps": next_steps,
    }


def _suggest_next_steps(summary: dict, daily: list) -> list:
    """Generate guided next steps based on Tesla power data."""
    steps = []

    steps.append({
        "action": "detected",
        "message": f"Tesla Powerwall data parsed: {summary['num_days']} days, "
                   f"5-minute resolution ({summary['num_intervals']} intervals).",
    })

    if summary.get("battery_efficiency_pct", 0) < 85:
        steps.append({
            "action": "alert",
            "message": f"Battery roundtrip efficiency is {summary['battery_efficiency_pct']}% "
                       f"— below the healthy 88-92% range. This may indicate battery degradation.",
        })

    if summary["self_consumption_pct"] < 60:
        steps.append({
            "action": "suggest_tool",
            "tool": "optimize_battery",
            "message": f"Self-consumption is only {summary['self_consumption_pct']}%. "
                       f"Battery optimization could shift more solar to self-use instead of grid export.",
        })

    steps.append({
        "action": "suggest_tool",
        "tool": "usage_profile",
        "message": "Combine this Tesla data with your Green Button data for a complete picture — "
                   "Green Button shows costs, Tesla shows what your system was actually doing.",
    })

    return steps


def _normalize_header(header: str) -> str:
    """Normalize various header formats to consistent column names."""
    # tesla-solar-download format: "timestamp, solar_power, battery_power, grid_power, load_power"
    # Some versions may use slightly different naming
    header = header.strip()
    replacements = {
        "solar_power": "solar_power",
        "battery_power": "battery_power",
        "grid_power": "grid_power",
        "load_power": "load_power",
        "home_power": "load_power",
    }
    for old, new in replacements.items():
        header = header.replace(old, new)
    return header


def _parse_timestamp(ts: str) -> datetime | None:
    """Parse various timestamp formats."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(ts.strip(), fmt)
        except ValueError:
            continue
    return None


def _to_float(val: str) -> float:
    """Safely convert string to float."""
    val = val.strip().replace(",", "")
    if not val or val == "-":
        return 0.0
    return float(val)
