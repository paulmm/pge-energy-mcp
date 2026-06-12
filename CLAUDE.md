# pge-energy-mcp

MCP server for PG&E solar + battery energy analysis. Built with FastMCP (Python), deployed on Railway.

## What This Project Does

Helps PG&E residential solar+battery customers answer: "Am I on the right rate plan and am I using my system optimally?" through Claude conversations. Users connect this MCP server in Claude Desktop/claude.ai, upload their Green Button CSV and Tesla exports, and get rate plan comparisons, usage analysis, system expansion modeling, and optimization recommendations.

## Key Design Principles

- **The rate engine is the product.** PG&E billing is extraordinarily complex: CCA vs bundled providers, 20+ PCIA vintage years, NEM 2.0 vs 3.0 export credits, base services charges by income tier, TOU periods varying by schedule. The server encodes all of this so users don't have to read tariff PDFs.
- **Stateless analysis, opt-in persistence.** CSV data comes in via tool parameters. System configs and PG&E OAuth tokens persist in SQLite at `$DATA_DIR/configs.db` (mount a volume in production). Live integrations (Powerwall, Solcast, PG&E Share My Data) activate via env credentials and degrade to setup instructions when unconfigured.
- **Auth:** `MCP_AUTH_TOKEN` enables bearer auth on the HTTP endpoint (`src/auth.py`). Never configure Tesla/PG&E credentials on a deployment without it.
- **Domain logic separate from MCP layer.** All analysis lives in `src/` as importable Python modules. MCP tools in `server.py` are thin wrappers. This allows reuse in a future web app.

## Architecture

```
pge-energy-mcp/
├── CLAUDE.md
├── server.py                  # FastMCP server — 22 tool definitions
├── pyproject.toml / requirements.txt
├── Procfile                   # Railway: python server.py (MCP only)
├── src/
│   ├── auth.py                # Optional bearer-token ASGI middleware
│   ├── parsers/               # green_button, billing, tesla, tesla_power
│   ├── rates/                 # engine (+RateCache), tou, nem (ACC), holidays, baseline
│   ├── analysis/              # compare, usage (+EV detection), simulator (+ROI),
│   │                          # strategy, trueup, nem_compare, bill_validation
│   ├── optimization/          # Pyomo battery optimizer (HiGHS/CBC/GLPK)
│   ├── integrations/          # powerwall, solcast, tesla fleet, pge_share_my_data, espi
│   ├── storage/               # SQLite config + OAuth token store
│   └── data/                  # system_config model
├── config/
│   ├── pge_rates.json         # PG&E rates + NBC + baseline credit
│   ├── cca_rates.json         # CCA generation rates (PCE loaded; others TBD)
│   ├── pcia_vintages.json     # PCIA per-kWh by vintage year
│   ├── baselines.json         # Baseline allowances by territory
│   └── rate_history.json      # Historical rate periods
├── web/                       # Local-only FastAPI UI (python server.py --web)
└── tests/
```

## How PG&E Billing Actually Works

A PG&E bill for a CCA solar customer has these components (from actual bills analyzed):

**PG&E Electric Monthly Charges** — just the Base Services Charge:
```
Base Services Charge    30 days @ $0.49281    $14.78
```
This is billed monthly regardless of NEM balance.

**NEM Charges** (accumulate monthly, settle at annual True-Up):
```
Net Usage
  Peak           172.73 kWh @ $0.34756      $60.03    <- PG&E delivery rate
  Part Peak      172.38 kWh @ $0.32547      $56.11
  Off Peak       711.53 kWh @ $0.31161     $221.72
NBC Net Usage Adjustment                    -$36.42    <- Avoids double-counting NBC
State Mandated Non-Bypassable Charge         $38.14    <- PPP + Nuclear Decom + Wildfire + CTC
Generation Credit                           -$68.74    <- Offsets PG&E gen (replaced by CCA)
Power Charge Indifference Adjustment         $38.96    <- PCIA vintage charge
Franchise Fee Surcharge                       $0.62
Monthly NEM Charges                         $310.42
```

