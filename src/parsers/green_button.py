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
    }


def _clean_number(val: str) -> float:
    """Strip $, commas, and whitespace from a numeric string."""
    val = val.strip().replace("$", "").replace(",", "")
    if not val or val == "-":
        return 0.0
    return float(val)
