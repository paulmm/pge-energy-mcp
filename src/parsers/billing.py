"""Parse PG&E Green Button billing data exports (monthly bill totals)."""

import csv
import io
from datetime import date


def parse(csv_content: str) -> dict:
    """
    Parse PG&E Green Button "Bill Totals" CSV into structured monthly data.

    Handles both electric billing (import/export/cost) and gas billing (therms/cost).
    Auto-detects format from column headers.

    Returns:
        {
            "metadata": {"name", "address", "account", "service", "date_range"},
            "service_type": "electric" | "gas",
            "bills": [{start_date, end_date, days, import_kwh, export_kwh, cost, ...}],
            "summary": {totals, true_up detection, seasonal patterns}
        }
    """
    if csv_content.startswith("\ufeff"):
        csv_content = csv_content[1:]

    lines = csv_content.strip().split("\n")

    metadata = {}
    data_start = 0
    for i, line in enumerate(lines):
        if line.startswith("TYPE,"):
            data_start = i
            break
        if "," in line and line.strip():
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

    header_line = lines[data_start] if data_start < len(lines) else ""
    is_gas = "therms" in header_line.lower()

    reader = csv.DictReader(lines[data_start:])
    bills = []

    for row in reader:
        start = row.get("START DATE", "")
        end = row.get("END DATE", "")
        if not start or not end:
            continue

        start_dt = date.fromisoformat(start)
        end_dt = date.fromisoformat(end)
        days = (end_dt - start_dt).days

        if is_gas:
            therms = _clean_number(row.get("USAGE (therms)", "0"))
            cost = _clean_number(row.get("COST", "0"))
            bills.append({
                "start_date": start,
                "end_date": end,
                "days": days,
                "therms": therms,
                "cost": cost,
                "cost_per_therm": round(cost / therms, 4) if therms > 0 else 0,
            })
        else:
            import_kwh = _clean_number(row.get("IMPORT (kWh)", "0"))
            export_kwh = _clean_number(row.get("EXPORT (kWh)", "0"))
            cost = _clean_number(row.get("COST", "0"))
            bills.append({
                "start_date": start,
                "end_date": end,
                "days": days,
                "import_kwh": import_kwh,
                "export_kwh": export_kwh,
                "net_kwh": round(import_kwh - export_kwh, 2),
                "cost": cost,
            })

    metadata["date_range"] = {
        "start": bills[0]["start_date"],
        "end": bills[-1]["end_date"],
    } if bills else None

    summary = _build_summary(bills, is_gas)

    has_solar = any(b.get("export_kwh", 0) > 0 for b in bills) if not is_gas else False

    next_steps = _suggest_next_steps(bills, is_gas, has_solar)

    return {
        "metadata": metadata,
        "service_type": "gas" if is_gas else "electric",
        "bills": bills,
        "summary": summary,
        "next_steps": next_steps,
    }


def _build_summary(bills: list, is_gas: bool) -> dict:
    """Build summary statistics from billing data."""
    if not bills:
        return {}

    if is_gas:
        total_therms = sum(b["therms"] for b in bills)
        total_cost = sum(b["cost"] for b in bills)
        return {
            "total_therms": round(total_therms, 2),
            "total_cost": round(total_cost, 2),
            "num_bills": len(bills),
            "avg_monthly_therms": round(total_therms / len(bills), 1),
            "avg_monthly_cost": round(total_cost / len(bills), 2),
        }

    total_import = sum(b["import_kwh"] for b in bills)
    total_export = sum(b["export_kwh"] for b in bills)
    total_cost = sum(b["cost"] for b in bills)

    # Detect true-up bills (unusually large costs)
    costs = [b["cost"] for b in bills]
    avg_cost = total_cost / len(bills) if bills else 0
    true_ups = [b for b in bills if b["cost"] > avg_cost * 3 and b["cost"] > 500]

    # Detect solar activation (first bill with export > 0)
    solar_start = None
    for b in bills:
        if b["export_kwh"] > 0:
            solar_start = b["start_date"]
            break

    # Seasonal analysis (post-solar only)
    solar_bills = [b for b in bills if b["export_kwh"] > 0]
    seasonal = {}
    if solar_bills:
        for b in solar_bills:
            month = date.fromisoformat(b["start_date"]).month
            season = "summer" if month in (6, 7, 8, 9) else "winter"
            if season not in seasonal:
                seasonal[season] = {"import_kwh": 0, "export_kwh": 0,
                                     "cost": 0, "count": 0}
            seasonal[season]["import_kwh"] += b["import_kwh"]
            seasonal[season]["export_kwh"] += b["export_kwh"]
            seasonal[season]["cost"] += b["cost"]
            seasonal[season]["count"] += 1

        for s in seasonal.values():
            if s["count"] > 0:
                s["avg_monthly_import"] = round(s["import_kwh"] / s["count"], 1)
                s["avg_monthly_export"] = round(s["export_kwh"] / s["count"], 1)
                s["avg_monthly_net"] = round(
                    (s["import_kwh"] - s["export_kwh"]) / s["count"], 1)

    # Year-over-year if enough data
    yoy = {}
    for b in bills:
        year = date.fromisoformat(b["start_date"]).year
        if year not in yoy:
            yoy[year] = {"import_kwh": 0, "export_kwh": 0, "cost": 0, "count": 0}
        yoy[year]["import_kwh"] += b["import_kwh"]
        yoy[year]["export_kwh"] += b["export_kwh"]
        yoy[year]["cost"] += b["cost"]
        yoy[year]["count"] += 1

    return {
        "total_import_kwh": round(total_import, 2),
        "total_export_kwh": round(total_export, 2),
        "total_cost": round(total_cost, 2),
        "num_bills": len(bills),
        "avg_monthly_import_kwh": round(total_import / len(bills), 1),
        "avg_monthly_export_kwh": round(total_export / len(bills), 1),
        "avg_monthly_cost": round(total_cost / len(bills), 2),
        "solar_start_date": solar_start,
        "true_up_bills": [{
            "date": t["end_date"],
            "cost": t["cost"],
        } for t in true_ups],
        "seasonal": seasonal,
        "yearly": {str(y): {
            "import_kwh": round(d["import_kwh"], 1),
            "export_kwh": round(d["export_kwh"], 1),
            "cost": round(d["cost"], 2),
            "months": d["count"],
        } for y, d in sorted(yoy.items())},
    }