**CCA Generation** (separate section on same bill):
```
Generation - On Peak - Winter    103.64 kWh @ $0.10592    $10.98
Generation - Part Peak - Winter  103.43 kWh @ $0.08795     $9.10
Generation - Off Peak - Winter   426.92 kWh @ $0.07593    $32.42
Energy Commission Surcharge                                 $0.32
Total PCE Charges                                          $61.74
```

**Key insight:** The rates on the PG&E NEM section are DELIVERY rates (total bundled minus generation), NOT the total effective rate. Total effective rate = PG&E delivery + CCA generation + PCIA. For bundled PG&E customers, the NEM rates ARE the total rate.

## Tariff Rates

### EV2-A (PUC Sheet 61169-E, March 1, 2026)

TOU windows: Off-peak midnight-3PM, partial-peak 3-4PM & 9PM-midnight, peak 4-9PM. Every day. Summer Jun-Sep.

Total Bundled ($/kWh):
- Summer: Peak $0.53809, Partial $0.42760, Off $0.22558
- Winter: Peak $0.41099, Partial $0.39428, Off $0.22558

Generation component (what CCA replaces):
- Summer: Peak $0.18830, Partial $0.14359, Off $0.10245
- Winter: Peak $0.13143, Partial $0.11894, Off $0.09546

Delivery (Bundled - Generation):
- Summer: Peak $0.34979, Partial $0.28401, Off $0.12313
- Winter: Peak $0.27956, Partial $0.27534, Off $0.13012

Base Services Charge: Tier 1 $0.19713/day, Tier 2 $0.39688/day, Tier 3 $0.79343/day
Requires: BEV/PHEV, battery w/ PTO, or heat pump. 800% baseline cap.

### E-ELEC (PUC Sheet 61116-E, March 1, 2026)

Same TOU windows as EV2-A.

Total Bundled:
- Summer: Peak $0.55214, Partial $0.39026, Off $0.33358
- Winter: Peak $0.32063, Partial $0.29854, Off $0.28468

Base Services Charge: Same tiers as EV2-A.
E-ELEC delivery rates from bills: Winter ~$0.348 peak, ~$0.326 partial, ~$0.312 off-peak (pre-March 2026). Post-March unbundled rates: verify from tariff sheet ELEC_SCHEDS_E-ELEC.pdf.

### E-TOU-D (March 1, 2026 — from PG&E residential rate plan pricing PDF)

Peak 5-8PM weekdays only (no weekend/holiday peak). Summer Jun-Sep (same as EV2-A).

Total Bundled ($/kWh):
- Summer: Peak $0.48, Off-Peak $0.34
- Winter: Peak $0.39, Off-Peak $0.35

### E-TOU-C (March 1, 2026 — from PG&E residential rate plan pricing PDF)

Peak 4-9PM daily. Two-period (no partial peak). Has baseline-tiered pricing.

Total Bundled ($/kWh, above baseline):
- Summer: Peak $0.52, Off-Peak $0.32
- Winter: Peak $0.40, Off-Peak $0.29

Below baseline:
- Summer: Peak $0.44
- Winter: Peak $0.32

### PCE Generation Rates (Feb 2026)

EV2:
- Summer: Peak $0.12291, Partial $0.08267, Off $0.04565
- Winter: Peak $0.07173, Partial $0.06049, Off $0.03936

E-ELEC (from bills):
- Winter pre-Feb 2026: Peak $0.10592, Partial $0.08795, Off $0.07593
- Winter post-Feb 2026: Peak $0.04422, Partial $0.02624, Off $0.01423
PCE dropped rates significantly Feb 2026. Summer E-ELEC rates TBD.

### PCIA Vintages (per kWh)
2009: $0.02973, 2010: $0.03366, 2011: $0.03492, 2012: $0.03676, 2013: $0.03708, 2014: $0.03686, 2015: $0.03680, 2016: $0.03687, 2017: $0.03661, 2018: $0.03679, 2019: $0.03725, 2020: $0.03632, 2021: $0.05264, 2022: $0.05272, 2023: $0.05380, 2024: $0.05066, 2025: -$0.01011, 2026: -$0.01011

