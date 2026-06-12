"""Usage profiling: self-consumption, peak exposure, seasonal patterns, baseload."""

from collections import defaultdict
from src.rates.tou import classify_tou_period, get_schedule_config


def profile(interval_data: list[dict], schedule: str = "EV2-A") -> dict:
    """
    Generate a comprehensive usage profile from hourly interval data.

    Args:
        interval_data: List of dicts from parse_green_button with
                       date, hour, month, day_of_week, import_kwh, export_kwh
        schedule: Rate schedule for TOU classification (default EV2-A)

    Returns:
        Dict with self_consumption_ratio, peak_exposure_pct, seasonal breakdown,
        overnight_baseload_kwh, monthly_trends, top_import_days
    """
    sched_config = get_schedule_config(schedule)

    total_import = 0.0
    total_export = 0.0

    # TOU period breakdowns
    period_import = defaultdict(float)
    period_export = defaultdict(float)
    season_import = defaultdict(float)
    season_export = defaultdict(float)

    # Daily aggregates
    daily_import = defaultdict(float)
    daily_export = defaultdict(float)

    # Monthly aggregates
    monthly_import = defaultdict(float)
    monthly_export = defaultdict(float)
    monthly_days = defaultdict(set)

    # Overnight baseload (midnight-5am)
    overnight_hours = 0
    overnight_import = 0.0

    # Weekday vs weekend
    weekday_import = 0.0
    weekday_hours = 0
    weekend_import = 0.0
    weekend_hours = 0

    for iv in interval_data:
        imp = iv["import_kwh"]
        exp = iv["export_kwh"]
        hour = iv["hour"]
        month = iv["month"]
        dow = iv["day_of_week"]
        dt = iv["date"]

        total_import += imp
        total_export += exp

        period, season = classify_tou_period(hour, month, dow,
                                             schedule_config=sched_config,
                                             date_str=dt)
        period_import[period] += imp
        period_export[period] += exp
        season_import[season] += imp
        season_export[season] += exp

        daily_import[dt] += imp
        daily_export[dt] += exp

        monthly_import[month] += imp
        monthly_export[month] += exp
        monthly_days[month].add(dt)

        # Overnight baseload: 0-5 AM
        if 0 <= hour <= 4:
            overnight_hours += 1
            overnight_import += imp

        if dow < 5:
            weekday_import += imp
            weekday_hours += 1
        else:
            weekend_import += imp
            weekend_hours += 1

    # Self-consumption: what fraction of solar was used on-site vs exported
    # Total solar production ≈ self-consumed + exported
    # Self-consumed solar ≈ total_export gives us the export; solar = self_consumed + export
    # But we don't have direct solar production from Green Button data.
    # We can estimate: net_consumption = import - export
    # Grid dependency = import / (import + self_consumed_solar)
    # For NEM: self_consumed solar = solar_production - export
    # Without solar production data, report what we can.

    # Peak exposure: % of imports during peak hours
    peak_import = period_import.get("peak", 0.0)
    peak_exposure_pct = round(peak_import / total_import * 100, 1) if total_import > 0 else 0.0

    # Partial peak exposure
    partial_import = period_import.get("partial_peak", 0.0)
    partial_exposure_pct = round(partial_import / total_import * 100, 1) if total_import > 0 else 0.0

    off_peak_import = period_import.get("off_peak", 0.0)
    off_peak_pct = round(off_peak_import / total_import * 100, 1) if total_import > 0 else 0.0

    # Overnight baseload (average kWh/hour during midnight-5am)
    baseload_kwh_per_hr = round(overnight_import / overnight_hours, 2) if overnight_hours > 0 else 0.0

    # Seasonal daily averages
    seasonal_daily = {}
    for season in ["summer", "winter"]:
        season_days = set()
        for dt, imp in daily_import.items():
            m = int(dt.split("-")[1])
            _, s = classify_tou_period(12, m, 0, schedule_config=sched_config)
            if s == season:
                season_days.add(dt)
        if season_days:
            total_s = sum(daily_import[d] for d in season_days)
            seasonal_daily[season] = {
                "avg_daily_import_kwh": round(total_s / len(season_days), 1),
                "num_days": len(season_days),
                "total_import_kwh": round(total_s, 1),
            }

    # Monthly trends
    monthly_trends = []
    for m in sorted(monthly_import.keys()):
        num_days = len(monthly_days[m])
        monthly_trends.append({
            "month": m,
            "import_kwh": round(monthly_import[m], 1),
            "export_kwh": round(monthly_export[m], 1),
            "avg_daily_import_kwh": round(monthly_import[m] / num_days, 1) if num_days else 0,
            "num_days": num_days,
        })

    # Top import days
    top_days = sorted(daily_import.items(), key=lambda x: x[1], reverse=True)[:10]

    ev_charging = detect_ev_charging(interval_data, sched_config)

    return {
        "ev_charging": ev_charging,
        "total_import_kwh": round(total_import, 1),
        "total_export_kwh": round(total_export, 1),
        "net_consumption_kwh": round(total_import - total_export, 1),
        "peak_exposure_pct": peak_exposure_pct,
        "partial_peak_exposure_pct": partial_exposure_pct,
        "off_peak_pct": off_peak_pct,
        "tou_import_breakdown": {k: round(v, 1) for k, v in period_import.items()},
        "tou_export_breakdown": {k: round(v, 1) for k, v in period_export.items()},
        "overnight_baseload_kwh_per_hr": baseload_kwh_per_hr,
        "seasonal_daily_averages": seasonal_daily,
        "weekday_avg_daily_kwh": round(weekday_import / (weekday_hours / 24), 1) if weekday_hours else 0,
        "weekend_avg_daily_kwh": round(weekend_import / (weekend_hours / 24), 1) if weekend_hours else 0,
        "monthly_trends": monthly_trends,
        "top_import_days": [{"date": d, "import_kwh": round(v, 1)} for d, v in top_days],
    }


