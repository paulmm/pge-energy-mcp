# PG&E Energy Analyzer

Upload your PG&E energy data, have a conversation about it, and get real answers — the right rate plan, whether your solar system is sized right, and how to cut your true-up bill.

> **"Here is my PG&E data. Help me optimize my energy costs."**

Works with Claude Desktop, claude.ai, or as a standalone web app.

## Why This Exists

### The solar industry problem

Solar companies are paid to sell you the biggest system possible. They ask for your electric bills, size a system to "cover" your usage, and move on. The result is often an oversized system — too much upfront cost generating more than you'll ever use.

The smarter approach is to **understand your electricity usage first**: When do you actually consume power? How much is baseload vs. EV charging vs. seasonal heating? What's your peak exposure? Only then can you right-size a system that balances cost, production, and consumption.

### The PG&E billing problem

PG&E billing is extraordinarily complex, and most customers have no idea what they're actually paying per kWh. Your effective rate depends on:

- **Rate schedule** — EV2-A, E-ELEC, E-TOU-C, E-TOU-D all have different peak windows and pricing
- **Provider** — Bundled PG&E vs. Community Choice (PCE, SJCE, SVCE, etc.) have completely different rate structures
- **PCIA vintage** — A per-kWh charge permanently set by the year you joined your CCA (20+ different values)
- **NEM version** — NEM 2.0 exports earn full retail credit; NEM 3.0 earns ~75% less via hourly Avoided Cost Calculator values
- **Time-of-use** — The same kWh costs 2-3x more during peak hours (4-9 PM) than off-peak
- **Income tier** — Base services charges range from $0.20 to $0.79/day depending on tier

Rates change roughly twice a year, and CCA rates change independently. Asking ChatGPT or Claude about your PG&E rates will get you hallucinated numbers. **This tool has the actual tariff rates built in** — verified against PG&E rate sheets effective March 2026.

### Why not just ask Claude?

Without this tool, Claude will:
- **Hallucinate rates** — It doesn't know EV2-A winter off-peak is $0.20635 for a PCE 2016-vintage customer
- **Confuse CCA vs. bundled** — PG&E bills show *delivery* rates, not total rates. The formula is `delivery + CCA generation + PCIA vintage`
- **Guess at NEM 3 export values** — The real Avoided Cost Calculator has 288 hourly values (12 months × 24 hours) ranging from $0.025 to $0.28. Claude would guess "$0.08"
- **Miss TOU differences** — EV2-A peak is every day; E-TOU-D peak is weekdays only. Wrong windows = wrong analysis
- **Not understand true-up** — NEM credits accumulate monthly and settle once a year. The true-up IS the bill

This tool encodes all of that complexity so Claude can give you accurate, personalized answers.

## How It Works

Upload your PG&E data and the tool guides you through a complete analysis:

1. **Upload your data** — Green Button CSV from pge.com (hourly intervals or monthly bill totals)
2. **Solar detected** — If your data shows exports, the tool knows you have solar
3. **Battery check** — Asks if you have a Powerwall or other battery, and how to upload that data
4. **Rate plan & provider** — Asks your rate schedule and whether you're bundled PG&E or with a CCA
5. **NEM version** — Asks if you're on NEM 2.0 or 3.0 (based on when solar was installed)
6. **Analysis** — Runs rate comparisons, usage profiling, true-up projections, and optimization recommendations
7. **Deeper data** — If you uploaded bill totals, suggests downloading hourly data for TOU optimization and battery scheduling

### Example Prompts

- "Here is my Green Button data. What are the best ways to optimize my energy usage?"
- "Am I on the right rate plan?"
- "My NEM 2 grandfathering expires next year. How much more will I pay?"
- "What will my true-up bill be this year?"
- "Would adding a second Powerwall save me money?"
- "When should I charge and discharge my Powerwall to minimize costs?"

## Get Your PG&E Data