def _suggest_next_steps(bills: list, is_gas: bool, has_solar: bool) -> list:
    """Generate guided next steps based on what the data reveals."""
    steps = []

    if is_gas:
        steps.append({
            "action": "ask_user",
            "message": "This is gas billing data. Do you also have your electric billing data or Green Button hourly export? Electric data is needed for solar/battery analysis.",
        })
        return steps

    if has_solar:
        steps.append({
            "action": "detected",
            "message": "Solar system detected — your data shows grid exports under NEM.",
        })
        steps.append({
            "action": "ask_user",
            "message": "Do you have a battery system (e.g., Tesla Powerwall, Enphase, Franklin)? If you have a Tesla Powerwall, there are two ways to get battery data:\n\n"
                       "1. **Quick:** Tesla app > Settings > Energy Data > Download My Data (monthly summaries)\n"
                       "2. **Detailed (recommended):** Use tesla-solar-download (github.com/netzero-labs/tesla-solar-download) to pull 5-minute power data going back to installation. This shows exactly when your battery charges/discharges and catches issues the app summary misses.\n\n"
                       "Upload either format and we'll analyze your battery performance.",
        })
        steps.append({
            "action": "ask_user",
            "message": "Upload your latest PG&E bill (PDF or screenshot). We can read your rate plan, NEM version, provider, PCIA vintage, and income tier directly from the bill — no need to look them up yourself.",
            "why": "Your PG&E bill contains all the plan details we need: rate schedule (e.g. EV2-A), whether you're with a CCA like PCE or bundled PG&E, your PCIA vintage year, NEM version, and income tier for base services charges.",
            "fallback": "If you don't have your bill handy, just tell us: What rate plan are you on? (e.g., EV2-A, E-ELEC, E-TOU-C, E-TOU-D) Are you bundled PG&E or with a CCA provider?",
        })
        steps.append({
            "action": "ask_user",
            "message": "For deeper analysis, download PG&E's hourly interval data: pge.com > Account > Energy Usage > Green Button > 'Export My Data' (not Bill Totals). Hourly data enables TOU optimization, battery scheduling, and system expansion modeling.",
            "why": "Billing data shows monthly totals, but hourly data shows exactly when you import and export — critical for optimizing TOU rates and battery dispatch.",
        })
    else:
        steps.append({
            "action": "ask_user",
            "message": "Your data shows no solar exports. Do you have solar panels, or are you considering getting them? If you have solar but exports aren't showing, try downloading hourly data instead: pge.com > Green Button > 'Export My Data' (not Bill Totals).",
        })
        steps.append({
            "action": "ask_user",
            "message": "Upload your latest PG&E bill (PDF or screenshot) so we can identify your rate plan and check if a different plan would save you money. Or just tell us your rate plan (e.g., EV2-A, E-ELEC, E-TOU-C, E-TOU-D).",
        })

    return steps


def _clean_number(val: str) -> float:
    """Strip $, commas, quotes, and whitespace from a numeric string."""
    val = val.strip().strip('"').replace("$", "").replace(",", "")
    if not val or val == "-":
        return 0.0
    return float(val)
