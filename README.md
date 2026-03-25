<p align="center">
  <h1 align="center">PG&E Energy Analyzer</h1>
  <p align="center">
    Upload your PG&E energy data. Have a conversation about it. Get real answers.
    <br />
    <em>The right rate plan. The right system size. The right battery strategy.</em>
  </p>
</p>

<p align="center">
  <a href="#how-it-works">How It Works</a> &nbsp;&bull;&nbsp;
  <a href="#get-your-pge-data">Get Your Data</a> &nbsp;&bull;&nbsp;
  <a href="#setup">Setup</a> &nbsp;&bull;&nbsp;
  <a href="#whats-inside">What's Inside</a>
</p>

---

> **"Here is my PG&E data. Help me optimize my energy costs."**

That's the whole prompt. Upload your data, ask a question, and the tool walks you through everything — detecting your solar system, asking about your battery, comparing rate plans, and finding where you're leaving money on the table.

Works with **Claude Desktop**, **claude.ai**, or as a **standalone web app**.

---

## Why This Exists

### :house_with_garden: The solar industry problem

Solar companies are paid to sell you the biggest system possible. They ask for your electric bills, size a system to "cover" your usage, and move on. The result is often an oversized system — too much upfront cost generating more than you'll ever use.

The smarter approach is to **understand your electricity usage first**: When do you actually consume power? How much is baseload vs. EV charging vs. seasonal heating? What's your peak exposure? Only then can you right-size a system that balances cost, production, and consumption.

### :zap: The PG&E billing problem

PG&E billing is extraordinarily complex, and most customers have no idea what they're actually paying per kWh. Your effective rate depends on:

| Factor | Why it matters |
|--------|---------------|
| **Rate schedule** | EV2-A, E-ELEC, E-TOU-C, E-TOU-D all have different peak windows and pricing |
| **Provider** | Bundled PG&E vs. Community Choice (PCE, SJCE, SVCE) have completely different rate structures |
| **PCIA vintage** | A per-kWh charge permanently set by the year you joined your CCA (20+ values) |
| **NEM version** | NEM 2.0 exports earn full retail credit; NEM 3.0 earns ~75% less |
| **Time-of-use** | The same kWh costs 2-3x more during peak (4-9 PM) than off-peak |
| **Income tier** | Base services charges range from $0.20 to $0.79/day |

Rates change roughly twice a year, and CCA rates change independently. **This tool has the actual tariff rates built in** — verified against PG&E rate sheets effective March 2026.

### :x: Why not just ask Claude?

Without this tool, AI assistants will:

| Problem | What happens |
|---------|-------------|
| **Hallucinate rates** | Doesn't know EV2-A winter off-peak is $0.20635 for a PCE 2016-vintage customer |
| **Confuse CCA vs. bundled** | PG&E bills show *delivery* rates, not total. The real formula: `delivery + CCA generation + PCIA` |
| **Guess NEM 3 values** | The Avoided Cost Calculator has 288 hourly values ($0.025-$0.28). AI guesses "$0.08" |
| **Miss TOU differences** | EV2-A peak is every day; E-TOU-D is weekdays only. Wrong windows = wrong math |
| **Break true-up math** | NEM credits accumulate monthly and settle annually. The true-up IS the bill |

This tool encodes all of that complexity so Claude gives you **accurate, personalized answers**.

---

## How It Works

```
Upload PG&E data
       |
       v
  Solar detected -----> No solar? Still analyzes usage
       |                 and finds best rate plan
       v
  "Do you have a battery?"
       |
       v
  "What rate plan are you on?"
       |
       v
  "NEM 2.0 or 3.0?"
       |
       v
  Full analysis: rate comparison, usage profile,
  true-up projection, optimization recommendations
```

### Example Prompts

| What you want to know | What to ask |
|---|---|
| Best overall optimization | *"Here is my Green Button data. What are the best ways to optimize my energy usage?"* |
| Rate plan check | *"Am I on the right rate plan?"* |
| NEM transition impact | *"My NEM 2 grandfathering expires next year. How much more will I pay?"* |
| True-up forecast | *"What will my true-up bill be this year?"* |
| Battery ROI | *"Would adding a second Powerwall save me money?"* |
| Battery scheduling | *"When should I charge and discharge my Powerwall to minimize costs?"* |

---

## Get Your PG&E Data

