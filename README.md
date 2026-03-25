# pge-energy-mcp

MCP server for PG&E residential solar + battery energy analysis. Upload your Green Button data, compare rate plans, project your true-up bill, and optimize battery dispatch — all through Claude or a web interface.

**TL;DR:** Solar companies are incentivized to sell you the biggest system possible. This tool helps you understand your actual usage first, then right-size your system — balancing cost, production, and consumption instead of just "covering the bill."

## Why This Exists

Getting a solar and battery system right is a multi-variable optimization problem, and the incentives in the industry don't help. Solar companies are paid to sell you the biggest system possible. They ask for your electric bills, size a system to "cover" your usage, and move on. The result is often an oversized system with too much upfront cost that generates far more than you'll ever use — money left on the roof.

The smarter approach is to **understand your electricity usage first**: When do you actually consume power? How much is baseload vs. EV charging vs. seasonal heating? What's your peak exposure? Which rate plan fits your actual usage pattern? Only after answering those questions can you right-size a system that balances cost, production, and consumption.

PG&E billing makes this harder than it should be. Between CCA vs. bundled providers, 20+ PCIA vintage years, NEM 2.0 export credits, time-of-use periods that vary by schedule, base services charges by income tier, and rate changes twice a year — most customers have no idea what they're actually paying per kWh or whether their rate plan is costing them hundreds extra per year.

This server encodes all of that complexity so you can have a conversation with Claude about your actual energy data and get real answers: the right rate plan, the right system size, the right battery strategy.

## What It Does

- **Rate engine** — Encodes PG&E's billing complexity: 4 rate schedules (EV2-A, E-ELEC, E-TOU-C, E-TOU-D), CCA vs bundled providers, 20+ PCIA vintage years, NEM 2.0/3.0 export credits, time-of-use periods, base services charges by income tier, and historical rate changes over time.

- **Plan comparison** — Calculate annual cost across multiple rate plans using your actual hourly usage. Shows savings, TOU period breakdown, and seasonal analysis.

- **True-up projection** — Accumulate monthly NEM balances and project your annual true-up bill. See which months generate credits vs charges.

- **Battery optimizer** — Pyomo MILP solver finds the mathematically optimal charge/discharge schedule given your rates and usage.

- **Usage profiler** — Peak exposure, overnight baseload, seasonal patterns, worst import days.

- **System simulator** — Model solar panel additions, battery upgrades, and dispatch strategy changes. Errors cancel in sim-vs-sim comparison.

- **PG&E Share My Data** — OAuth integration to auto-fetch interval data from PG&E's Green Button Connect API.

- **Integrations** — Tesla FleetAPI (Powerwall status), Solcast (solar forecast).

## Getting Started

Upload your PG&E Green Button data and ask:

> **"Here is my PG&E data. Help me optimize my energy costs."**

That's it. The server parses your data, detects your solar system, then guides you through the analysis — asking about your battery, rate plan, and NEM version before running comparisons and recommendations.

### Example Prompts

- "Here is my Green Button data. What are the best ways to optimize my energy usage?"
- "My NEM 2 grandfathering expires next year. How much more will I pay?"
- "Would adding a second Powerwall save me money?"
- "What will my true-up bill be this year?"
- "When should I charge and discharge my Powerwall to minimize costs?"
- "Am I on the right rate plan?"

### Setup — Claude Desktop (Mac/Windows)

