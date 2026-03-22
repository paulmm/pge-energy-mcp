"""NEM 2.0 and NEM 3.0 export credit calculation."""


def calculate_export_credit(export_kwh: float, rate_per_kwh: float,
                            nem_version: str = "NEM2") -> float:
    """
    Calculate the credit earned for exported energy.

    NEM 2.0: Full retail rate credit (minus ~$0.03-0.04 NBC that can't be offset,
             but NBC is already excluded from our delivery rates, so we use full rate).
    NEM 3.0: Avoided cost (~$0.04-0.10/kWh, varies by hour/season).

    Args:
        export_kwh: Energy exported in the interval
        rate_per_kwh: Effective rate for this TOU period (for NEM2) or
                      avoided cost rate (for NEM3)
        nem_version: "NEM2" or "NEM3"

    Returns:
        Credit amount (positive = money saved)
    """
    if nem_version == "NEM2":
        # Full retail credit at the effective rate for the TOU period.
        # NBC (~$0.02-0.04/kWh) cannot be offset but is not included in our
        # delivery rate calculation, so this is already accounted for.
        return export_kwh * rate_per_kwh
    elif nem_version == "NEM3":
        # Simplified avoided cost — in practice this varies hourly via ACC.
        # Using a conservative flat estimate for now.
        NEM3_AVOIDED_COST = 0.08  # $/kWh average
        return export_kwh * NEM3_AVOIDED_COST
    else:
        raise ValueError(f"Unknown NEM version: {nem_version}")
