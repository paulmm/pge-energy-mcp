"""Parse PG&E Green Button CSV exports into structured hourly interval data."""

import csv
import io
from datetime import date


def parse(csv_content: str) -> dict:
    """
    Parse PG&E Green Button CSV into structured data.

    Handles BOM, skips header metadata lines, strips $ and commas from values.

    Returns:
        {
            "metadata": {"name", "address", "account", "service", "date_range"},
            "intervals": [{"date", "hour", "month", "day_of_week", "import_kwh",
                           "export_kwh", "cost"}],
            "summary": {"total_import_kwh", "total_export_kwh", "total_cost",
                        "num_intervals", "date_range"}
        }
    """
    # Strip BOM if present
    if csv_content.startswith("\ufeff"):
        csv_content = csv_content[1:]

    lines = csv_content.strip().split("\n")

    # Parse metadata header
    metadata = {}
    data_start = 0
    for i, line in enumerate(lines):
        if line.startswith("TYPE,DATE"):
            data_start = i
            break
        if "," in line and not line.strip() == "":
            parts = line.split(",", 1)
            key = parts[0].strip().lower()
            val = parts[1].strip().strip('"') if len(parts) > 1 else ""
            if key == "name":
                metadata["name"] = val
            elif key == "address":
                metadata["address"] = val
            elif key == "account number":
                metadata["account"] = val
            elif key == "service":
                metadata["service"] = val

    # Parse data rows
    reader = csv.DictReader(lines[data_start:])
    intervals = []
    total_import = 0.0
    total_export = 0.0
    total_cost = 0.0

    for row in reader:
        dt = date.fromisoformat(row["DATE"])
        hour = int(row["START TIME"].split(":")[0])

        import_kwh = _clean_number(row["IMPORT (kWh)"])
        export_kwh = _clean_number(row["EXPORT (kWh)"])
        cost = _clean_number(row["COST"])

        intervals.append({
            "date": row["DATE"],
            "hour": hour,
            "month": dt.month,
            "day_of_week": dt.weekday(),  # 0=Mon, 6=Sun
            "import_kwh": import_kwh,
            "export_kwh": export_kwh,
            "cost": cost,
        })

        total_import += import_kwh
        total_export += export_kwh
        total_cost += cost

    date_range = None
    if intervals:
        date_range = {"start": intervals[0]["date"], "end": intervals[-1]["date"]}

    metadata["date_range"] = date_range

    has_solar = total_export > 0
    next_steps = []

    if has_solar:
        next_steps.append({
            "action": "detected",
            "message": "Solar system detected — your hourly data shows grid exports under NEM.",
        })
        next_steps.append({
            "action": "ask_user",
            "message": "Do you have a battery system (e.g., Tesla Powerwall, Enphase, Franklin)? If you have a Tesla Powerwall, there are two ways to get battery data:\n\n"
                       "1. **Quick:** Tesla app > Settings > Energy Data > Download My Data (monthly summaries)\n"
                       "2. **Detailed (recommended):** Use tesla-solar-download (github.com/netzero-labs/tesla-solar-download) to pull 5-minute power data going back to installation. This shows exactly when your battery charges/discharges and catches issues the app summary misses.\n\n"
                       "Upload either format and we'll analyze your battery performance.",
        })
        next_steps.append({
            "action": "ask_user",
            "message": "Upload your latest PG&E bill (PDF or screenshot). We can read your rate plan, NEM version, provider, PCIA vintage, and income tier directly from the bill — no need to look them up yourself.",
            "why": "Your PG&E bill contains all the plan details we need: rate schedule (e.g. EV2-A), whether you're with a CCA like PCE or bundled PG&E, your PCIA vintage year, NEM version, and income tier.",
            "fallback": "If you don't have your bill handy, just tell us: What rate plan are you on? Are you bundled PG&E or with a CCA provider?",
        })
        next_steps.append({
            "action": "suggest_tools",
            "tools": ["usage_profile", "compare_plans", "compare_nem_versions"],
            "message": "With your bill details, we can run: usage profiling (peak exposure, baseload, seasonal patterns), rate plan comparison (find the cheapest plan), and NEM 2 vs 3 transition analysis.",
        })
    else:
        next_steps.append({
            "action": "ask_user",
            "message": "Your data shows no solar exports. Do you have solar panels, or are you considering getting them? We can still analyze your usage patterns and find the best rate plan.",
        })
        next_steps.append({
            "action": "ask_user",
            "message": "Upload your latest PG&E bill (PDF or screenshot) so we can identify your rate plan and check if a different plan would save you money. Or just tell us your rate plan (e.g., EV2-A, E-ELEC, E-TOU-C, E-TOU-D).",
        })

    return {
        "metadata": metadata,
        "intervals": intervals,
        "summary": {
            "total_import_kwh": round(total_import, 2),
            "total_export_kwh": round(total_export, 2),
            "total_cost": round(total_cost, 2),
            "num_intervals": len(intervals),
            "date_range": date_range,
        },
        "next_steps": next_steps,
    }


def _clean_number(val: str) -> float:
    """Strip $, commas, and whitespace from a numeric string."""
    val = val.strip().replace("$", "").replace(",", "")
    if not val or val == "-":
        return 0.0
    return float(val)