```bash
git clone https://github.com/paulmm/pge-energy-mcp.git
cd pge-energy-mcp
pip install -e .
```

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pge-energy": {
      "command": "/path/to/pge-energy-mcp/.venv/bin/python",
      "args": ["/path/to/pge-energy-mcp/server.py", "--stdio"]
    }
  }
}
```

Restart Claude Desktop. You'll see the MCP tools icon in the chat input.

### Setup — Remote (claude.ai / Streamable HTTP)

```bash
python server.py
# Runs on http://0.0.0.0:8000/mcp
```

### As a Web App

```bash
python server.py --web
# Open http://localhost:8001
```

Upload your CSV, compare plans, view usage profile, and project your true-up — all in the browser.

### Deploy to Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template)

The included `Procfile` runs the MCP server on Railway's assigned port. Set environment variables for optional integrations.

### How to Get Your Data

1. **Green Button (PG&E)** — Go to [pge.com](https://www.pge.com) > Account > Energy Usage > Green Button > "Export My Data" for hourly intervals, or "Export Bill Totals" for monthly summaries. Both formats are supported.
2. **Tesla** — Tesla app > Settings > Energy Data > Download My Data. Upload the CSV for battery/solar analysis.
3. **Share My Data API** — Connect directly via OAuth for automatic data fetching (see Environment Variables below).

## MCP Tools (21 total)

### Data Parsing
| Tool | Description |
|------|-------------|
| `parse_green_button` | Parse PG&E Green Button hourly interval CSV ("Export My Data") |
| `parse_billing_data` | Parse PG&E Green Button monthly bill totals ("Export Bill Totals") |
| `parse_tesla_export` | Parse Tesla app CSV with auto unit detection (MWh/kWh varies by year) |

### Rate Engine
| Tool | Description |
|------|-------------|
| `get_rates` | Look up effective $/kWh rates for any schedule + provider + vintage + tier |

### Analysis
| Tool | Description |
|------|-------------|
| `compare_plans` | Annual cost comparison across multiple rate plans |
| `usage_profile` | Peak exposure, baseload, seasonal patterns, worst days |
| `simulate_system` | Model solar/battery upgrades with sim-vs-sim error cancellation |
| `seasonal_strategy` | Optimization recommendations by season |
| `nem_projection` | NEM true-up bill projection with monthly breakdown |
| `compare_nem_versions` | NEM 2 vs NEM 3 transition impact analysis |
| `optimize_battery` | Pyomo MILP optimal battery dispatch schedule |

### System Config
| Tool | Description |
|------|-------------|
| `save_system_config` | Persist system config (arrays, batteries, rate plan) |
| `get_system_config` | Retrieve stored config |
| `update_system_config` | Partial update (change rate plan, add battery, etc.) |
| `list_system_configs` | List all stored configs |
| `delete_system_config` | Delete a stored config |

### Integrations
| Tool | Description |
|------|-------------|
| `powerwall_status` | Real-time Powerwall status via Tesla FleetAPI |
| `solar_forecast` | Solar production forecast via Solcast API |
| `connect_pge` | Start PG&E Share My Data OAuth flow |
| `complete_pge_connection` | Complete OAuth with authorization code |
| `fetch_pge_data` | Fetch interval data from PG&E API |

## Environment Variables

All optional — tools return helpful setup instructions when credentials are missing.

| Variable | Purpose |
|----------|---------|
| `DATA_DIR` | SQLite storage directory (default: `./data`) |
| `TESLA_FLEET_TOKEN` | Tesla FleetAPI access token |
| `SOLCAST_API_KEY` | Solcast API key (Hobbyist: 10 req/day) |
| `PGE_CLIENT_ID` | PG&E Share My Data OAuth client ID |
| `PGE_CLIENT_SECRET` | PG&E Share My Data OAuth client secret |
| `PGE_REDIRECT_URI` | OAuth callback URL |

## Supported Rate Schedules

| Schedule | Peak Window | Peak Days | Summer | Notes |
|----------|-------------|-----------|--------|-------|
| **EV2-A** | 4-9 PM | Every day | Jun-Sep | Requires EV, battery w/ PTO, or heat pump |
| **E-ELEC** | 4-9 PM | Every day | Jun-Sep | Electric home rate plan |
| **E-TOU-C** | 4-9 PM | Every day | Jun-Sep | Default residential TOU, baseline-tiered |
| **E-TOU-D** | 5-8 PM | Weekdays only | Jun-Sep | Shortest/narrowest peak window |

All rates verified against PG&E tariff sheets effective March 1, 2026.

### Providers
- **PGE_BUNDLED** — PG&E bundled service
- **PCE** — Peninsula Clean Energy (San Mateo County)
- Additional CCAs (SVCE, MCE, SJCE, EBCE) have placeholder configs

### How PG&E Billing Works

For CCA customers: `effective_rate = pge_delivery + cca_generation + pcia_vintage`

For bundled PG&E: `effective_rate = total_bundled_rate`

Example (EV2-A + PCE, 2016 vintage, winter off-peak):
`$0.13012 + $0.03936 + $0.03687 = $0.20635/kWh`

## Architecture

```
pge-energy-mcp/
├── server.py                     # FastMCP server + web app composition
├── config/
│   ├── pge_rates.json            # PG&E delivery & bundled rates
│   ├── cca_rates.json            # CCA provider generation rates
│   ├── pcia_vintages.json        # PCIA per-kWh by vintage year
│   └── rate_history.json         # Historical rate overrides (time-aware)
├── src/
│   ├── parsers/                  # Green Button (hourly + billing), Tesla CSV
│   ├── rates/                    # Rate engine, TOU classification, NEM credits
│   ├── analysis/                 # Compare, usage, simulator, strategy, true-up
│   ├── optimization/             # Pyomo MILP battery optimizer
│   ├── integrations/             # Tesla FleetAPI, Solcast, PG&E Share My Data
│   ├── storage/                  # SQLite config persistence
│   └── data/                     # SystemConfig dataclass
├── web/
│   ├── app.py                    # FastAPI application
│   ├── routes/                   # Upload, compare, profile, true-up
│   ├── templates/                # Jinja2 + htmx partials
│   └── static/                   # CSS
└── tests/                        # 234 tests
```

Domain logic lives in `src/` — MCP tools and web routes are thin wrappers. The same engine powers both interfaces.

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with verbose output
pytest -v

# Install CBC solver for battery optimizer
brew install cbc          # macOS
apt-get install coinor-cbc  # Linux
```

## Green Button Data Formats

PG&E offers two Green Button export formats — both are supported:

- **"Export My Data"** — Hourly intervals (~8,760 rows/year). Each row has date, hour, import kWh, export kWh, and cost. This is the most useful format for TOU optimization, battery scheduling, and system modeling.
- **"Export Bill Totals"** — Monthly billing summaries. Each row covers a billing period with total import, export, and cost. Useful for trend analysis, true-up detection, and year-over-year comparison.

The server auto-detects which format you uploaded and guides you through the analysis flow.

## License

MIT

---

Built by [Proprious Labs](https://proprious.com) with Claude Code.
