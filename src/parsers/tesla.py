"""Parse Tesla app energy CSV exports with automatic unit normalization."""

import csv
import io
import re


def parse(csv_content: str, export_type: str = "year") -> dict:
    """
    Parse Tesla energy CSV export, normalizing all values to kWh.

    Tesla uses inconsistent units across exports:
    - 2025 yearly: Home(MWh), Vehicle(kWh), Powerwall(kWh), Solar(MWh), Grid(MWh)
    - 2026 yearly: Home(MWh), Vehicle(kWh), Powerwall(kWh), Solar(kWh), Grid(MWh)
    - Lifetime: Home(MWh), Vehicle(kWh), Powerwall(MWh), Solar(MWh), Grid(MWh)

    Returns:
        {
            "months": [{"date", "home_kwh", "vehicle_kwh", "powerwall_kwh",
                         "solar_kwh", "grid_in_kwh", "grid_out_kwh"}],
            "totals": {same fields summed},
            "column_units": {detected units per column}
        }
    """
    if csv_content.startswith("\ufeff"):
        csv_content = csv_content[1:]

    lines = csv_content.strip().split("\n")
    if not lines:
        raise ValueError("Empty CSV content")

    # Parse header to detect units
    header = lines[0]
    column_map = _parse_header(header)

    reader = csv.DictReader(lines)
    months = []
    totals = {
        "home_kwh": 0.0, "vehicle_kwh": 0.0, "powerwall_kwh": 0.0,
        "solar_kwh": 0.0, "grid_in_kwh": 0.0, "grid_out_kwh": 0.0,
    }

    for row in reader:
        record = {"date": row.get("Date time", "")}

        for col_name, (output_key, unit) in column_map.items():
            raw = row.get(col_name, "0")
            val = _to_float(raw)
            kwh = val * 1000.0 if unit == "MWh" else val
            record[output_key] = round(kwh, 2)
            totals[output_key] += kwh

        months.append(record)

    # Round totals
    totals = {k: round(v, 2) for k, v in totals.items()}

    return {
        "months": months,
        "totals": totals,
        "column_units": {col: unit for col, (_, unit) in column_map.items()},
    }


def _parse_header(header: str) -> dict[str, tuple[str, str]]:
    """
    Parse header row to map column names to (output_key, unit).

    Detects unit from parenthetical suffix: "Home (MWh)" -> ("home_kwh", "MWh")
    """
    mapping = {}
    cols = [c.strip() for c in header.split(",")]

    field_patterns = {
        "home": "home_kwh",
        "vehicle": "vehicle_kwh",
        "powerwall": "powerwall_kwh",
        "from powerwall": "powerwall_kwh",
        "solar": "solar_kwh",
        "solar energy": "solar_kwh",
        "from grid": "grid_in_kwh",
        "to grid": "grid_out_kwh",
    }

    for col in cols:
        if col.lower() in ("date time", ""):
            continue

        # Extract unit from parentheses
        unit_match = re.search(r"\((kWh|MWh)\)", col)
        unit = unit_match.group(1) if unit_match else "kWh"

        # Match field name
        col_base = re.sub(r"\s*\(.*?\)\s*", "", col).strip().lower()
        output_key = field_patterns.get(col_base)

        if output_key:
            mapping[col] = (output_key, unit)

    return mapping


def _to_float(val: str) -> float:
    """Safely convert string to float."""
    val = val.strip().replace(",", "")
    if not val or val == "-":
        return 0.0
    return float(val)