Brisbane = PCE 2016 vintage = $0.03687/kWh.

### Effective Rate Formula

CCA customer: effective_rate = pge_delivery + cca_generation + pcia_vintage
Example EV2-A+PCE winter off-peak: $0.13012 + $0.03936 + $0.03687 = $0.20635/kWh
Example EV2-A+PCE summer peak: $0.34979 + $0.12291 + $0.03687 = $0.50957/kWh

Bundled PG&E: effective_rate = total_bundled_rate (no PCIA)

### NEM 2.0

- Exports earn full retail credit at applicable TOU rate
- Credits accumulate monthly, settle at annual true-up (no cash back)
- NBC (~$0.0345/kWh, config `pge_rates.json:nbc`) cannot be offset by
  export credits — modeled as NEM2 export credit = retail − NBC
- Switching rate plans mid-cycle triggers early true-up

### NEM 3.0 (reference)

- Export value ~$0.04-0.10/kWh via Avoided Cost Calculator
- Self-consumption worth 5-15x more than export
- NEM 2.0 grandfathered 20 years from PTO date

## Key Analytical Findings

From analysis of reference customer (Brisbane CA, ~7kW solar, 1 working PW2, 2 Teslas, E-ELEC+PCE 2016 vintage). Use to validate tool outputs.

1. **EV2-A saves ~$273/yr over E-ELEC.** 84.5% of imports in off-peak hours; $0.206 vs $0.31-0.33 off-peak drives it. EV2-A winter peak ($0.411) higher than E-ELEC ($0.321), claws back ~$132. Net savings from off-peak volume.

2. **2nd Powerwall only helps with TOU dispatch.** 1PW→2PW self-powered: -$34/yr (worse). 2PW+TOU-optimized: +$203/yr. At 95% self-consumption, no excess solar to fill bigger battery. TOU grid-charging overnight is the unlock. Peak imports drop 14.1%→4.4%.

3. **More solar is the biggest lever.** +3kW saves ~$1,200/yr (5-7yr payback). +5kW+fix PW+TOU: ~$2,100/yr (near net-zero). Winter production is the bottleneck. 3rd Powerwall adds only $21/yr beyond 2nd.

4. **Oversized panels on undersized micros work in winter.** 585W on 320W IQ7HS: 0% clip Nov-Feb, 4.6% annual. $585 total, 1.1yr payback, $535/yr value. IQ8H upgrade ($450 more) recovers $26/yr — 17yr payback.

5. **Winter dominates.** 40.1 kWh/day avg import (2.7x summer). Overnight baseload 1.73 kWh/hr. EV charging 100-250 kWh/month bursts.

6. **True-up IS the bill.** ~$2,000-2,100 in Dec-Jan cycle. Monthly charges $8-118. Other 11 months = interest-free borrowing.

## MCP Tools (22)

**Ingestion:** parse_green_button, parse_billing_data, parse_tesla_export,
parse_tesla_power, fetch_pge_data (Share My Data API)
**Rates:** get_rates, extract_bill_details
**Analysis:** compare_plans, usage_profile (incl. EV charging detection),
simulate_system (incl. payback/ROI), seasonal_strategy, nem_projection,
compare_nem_versions, validate_bill, optimize_battery, solar_forecast
**Config:** save/get/update/list/delete_system_config
**Powerwall (live, FleetAPI):** powerwall_live, powerwall_details,
set_powerwall_mode/reserve/grid_charging/grid_export
**PG&E OAuth:** connect_pge, complete_pge_connection

Remaining roadmap: per-user auth scoping, SVCE/MCE/SJCE/EBCE rates,
inland-zone ACC tables, web app productization.

## Green Button CSV Format

