"""User system configuration model for solar+battery systems."""

from dataclasses import dataclass, field, asdict


@dataclass
class SolarArray:
    name: str
    panels: int
    panel_watts: int
    make: str
    inverter: str
    inverter_watts_ac: int
    inverter_type: str  # "micro" or "string"
    orientation: str
    dc_watts: int = 0
    ac_watts: int = 0
    notes: str = ""

    def __post_init__(self):
        if not self.dc_watts:
            self.dc_watts = self.panels * self.panel_watts
        if not self.ac_watts:
            if self.inverter_type == "micro":
                self.ac_watts = self.panels * self.inverter_watts_ac
            else:
                self.ac_watts = self.inverter_watts_ac

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SolarArray":
        """Create a SolarArray from a dict, accepting both 'type' and 'inverter_type' keys."""
        d = dict(data)
        # Accept 'type' as alias for 'inverter_type' (matches reference config shape)
        if "type" in d and "inverter_type" not in d:
            d["inverter_type"] = d.pop("type")
        elif "type" in d:
            d.pop("type")
        # Filter to known fields
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        if "name" not in filtered:
            raise ValueError("SolarArray requires 'name'")
        if "panels" not in filtered:
            raise ValueError("SolarArray requires 'panels'")
        if "panel_watts" not in filtered:
            raise ValueError("SolarArray requires 'panel_watts'")
        return cls(**filtered)


@dataclass
class Battery:
    battery_type: str
    kwh: float
    kw: float
    efficiency: float = 0.90
    status: str = "working"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Battery":
        """Create a Battery from a dict, accepting 'type' as alias for 'battery_type'."""
        d = dict(data)
        if "type" in d and "battery_type" not in d:
            d["battery_type"] = d.pop("type")
        elif "type" in d:
            d.pop("type")
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        if "battery_type" not in filtered:
            raise ValueError("Battery requires 'battery_type' (or 'type')")
        if "kwh" not in filtered:
            raise ValueError("Battery requires 'kwh'")
        if "kw" not in filtered:
            raise ValueError("Battery requires 'kw'")
        return cls(**filtered)


@dataclass
class SystemConfig:
    location: dict = field(default_factory=dict)
    baseline_territory: str = "T"
    heat_source: str = "electric"
    rate_plan: str = "EV2-A"
    provider: str = "PCE"
    pcia_vintage: int = 2016
    income_tier: int = 3
    nem_version: str = "NEM2"
    true_up_month: int = 1
    arrays: list = field(default_factory=list)
    batteries: list = field(default_factory=list)
    vehicles: list = field(default_factory=list)
    psh_by_month: dict = field(default_factory=dict)

    @property
    def total_dc_watts(self) -> int:
        return sum(a.dc_watts for a in self.arrays if isinstance(a, SolarArray))

    @property
    def total_ac_watts(self) -> int:
        return sum(a.ac_watts for a in self.arrays if isinstance(a, SolarArray))

    @property
    def total_battery_kwh(self) -> float:
        return sum(b.kwh for b in self.batteries
                   if isinstance(b, Battery) and b.status == "working")

    def to_dict(self) -> dict:
        """Serialize to a plain dict suitable for JSON storage."""
        d = {
            "location": self.location,
            "baseline_territory": self.baseline_territory,
            "heat_source": self.heat_source,
            "rate_plan": self.rate_plan,
            "provider": self.provider,
            "pcia_vintage": self.pcia_vintage,
            "income_tier": self.income_tier,
            "nem_version": self.nem_version,
            "true_up_month": self.true_up_month,
            "arrays": [a.to_dict() if isinstance(a, SolarArray) else a for a in self.arrays],
            "batteries": [b.to_dict() if isinstance(b, Battery) else b for b in self.batteries],
            "vehicles": list(self.vehicles),
            "psh_by_month": dict(self.psh_by_month),
        }
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "SystemConfig":
        """Create a SystemConfig from a dict with validation.

        Accepts the reference config shape from CLAUDE.md (e.g., 'type' instead
        of 'inverter_type' in arrays, 'type' instead of 'battery_type' in batteries).
        """
        if not isinstance(data, dict):
            raise ValueError("SystemConfig.from_dict expects a dict")

        d = dict(data)

        # Parse arrays into SolarArray objects
        raw_arrays = d.pop("arrays", [])
        arrays = []
        for i, arr in enumerate(raw_arrays):
            if isinstance(arr, SolarArray):
                arrays.append(arr)
            elif isinstance(arr, dict):
                try:
                    arrays.append(SolarArray.from_dict(arr))
                except (ValueError, TypeError) as e:
                    raise ValueError(f"Invalid array at index {i}: {e}")
            else:
                raise ValueError(f"Invalid array at index {i}: expected dict or SolarArray")

        # Parse batteries into Battery objects
        raw_batteries = d.pop("batteries", [])
        batteries = []
        for i, bat in enumerate(raw_batteries):
            if isinstance(bat, Battery):
                batteries.append(bat)
            elif isinstance(bat, dict):
                try:
                    batteries.append(Battery.from_dict(bat))
                except (ValueError, TypeError) as e:
                    raise ValueError(f"Invalid battery at index {i}: {e}")
            else:
                raise ValueError(f"Invalid battery at index {i}: expected dict or Battery")

        # Validate key fields
        valid_plans = {"EV2-A", "E-ELEC", "E-TOU-C", "E-TOU-D"}
        rate_plan = d.get("rate_plan", "EV2-A")
        if rate_plan not in valid_plans:
            raise ValueError(f"Invalid rate_plan '{rate_plan}'. Must be one of {valid_plans}")

        nem = d.get("nem_version", "NEM2")
        if nem not in {"NEM2", "NEM3"}:
            raise ValueError(f"Invalid nem_version '{nem}'. Must be 'NEM2' or 'NEM3'")

        tier = d.get("income_tier", 3)
        if tier not in {1, 2, 3}:
            raise ValueError(f"Invalid income_tier {tier}. Must be 1, 2, or 3")

        # Filter to known scalar fields
        scalar_fields = {
            "location", "baseline_territory", "heat_source", "rate_plan",
            "provider", "pcia_vintage", "income_tier", "nem_version",
            "true_up_month", "vehicles", "psh_by_month",
        }
        kwargs = {k: v for k, v in d.items() if k in scalar_fields}
        kwargs["arrays"] = arrays
        kwargs["batteries"] = batteries

        return cls(**kwargs)
