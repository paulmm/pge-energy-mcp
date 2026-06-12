"""Baseline allowance lookups for baseline-tiered schedules (E-TOU-C).

PG&E applies a flat per-kWh baseline credit to monthly net usage up to the
baseline allowance (daily allowance x days in billing period). The credit
rate lives in pge_rates.json per schedule; allowances live in baselines.json
per territory.
"""

from __future__ import annotations

import json
from pathlib import Path

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
_baselines = None


def _load() -> dict:
    global _baselines
    if _baselines is None:
        with open(_CONFIG_DIR / "baselines.json") as f:
            _baselines = json.load(f)
    return _baselines


def get_daily_allowance(territory: str, season: str,
                        all_electric: bool = False) -> float:
    """Daily baseline allowance in kWh for a territory and season."""
    territories = _load()["territories"]
    if territory not in territories:
        raise ValueError(
            f"Unknown baseline territory '{territory}'. Loaded territories: "
            f"{sorted(territories)}. Find yours on your PG&E bill "
            f"('Baseline Territory') and add it to config/baselines.json.")
    heat_key = "all_electric" if all_electric else "basic"
    return territories[territory][heat_key][season]