```
(BOM)
Name,JANE DOE
Address,"123 MAIN ST, BRISBANE CA 94005"
Account Number,1234567890
Service,Service 2
(blank)
TYPE,DATE,START TIME,END TIME,IMPORT (kWh),EXPORT (kWh),COST,NOTES
Electric usage,2025-03-20,00:00,00:59,2.94,0.00,$1.02
```
Skip to "TYPE,DATE" row. Strip $ and comma from COST/IMPORT/EXPORT. ~8,760 rows/year.

## Tesla CSV Format

Units vary by file and year — ALWAYS check column headers:
- 2025 yearly: Home(MWh), Vehicle(kWh), Powerwall(kWh), Solar(MWh), Grid(MWh)
- 2026 yearly: Home(MWh), Vehicle(kWh), Powerwall(kWh), Solar(kWh), Grid(MWh)
- Lifetime: Home(MWh), Vehicle(kWh), Powerwall(MWh), Solar(MWh), Grid(MWh)

## Reference System Config

```json
{
  "location": {"lat": 37.68, "lon": -122.40, "city": "Brisbane, CA"},
  "baseline_territory": "T",
  "heat_source": "electric",
  "rate_plan": "EV2-A",
  "provider": "PCE",
  "pcia_vintage": 2016,
  "income_tier": 3,
  "nem_version": "NEM2",
  "true_up_month": 1,
  "arrays": [
    {"name": "Array 1", "panels": 8, "panel_watts": 385, "make": "Longi", "inverter": "Enphase IQ7A", "inverter_watts_ac": 366, "type": "micro", "orientation": "south", "dc_watts": 3080, "ac_watts": 2928},
    {"name": "Array 2", "panels": 12, "panel_watts": 315, "make": "Qcells", "inverter": "4kW string", "inverter_watts_ac": 4000, "type": "string", "orientation": "south", "dc_watts": 3780, "ac_watts": 3780},
    {"name": "Array 3", "panels": 3, "panel_watts": 585, "make": "Znshine", "inverter": "SPWR-A5 (IQ7HS)", "inverter_watts_ac": 320, "type": "micro", "orientation": "south", "dc_watts": 1755, "ac_watts": 960, "notes": "DC/AC 1.83:1, 0% winter clip, 4.6% annual"}
  ],
  "batteries": [
    {"type": "Powerwall 2", "kwh": 13.5, "kw": 5.0, "efficiency": 0.90, "status": "working"},
    {"type": "Powerwall 2", "kwh": 13.5, "kw": 5.0, "efficiency": 0.90, "status": "needs_repair"}
  ],
  "vehicles": [{"make": "Tesla", "charger": "L2"}, {"make": "Tesla", "charger": "L2"}],
  "psh_by_month": {"Jan":3.2,"Feb":4.0,"Mar":5.0,"Apr":5.8,"May":6.3,"Jun":6.8,"Jul":6.5,"Aug":6.0,"Sep":5.5,"Oct":4.5,"Nov":3.5,"Dec":2.9}
}
```

## Tech Stack

FastMCP, pandas, numpy, uvicorn, Python 3.11+. Deploy on Railway (Streamable HTTP).

## Development Notes

- Rate changes ~2x/year. CCA rates change independently. Config JSONs need manual updates.
- March 2026 BSC restructure: ~$15→$24/month BSC, per-kWh dropped ~$0.05-0.07. Affects E-ELEC, EV2-A. E-TOU-C/D may lag.
- E-TOU-C rates TODO — fetch from tariff sheet.
- PCIA vintage is permanent (set when customer joined CCA).
- PCE dropped generation rates significantly Feb 2026 — watch for further changes.
- NBC per-kWh and baseline allowances live in config — update with tariff changes.
- `rates_meta` in every lookup carries last_updated/stale so Claude can warn users.
- E-TOU-C baseline credit: $0.08/kWh on monthly net usage up to territory allowance (config/baselines.json — territory T estimated, verify).
- E-TOU-D peak skips PG&E holidays (src/rates/holidays.py).

## Proprious Labs

Product alongside Calvin, Claude Code Gauges, Volatility Edge. Target: PG&E solar+battery customers. Distribute via MCP connector. Future web app shares src/ engine.