def detect_ev_charging(interval_data: list[dict],
                       schedule_config: dict) -> dict:
    """
    Heuristic EV-charging detector for hourly interval data.

    L2 charging shows up as multi-hour blocks of 3-11 kWh/h on top of the
    house baseload. Method: take the 25th percentile of all hourly imports
    as the baseload estimate (robust even when charging is frequent), then
    group consecutive hours that sit well above it into sessions and keep
    sessions with enough energy to be a vehicle rather than an appliance.
    """
    if not interval_data:
        return {"detected": False, "reason": "no data"}

    imports = sorted(iv["import_kwh"] for iv in interval_data)
    baseload = imports[len(imports) // 4]  # 25th percentile
    threshold = baseload + 1.5  # kWh/h above baseload to count as charging
    min_session_kwh = 6.0       # below this it's likely an oven/dryer

    sessions = []
    current = None
    prev_key = None
    for iv in interval_data:  # parser output is chronological
        key = (iv["date"], iv["hour"])
        is_charging = iv["import_kwh"] >= threshold
        contiguous = (
            prev_key is not None
            and (key[0] == prev_key[0] and key[1] == prev_key[1] + 1
                 or key[1] == 0 and prev_key[1] == 23)
        )
        if is_charging:
            excess = iv["import_kwh"] - baseload
            if current is not None and contiguous:
                current["kwh"] += excess
                current["hours"].append(iv)
            else:
                current = {"start_date": iv["date"], "start_hour": iv["hour"],
                           "kwh": excess, "hours": [iv]}
                sessions.append(current)
        else:
            current = None
        prev_key = key

    sessions = [s for s in sessions if s["kwh"] >= min_session_kwh]
    if not sessions:
        return {"detected": False,
                "baseload_kwh_per_hr": round(baseload, 2),
                "reason": "no multi-hour high-load blocks found"}

    total_kwh = sum(s["kwh"] for s in sessions)
    months = {iv["date"][:7] for iv in interval_data}

    # Energy by TOU period across all session hours
    period_kwh = defaultdict(float)
    for s in sessions:
        for iv in s["hours"]:
            period, _ = classify_tou_period(
                iv["hour"], iv["month"], iv["day_of_week"],
                schedule_config=schedule_config, date_str=iv["date"])
            period_kwh[period] += iv["import_kwh"] - baseload

    start_hours = [s["start_hour"] for s in sessions]
    typical_start = max(set(start_hours), key=start_hours.count)
    pct_off_peak = round(period_kwh.get("off_peak", 0.0) / total_kwh * 100, 1)

    recommendations = []
    risky_kwh = total_kwh - period_kwh.get("off_peak", 0.0)
    if risky_kwh > total_kwh * 0.05:
        recommendations.append(
            f"{risky_kwh:,.0f} kWh of charging landed outside off-peak hours. "
            "Schedule charging to start after the off-peak window opens "
            "(midnight on EV2-A/E-ELEC) to capture the cheapest rate.")
    else:
        recommendations.append(
            "Charging is already well-timed — nearly all of it lands in "
            "off-peak hours.")

    return {
        "detected": True,
        "baseload_kwh_per_hr": round(baseload, 2),
        "num_sessions": len(sessions),
        "estimated_ev_kwh": round(total_kwh, 1),
        "avg_kwh_per_month": round(total_kwh / len(months), 1),
        "avg_session_kwh": round(total_kwh / len(sessions), 1),
        "typical_start_hour": typical_start,
        "pct_in_off_peak": pct_off_peak,
        "kwh_by_period": {k: round(v, 1) for k, v in period_kwh.items()},
        "recommendations": recommendations,
        "note": ("Heuristic: sustained hourly imports >= baseload+1.5 kWh "
                 "grouped into sessions of >= 6 kWh. Large appliances can "
                 "masquerade as short sessions."),
    }