1. **Green Button** — Go to [pge.com](https://www.pge.com) > Account > Energy Usage > Green Button
   - **"Export My Data"** — Hourly intervals (~8,760 rows/year). Best for detailed analysis
   - **"Export Bill Totals"** — Monthly summaries. Good for trends and year-over-year comparison
   - Both formats are supported — the tool auto-detects which one you uploaded
2. **Tesla data** — Tesla app > Settings > Energy Data > Download My Data
3. **Automatic** — Connect via PG&E's Share My Data API for ongoing data access

## Setup

### Claude Desktop (Mac/Windows)

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

Restart Claude Desktop. You'll see the tools icon in the chat input.

### claude.ai / Remote

```bash
python server.py
# Runs on http://0.0.0.0:8000/mcp
```

### Web App

```bash
python server.py --web
# Open http://localhost:8001
```

### Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template)

## What's Inside

### Analysis Tools

| What it does | How to ask |
|---|---|
| **Rate plan comparison** — Annual cost across EV2-A, E-ELEC, E-TOU-C, E-TOU-D | "Am I on the right rate plan?" |
| **Usage profiling** — Peak exposure, baseload, seasonal patterns, worst days | "Break down my energy usage" |
| **True-up projection** — Monthly NEM balance accumulation and annual bill | "What will my true-up be?" |
| **NEM 2 vs 3 comparison** — Transition impact, credit loss, worst months | "How much more will NEM 3 cost me?" |
| **Battery optimization** — Mathematically optimal charge/discharge schedule | "When should my Powerwall charge?" |
| **System simulator** — Model adding panels, batteries, or changing dispatch | "Would more solar panels help?" |
| **Seasonal strategy** — Season-specific optimization recommendations | "How should I prepare for summer?" |

### Rate Engine

Encodes the full complexity of PG&E billing:

- 4 rate schedules with correct TOU windows and seasonal pricing
- CCA vs. bundled provider rates (PCE fully supported, others in progress)
- 20+ PCIA vintage years ($0.030-$0.054/kWh charge, or -$0.010 credit for 2025+)
- NEM 2.0 full retail credits and NEM 3.0 hourly Avoided Cost Calculator (288 values)
- Base services charges by income tier ($0.20-$0.79/day)
- Historical rate tracking for time-aware analysis
- All rates verified against PG&E tariff sheets effective March 1, 2026

### Data Parsing

Handles PG&E's Green Button CSV (hourly intervals and monthly bill totals), Tesla energy exports (with automatic unit detection — Tesla mixes MWh and kWh across different export types), and PG&E Share My Data API (ESPI/XML format).

### Supported Rate Schedules

| Schedule | Peak Window | Peak Days | Summer | Notes |
|----------|-------------|-----------|--------|-------|
| **EV2-A** | 4-9 PM | Every day | Jun-Sep | Requires EV, battery w/ PTO, or heat pump |
| **E-ELEC** | 4-9 PM | Every day | Jun-Sep | Electric home rate plan |
| **E-TOU-C** | 4-9 PM | Every day | Jun-Sep | Default residential TOU, baseline-tiered |
| **E-TOU-D** | 5-8 PM | Weekdays only | Jun-Sep | Shortest/narrowest peak window |

### Providers

- **PGE_BUNDLED** — PG&E bundled service (effective_rate = total bundled rate)
- **PCE** — Peninsula Clean Energy (effective_rate = PG&E delivery + PCE generation + PCIA vintage)
- Additional CCAs (SVCE, MCE, SJCE, EBCE) — placeholder configs ready for rate data

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests (234 tests)
pytest

# Install CBC solver for battery optimizer
brew install cbc          # macOS
apt-get install coinor-cbc  # Linux
```

### Environment Variables

All optional — tools return helpful setup instructions when credentials are missing.

| Variable | Purpose |
|----------|---------|
| `DATA_DIR` | SQLite storage directory (default: `./data`) |
| `TESLA_FLEET_TOKEN` | Tesla FleetAPI access token |
| `SOLCAST_API_KEY` | Solcast API key |
| `PGE_CLIENT_ID` | PG&E Share My Data OAuth client ID |
| `PGE_CLIENT_SECRET` | PG&E Share My Data OAuth client secret |

### Architecture

Domain logic lives in `src/` — the MCP server and web app are thin wrappers over the same engine.

```
server.py                     # MCP tool definitions
src/parsers/                  # Green Button (hourly + billing), Tesla CSV
src/rates/                    # Rate engine, TOU classification, NEM credits
src/analysis/                 # Compare, usage, simulator, strategy, true-up, NEM transition
src/optimization/             # Pyomo MILP battery optimizer
src/integrations/             # Tesla FleetAPI, Solcast, PG&E Share My Data
src/storage/                  # SQLite config persistence
web/                          # FastAPI + Jinja2 + htmx web app
tests/                        # 234 tests
```

## License

MIT

---

Built by [Proprious Labs](https://proprious.com) with Claude Code.
