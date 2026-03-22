"""User system configuration model for solar+battery systems."""

from dataclasses import dataclass, field


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


@dataclass
class Battery:
    battery_type: str
    kwh: float
    kw: float
    efficiency: float = 0.90
    status: str = "working"


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