| Source | Where to find it | What it gives you |
|--------|-------------------|-------------------|
| **Green Button** (hourly) | [pge.com](https://www.pge.com) > Account > Energy Usage > Green Button > **"Export My Data"** | Hourly import/export for up to 13 months (~8,760 rows). Best for detailed analysis |
| **Green Button** (billing) | Same page > **"Export Bill Totals"** | Monthly summaries. Good for trends and year-over-year comparison |
| **Tesla** | Tesla app > Settings > Energy Data > **Download My Data** | Solar production, battery dispatch, home usage, grid flow |
| **Share My Data API** | [PG&E Share My Data](https://sharemydata.pge.com) | Automatic ongoing data access via OAuth |

Both Green Button formats are auto-detected — upload whichever you have.

---

## Setup

<details>
<summary><strong>Claude Desktop (Mac/Windows)</strong></summary>

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

</details>

<details>
<summary><strong>claude.ai / Remote (Streamable HTTP)</strong></summary>

```bash
python server.py
# Runs on http://0.0.0.0:8000/mcp
```

</details>

<details>
<summary><strong>Web App</strong></summary>

```bash
python server.py --web
# Open http://localhost:8001
```

</details>

<details>
<summary><strong>Deploy to Railway</strong></summary>

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template)

The included `Procfile` runs the MCP server on Railway's assigned port.

</details>

---

## What's Inside

### Analysis Tools

| Tool | What it does | Example prompt |
|------|-------------|----------------|
| **Rate plan comparison** | Annual cost across EV2-A, E-ELEC, E-TOU-C, E-TOU-D with TOU breakdown | *"Am I on the right rate plan?"* |
| **Usage profiling** | Peak exposure, overnight baseload, seasonal patterns, worst import days | *"Break down my energy usage"* |
| **True-up projection** | Monthly NEM balance accumulation and annual bill forecast | *"What will my true-up be?"* |
| **NEM 2 vs 3 comparison** | Transition impact, credit loss by TOU period, worst months | *"How much more will NEM 3 cost me?"* |
| **Battery optimization** | Mathematically optimal charge/discharge schedule (Pyomo MILP solver) | *"When should my Powerwall charge?"* |
| **System simulator** | Model adding panels, batteries, or changing dispatch strategy | *"Would more solar panels help?"* |
| **Seasonal strategy** | Season-specific recommendations for TOU shifting and battery use | *"How should I prepare for summer?"* |

### Rate Engine

Encodes the full complexity of PG&E billing so you don't have to read tariff PDFs:

- **4 rate schedules** with correct TOU windows and seasonal pricing
- **CCA vs. bundled** provider rates (PCE fully supported, others in progress)
- **20+ PCIA vintage years** ($0.030-$0.054/kWh charge, or -$0.010 credit for 2025+)
- **NEM 2.0** full retail export credits
- **NEM 3.0** hourly Avoided Cost Calculator (288 values across all hours and months)
- **Base services charges** by income tier ($0.20-$0.79/day)
- **Historical rate tracking** for time-aware analysis across rate change boundaries
- All rates verified against PG&E tariff sheets effective **March 1, 2026**

### Supported Rate Schedules

| Schedule | Peak Window | Peak Days | Summer | Best for |
|----------|-------------|-----------|--------|----------|
| **EV2-A** | 4-9 PM | Every day | Jun-Sep | EV owners, battery systems, heat pumps |
| **E-ELEC** | 4-9 PM | Every day | Jun-Sep | All-electric homes |
| **E-TOU-C** | 4-9 PM | Every day | Jun-Sep | Default residential TOU |
| **E-TOU-D** | 5-8 PM | Weekdays only | Jun-Sep | Shortest peak window, weekday-only |

### Providers

| Provider | Type | Coverage |
|----------|------|----------|
| **PGE_BUNDLED** | PG&E bundled service | Full rate support |
| **PCE** | Peninsula Clean Energy | Full rate support |
| SVCE, MCE, SJCE, EBCE | Other CCAs | Placeholder configs — rate data welcome |

---

<details>
<summary><strong>Development</strong></summary>

### Getting Started

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests (234 passing)
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
server.py                     # MCP tool definitions (21 tools)
src/parsers/                  # Green Button (hourly + billing), Tesla CSV
src/rates/                    # Rate engine, TOU classification, NEM credits
src/analysis/                 # Compare, usage, simulator, strategy, true-up, NEM transition
src/optimization/             # Pyomo MILP battery optimizer
src/integrations/             # Tesla FleetAPI, Solcast, PG&E Share My Data
src/storage/                  # SQLite config persistence
web/                          # FastAPI + Jinja2 + htmx web app
tests/                        # 234 tests
```

</details>

---

## License

MIT

<p align="center">
  Built by <a href="https://proprious.com">Proprious Labs</a> with Claude Code
</p>
