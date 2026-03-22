"""TOU period and season classification for PG&E rate schedules."""

from __future__ import annotations

import json
from pathlib import Path
from datetime import date

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
_pge_rates = None


def _load_pge_rates():
    global _pge_rates
    if _pge_rates is None:
        with open(_CONFIG_DIR / "pge_rates.json") as f:
            _pge_rates = json.load(f)
    return _pge_rates


def get_schedule_config(schedule: str) -> dict:
    """Return the schedule config dict from pge_rates.json."""
    rates = _load_pge_rates()
    if schedule not in rates["schedules"]:
        raise ValueError(f"Unknown schedule: {schedule}. Available: {list(rates['schedules'].keys())}")
    return rates["schedules"][schedule]


def classify_season(month: int, summer_months: list[int]) -> str:
    """Return 'summer' or 'winter' based on month and schedule's summer months."""
    return "summer" if month in summer_months else "winter"


def classify_tou_period(hour: int, month: int, day_of_week: int,
                        schedule: str | None = None,
                        schedule_config: dict | None = None) -> tuple[str, str]:
    """
    Classify an hour into (tou_period, season).

    Args:
        hour: 0-23
        month: 1-12
        day_of_week: 0=Monday, 6=Sunday (ISO convention)
        schedule: Schedule name (e.g. "EV2-A"). Used if schedule_config not provided.
        schedule_config: Pre-loaded schedule config dict.

    Returns:
        (period, season) — e.g. ("peak", "winter"), ("off_peak", "summer")
    """
    if schedule_config is None:
        schedule_config = get_schedule_config(schedule)

    summer_months = schedule_config["summer_months"]
    season = classify_season(month, summer_months)
    tou_windows = schedule_config["tou_windows"]

    # Check periods in priority order: peak first, then partial_peak, then off_peak
    for period in ["peak", "partial_peak", "off_peak"]:
        if period not in tou_windows:
            continue
        window = tou_windows[period]
        # E-TOU-D has weekdays_only peak
        if window.get("weekdays_only", False) and day_of_week >= 5:
            continue
        if hour in window["hours"]:
            return period, season

    # Fallback: if no period matched (e.g. weekend hour for weekday-only peak),
    # it's off-peak
    return "off_peak", season
