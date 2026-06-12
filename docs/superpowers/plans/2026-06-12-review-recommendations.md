# Review Recommendations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all P0–P3 recommendations from the 2026-06-12 code review: bearer auth for the public MCP endpoint, rate-engine correctness (NBC, baseline tiers, holidays, ACC zones, graceful CCA errors, input hardening), new features (bill validation, rate freshness, ROI, EV detection), and housekeeping (rate-cache dedup, history ordering, pip-installable solver, CLAUDE.md refresh).

**Architecture:** All domain logic stays in `src/` modules; `server.py` tools remain thin wrappers. Auth is a pure-ASGI middleware so it covers the FastMCP streamable-http app regardless of transport internals. NBC is modeled as a reduction on NEM2 export credit (`credit = export × (rate − nbc)`), which is algebraically identical to the bill's "NBC on gross imports + net-usage adjustment" structure. Baseline tiers are modeled as PG&E does: a flat per-kWh baseline credit on monthly net usage up to the allowance.

**Tech Stack:** Python 3.10+, FastMCP, pytest, Pyomo (+ new highspy solver), SQLite.

**Test command:** `python -m pytest tests/ -q` from repo root. Full suite currently passes (~234 tests). Run it before starting to confirm a green baseline.

**Commit convention:** one commit per task, message style matches repo history (imperative, no prefix), each ending with the Claude co-author trailer.

---

### Task 1: Bearer auth middleware (P0)

**Files:**
- Create: `src/auth.py`
- Modify: `server.py:937-955` (ASGI app + `__main__` default branch)
- Test: `tests/test_auth.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_auth.py
"""Tests for the optional bearer-token auth middleware."""

from starlette.testclient import TestClient

from src.auth import BearerAuthMiddleware


async def ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


def _client():
    return TestClient(BearerAuthMiddleware(ok_app))


class TestOpenMode:
    def test_passthrough_when_no_token_configured(self, monkeypatch):
        monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
        assert _client().get("/mcp").status_code == 200


class TestAuthEnforced:
    def test_rejects_missing_header(self, monkeypatch):
        monkeypatch.setenv("MCP_AUTH_TOKEN", "sekret")
        resp = _client().get("/mcp")
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == "Bearer"

    def test_rejects_wrong_token(self, monkeypatch):
        monkeypatch.setenv("MCP_AUTH_TOKEN", "sekret")
        resp = _client().get("/mcp", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_accepts_correct_token(self, monkeypatch):
        monkeypatch.setenv("MCP_AUTH_TOKEN", "sekret")
        resp = _client().get("/mcp", headers={"Authorization": "Bearer sekret"})
        assert resp.status_code == 200

    def test_icon_paths_exempt(self, monkeypatch):
        monkeypatch.setenv("MCP_AUTH_TOKEN", "sekret")
        assert _client().get("/favicon.ico").status_code == 200
        assert _client().get("/icon.svg").status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_auth.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.auth'`

- [ ] **Step 3: Write the middleware**

```python
# src/auth.py
"""Optional bearer-token authentication for the MCP HTTP transport.

When the MCP_AUTH_TOKEN environment variable is set, every HTTP request
must carry "Authorization: Bearer <token>". When unset, all requests pass
through unchanged (open mode — suitable only for deployments that hold no
credentials or stored user data).

Implemented as pure ASGI so it wraps FastMCP's streamable-http app without
depending on FastMCP internals, and passes lifespan/websocket scopes through.
"""

from __future__ import annotations

import hmac
import os

_EXEMPT_PATHS = {"/favicon.ico", "/icon.svg"}

_UNAUTHORIZED_BODY = (
    b'{"error": "unauthorized", '
    b'"message": "This server requires Authorization: Bearer <token>. '
    b'Ask the operator for the MCP_AUTH_TOKEN value."}'
)


class BearerAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Read per-request so the operator can rotate the token without
        # code changes and tests can monkeypatch the environment.
        token = os.environ.get("MCP_AUTH_TOKEN", "")
        if not token or scope.get("path") in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        provided = ""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                provided = value.decode("latin-1")
                break

        expected = f"Bearer {token}"
        if hmac.compare_digest(provided.encode(), expected.encode()):
            await self.app(scope, receive, send)
            return

        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_auth.py -q`
Expected: 5 passed

- [ ] **Step 5: Wire into server.py**

In `server.py`, add to the imports near the top (after `from src.data.system_config import SystemConfig`):

```python
from src.auth import BearerAuthMiddleware
```

Replace the bottom of the file (the `app = mcp.http_app()` line and the `__main__` block):

```python
# ASGI app for deployment (Railway, uvicorn, etc.)
# Wrapped in optional bearer auth — set MCP_AUTH_TOKEN to enforce.
app = BearerAuthMiddleware(mcp.http_app())


if __name__ == "__main__":
    import sys

    if "--web" in sys.argv:
        import uvicorn
        from web.app import create_web_app

        web = create_web_app()
        uvicorn.run(web, host="0.0.0.0", port=8001)
    elif "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        import os
        import uvicorn
        port = int(os.environ.get("PORT", 8000))
        if not os.environ.get("MCP_AUTH_TOKEN"):
            print("WARNING: MCP_AUTH_TOKEN not set — the HTTP endpoint is "
                  "unauthenticated. Do not configure Tesla/PG&E credentials "
                  "on an open deployment.")
        uvicorn.run(app, host="0.0.0.0", port=port)
```

Note: the default branch switches from `mcp.run(transport="streamable-http")` to `uvicorn.run(app, ...)` so the Procfile path goes through the auth wrapper. `mcp.http_app()`'s lifespan still runs because uvicorn drives the lifespan protocol and the middleware forwards non-http scopes.

- [ ] **Step 6: Verify the server still boots and serves MCP**

Run: `MCP_AUTH_TOKEN=test timeout 5 python server.py & sleep 2 && curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/mcp && curl -s -o /dev/null -w " %{http_code}" -H "Authorization: Bearer test" -H "Accept: application/json, text/event-stream" -X POST http://localhost:8000/mcp; wait`
Expected: first code `401`, second code NOT `401` (will be 400/406-family — wrong MCP handshake body is fine, the point is auth passed). Also `python -m pytest tests/ -q` still green.

- [ ] **Step 7: Commit**

```bash
git add src/auth.py server.py tests/test_auth.py
git commit -m "Add optional bearer-token auth for the MCP HTTP endpoint"
```

---

### Task 2: Security & deployment documentation (P0)

**Files:**
- Modify: `README.md` (Environment Variables section at line ~298, add Security section before it)

- [ ] **Step 1: Add a Security section to README.md**

Insert immediately before the `### Environment Variables` heading:

```markdown
### Security

The MCP endpoint supports optional bearer-token auth. Set `MCP_AUTH_TOKEN`
to any long random string (e.g. `openssl rand -hex 32`) and every HTTP
request must send `Authorization: Bearer <token>`. When unset, the endpoint
is open.

**Rules of thumb:**

- **Never set `TESLA_FLEET_TOKEN`, `PGE_CLIENT_ID`/`PGE_CLIENT_SECRET`, or
  `SOLCAST_API_KEY` on a deployment without `MCP_AUTH_TOKEN`.** Those
  credentials are server-global — on an open endpoint, anyone who connects
  can control your Powerwall and read your usage data.
- claude.ai custom connectors cannot send custom headers, so an
  authenticated deployment works with Claude Code / Claude Desktop
  (`claude mcp add --transport http pge-energy <url> --header
  "Authorization: Bearer <token>"`) but not claude.ai. For claude.ai, run
  a separate open deployment with **no credentials configured** — the
  analysis tools (parsers, rates, comparisons) are safe without auth
  because they hold no secrets.
- For live Powerwall control from Claude Desktop, prefer running locally
  with `python server.py --stdio` and credentials in your local env.
- Stored configs and OAuth tokens live in `$DATA_DIR/configs.db`
  (default `./data`). On Railway the filesystem is **ephemeral** — mount a
  volume and point `DATA_DIR` at it or stored configs vanish on redeploy.
  Tokens are stored unencrypted; treat the volume as sensitive.
```

- [ ] **Step 2: Add the new env vars to the Environment Variables table**

Add rows to the existing table (which currently lists `TESLA_FLEET_TOKEN`, `SOLCAST_API_KEY`, `PGE_CLIENT_ID`, `PGE_CLIENT_SECRET`):

```markdown
| `MCP_AUTH_TOKEN` | Optional bearer token for the HTTP endpoint. Unset = open access. |
| `DATA_DIR` | Directory for the SQLite config/token store (default `./data`). Mount a volume in production. |
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document auth, credential safety, and persistent storage for deployments"
```

---

### Task 3: RateCache — dedupe the per-date rate lookup pattern (P3, done early so later tasks build on it)

**Files:**
- Modify: `src/rates/engine.py` (add `RateCache` class at end of file)
- Modify: `src/analysis/compare.py:63-99,165-172` 
- Modify: `src/analysis/trueup.py:45-78`
- Modify: `src/analysis/nem_compare.py:39-88`
- Test: `tests/test_rates.py` (add class)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rates.py`:

```python
# ── RateCache ─────────────────────────────────────────────────────────


class TestRateCache:
    def test_for_date_matches_direct_lookup(self):
        from src.rates.engine import RateCache
        rc = RateCache("EV2-A", "PCE", 2016, 3)
        direct = lookup_rates("EV2-A", "PCE", 2016, 3, date="2026-01-15")
        assert rc.for_date("2026-01-15") == direct

    def test_time_aware_false_returns_base(self):
        from src.rates.engine import RateCache
        rc = RateCache("EV2-A", "PCE", 2016, 3, time_aware=False)
        assert rc.for_date("2025-06-01") == rc.base

    def test_schedule_config_shape(self):
        from src.rates.engine import RateCache
        rc = RateCache("EV2-A", "PCE", 2016, 3)
        assert set(rc.schedule_config) == {"tou_windows", "summer_months"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_rates.py -q -k RateCache`
Expected: FAIL with `ImportError: cannot import name 'RateCache'`

- [ ] **Step 3: Implement RateCache in engine.py**

Append to `src/rates/engine.py`:

```python
class RateCache:
    """Date-aware rate lookups for one plan, cached by date.

    Replaces the cache-by-date pattern previously duplicated across
    compare.py, trueup.py, and nem_compare.py.
    """

    def __init__(self, schedule: str, provider: str = "PGE_BUNDLED",
                 vintage_year: int = 2016, income_tier: int = 3,
                 time_aware: bool = True):
        self.schedule = schedule
        self.provider = provider
        self.vintage_year = vintage_year
        self.income_tier = income_tier
        self.time_aware = time_aware
        self.base = lookup_rates(schedule, provider, vintage_year, income_tier)
        self._by_date: dict[str, dict] = {}

    @classmethod
    def from_plan(cls, plan: dict, time_aware: bool = True) -> "RateCache":
        return cls(plan["schedule"], plan.get("provider", "PGE_BUNDLED"),
                   plan.get("vintage_year", 2016), plan.get("income_tier", 3),
                   time_aware=time_aware)

    @property
    def schedule_config(self) -> dict:
        return {"tou_windows": self.base["tou_windows"],
                "summer_months": self.base["summer_months"]}

    def for_date(self, date_str: str) -> dict:
        if not self.time_aware:
            return self.base
        if date_str not in self._by_date:
            self._by_date[date_str] = lookup_rates(
                self.schedule, self.provider, self.vintage_year,
                self.income_tier, date=date_str)
        return self._by_date[date_str]

    @property
    def used_history(self) -> bool:
        return self.time_aware and bool(self._by_date)
```

- [ ] **Step 4: Refactor the three analysis modules to use it**

`src/analysis/compare.py` — in `_calculate_annual_cost`, replace lines 63-71 (base lookup, schedule_config, rate_cache) with:

```python
    from src.rates.engine import RateCache
    rc = RateCache.from_plan(plan, time_aware=time_aware)
    base_rate_info = rc.base
    schedule_config = rc.schedule_config
```

Replace the per-interval lookup (lines 95-99) with:

```python
        rate_info = rc.for_date(dt)
```

(`rc.for_date` already returns `base` when `time_aware=False`.) Update the `rate_info_summary` condition `if time_aware and rate_cache:` → `if rc.used_history:`. Delete `_get_cached_rates` (lines 165-172).

`src/analysis/trueup.py` — replace lines 46-53 with:

```python
    from src.rates.engine import RateCache
    rc = RateCache.from_plan(plan, time_aware=time_aware)
    base_rates = rc.base
    schedule_config = rc.schedule_config
```

and the per-interval block (lines 72-78) with:

```python
        rate_info = rc.for_date(dt)
```

`src/analysis/nem_compare.py` — replace lines 39-45 with:

```python
    from src.rates.engine import RateCache
    rc = RateCache.from_plan(plan, time_aware=time_aware)
    base_rate_info = rc.base
    schedule_config = rc.schedule_config
```

and the per-interval block (lines 82-88) with:

```python
        rate_info = rc.for_date(dt)
```

In all three files the `schedule/provider/vintage_year/income_tier` local unpacking at the top of the function can stay (trueup/nem_compare still reference them) or be removed if now unused — remove only if unused.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass (pure refactor — any failure means the refactor changed behavior; fix before proceeding)

- [ ] **Step 6: Commit**

```bash
git add src/rates/engine.py src/analysis/compare.py src/analysis/trueup.py src/analysis/nem_compare.py tests/test_rates.py
git commit -m "Extract RateCache to dedupe per-date rate lookups across analysis modules"
```

---

### Task 4: Rate-history period ordering (P3)

**Files:**
- Modify: `src/rates/engine.py:162` (`_apply_history`)
- Test: `tests/test_rates.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rates.py`:

```python
# ── Rate history ordering ─────────────────────────────────────────────


class TestRateHistoryOrdering:
    def test_tightest_cutoff_wins_when_periods_overlap(self):
        """For a date matched by two periods overriding the same field, the
        period with the smallest applies_before (oldest era) must win."""
        import src.rates.engine as engine
        saved = engine._cache.get("rate_history.json")
        engine._cache["rate_history.json"] = {
            "periods": [
                {  # listed FIRST but covers the later era
                    "applies_before": "2026-03-01",
                    "pge_delivery_overrides": {
                        "EV2-A": {"winter": {"off_peak": 0.99}},
                    },
                },
                {  # smaller cutoff — must win for dates before 2026-01-01
                    "applies_before": "2026-01-01",
                    "pge_delivery_overrides": {
                        "EV2-A": {"winter": {"off_peak": 0.11}},
                    },
                },
            ]
        }
        try:
            r = lookup_rates("EV2-A", "PGE_BUNDLED", income_tier=3,
                             date="2025-12-15")
            assert r["components"]["delivery"]["winter"]["off_peak"] == 0.11
        finally:
            if saved is not None:
                engine._cache["rate_history.json"] = saved
            else:
                engine._cache.pop("rate_history.json", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_rates.py -q -k Ordering`
Expected: FAIL — delivery comes back `0.99` (last-listed period wins today)

- [ ] **Step 3: Sort periods before applying**

In `src/rates/engine.py` `_apply_history`, replace:

```python
    for period in history.get("periods", []):
```

with:

```python
    # Apply periods in descending cutoff order so that when several periods
    # match a date, the one with the smallest applies_before (the oldest
    # rate era, closest to the date) is applied last and wins.
    periods = sorted(history.get("periods", []),
                     key=lambda p: p.get("applies_before", ""), reverse=True)
    for period in periods:
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/rates/engine.py tests/test_rates.py
git commit -m "Apply rate-history periods tightest-cutoff-last so overlapping overrides resolve correctly"
```

---

### Task 5: Model NBC in NEM 2.0 export credits (P1 — highest-value correctness fix)

Bill algebra: PG&E delivery rates embed NBC; the bill charges NBC on gross imports and backs out the net-usage portion. Net effect: `cost = imports×rate − exports×(rate − nbc)`. So NEM2 export credit = retail minus NBC.

**Files:**
- Modify: `config/pge_rates.json` (`_meta` + new top-level `nbc` block)
- Modify: `src/rates/engine.py` (return `nbc_per_kwh` in both bundled and CCA branches)
- Modify: `src/rates/nem.py:15-43` (`calculate_export_credit` signature)
- Modify call sites: `src/analysis/compare.py:112`, `src/analysis/trueup.py:86`, `src/analysis/nem_compare.py:103`, `src/analysis/simulator.py:229,393` (+ thread `nbc_per_kwh` into `_simulate_system`/`_compute_gb_cost` params), `src/optimization/battery_optimizer.py:147-148`
- Test: `tests/test_rates.py`, plus expectation updates in `tests/test_compare.py` / `tests/test_trueup.py` / `tests/test_nem_compare.py` / `tests/test_simulator.py` if they assert absolute credits

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rates.py`:

```python
# ── NBC (non-bypassable charges) ──────────────────────────────────────


class TestNBC:
    def test_lookup_exposes_nbc(self):
        r = lookup_rates("EV2-A", "PCE", 2016, 3)
        assert r["nbc_per_kwh"] == pytest.approx(0.0345, abs=1e-4)
        r2 = lookup_rates("EV2-A", "PGE_BUNDLED", income_tier=3)
        assert r2["nbc_per_kwh"] == pytest.approx(0.0345, abs=1e-4)

    def test_nem2_credit_excludes_nbc(self):
        from src.rates.nem import calculate_export_credit
        credit = calculate_export_credit(10.0, 0.30, "NEM2", nbc_per_kwh=0.0345)
        assert credit == pytest.approx(10.0 * (0.30 - 0.0345))

    def test_nem2_credit_never_negative(self):
        from src.rates.nem import calculate_export_credit
        assert calculate_export_credit(10.0, 0.02, "NEM2", nbc_per_kwh=0.0345) == 0.0

    def test_nem2_default_nbc_zero_backcompat(self):
        from src.rates.nem import calculate_export_credit
        assert calculate_export_credit(10.0, 0.30, "NEM2") == pytest.approx(3.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rates.py -q -k NBC`
Expected: FAIL — `KeyError: 'nbc_per_kwh'` and TypeError on the new kwarg

- [ ] **Step 3: Add NBC to config**

In `config/pge_rates.json`, add after the `"_meta"` object (top level, sibling of `"schedules"`):

```json
  "nbc": {
    "per_kwh": 0.0345,
    "_notes": "Non-bypassable charges (PPP + Nuclear Decommissioning + Wildfire Fund + CTC). Cannot be offset by NEM export credits. Derived from reference bill: NBC Net Usage Adjustment -$36.42 / 1056.64 kWh net = $0.0345/kWh. Verify against current PUC sheets ~2x/year."
  },
```

- [ ] **Step 4: Return it from lookup_rates**

In `src/rates/engine.py` `lookup_rates`, after `pcia_data = _load_json("pcia_vintages.json")` add:

```python
    nbc_per_kwh = pge_rates.get("nbc", {}).get("per_kwh", 0.0)
```

Add `"nbc_per_kwh": nbc_per_kwh,` to BOTH return dicts (bundled branch ~line 93 and CCA branch ~line 125), next to `"base_services_charge_daily"`.

- [ ] **Step 5: Update calculate_export_credit**

In `src/rates/nem.py`, replace the function signature and NEM2 branch:

```python
def calculate_export_credit(export_kwh: float, rate_per_kwh: float,
                            nem_version: str = "NEM2",
                            hour: int = None, month: int = None,
                            nbc_per_kwh: float = 0.0) -> float:
    """
    Calculate the credit earned for exported energy.

    NEM 2.0: Retail-rate credit minus non-bypassable charges. NBCs (PPP,
    nuclear decommissioning, wildfire fund, CTC — ~$0.03-0.04/kWh) cannot
    be offset by exports, so each exported kWh earns (retail − NBC).
    NEM 3.0: Avoided Cost Calculator value by hour and month (NBC does not
    apply to export valuation under the net billing tariff).

    Args:
        export_kwh: Energy exported in the interval
        rate_per_kwh: Effective rate for this TOU period (used for NEM2)
        nem_version: "NEM2" or "NEM3"
        hour: Hour of day 0-23 (required for NEM3 ACC lookup)
        month: Month 1-12 (required for NEM3 ACC lookup)
        nbc_per_kwh: Non-bypassable charge $/kWh from lookup_rates()

    Returns:
        Credit amount (positive = money saved)
    """
    if nem_version == "NEM2":
        return export_kwh * max(0.0, rate_per_kwh - nbc_per_kwh)
    elif nem_version == "NEM3":
        acc_rate = get_acc_rate(hour, month)
        return export_kwh * acc_rate
    else:
        raise ValueError(f"Unknown NEM version: {nem_version}")
```

- [ ] **Step 6: Thread nbc through every call site**

- `src/analysis/compare.py` (~line 112): add kwarg `nbc_per_kwh=rate_info["nbc_per_kwh"]` to the `calculate_export_credit(...)` call.
- `src/analysis/trueup.py` (~line 86): same — `nbc_per_kwh=rate_info["nbc_per_kwh"]`.
- `src/analysis/nem_compare.py` (~line 103): NEM2 call becomes `calculate_export_credit(exp, rate, "NEM2", nbc_per_kwh=rate_info["nbc_per_kwh"])`. Leave the NEM3 call unchanged.
- `src/analysis/simulator.py`: give `_simulate_system` and `_compute_gb_cost` a new parameter `nbc_per_kwh: float = 0.0`; pass `nbc_per_kwh=nbc_per_kwh` in their `calculate_export_credit` calls (lines ~229 and ~393). In `simulate()`, read `nbc = rate_config.get("nbc_per_kwh", 0.0)` next to `bsc_daily` and pass it to all three `_simulate_system` calls and the `_compute_gb_cost` call.
- `src/optimization/battery_optimizer.py` (lines ~146-153): read `nbc = rate_config.get("nbc_per_kwh", 0.0)` near `effective_rates = ...`, then in the export-rate branch replace `export_rate_arr.append(rate)` (NEM2 and the fallback `else`) with `export_rate_arr.append(max(0.0, rate - nbc))`.

- [ ] **Step 7: Run full suite and update golden expectations**

Run: `python -m pytest tests/ -q`

The new NBC tests pass; any failures in `test_compare.py`, `test_trueup.py`, `test_nem_compare.py`, `test_simulator.py`, `test_optimizer.py` will be assertions on absolute export credits / annual totals. For each failure the correction rule is mechanical: NEM2 export credit dropped by exactly `export_kwh × 0.0345` (per period/total). Update the expected numbers accordingly — do not loosen tolerances; recompute the exact expectation. If a test was asserting `credit == export × rate`, it now asserts `credit == export × (rate − 0.0345)`.

- [ ] **Step 8: Commit**

```bash
git add config/pge_rates.json src/rates/engine.py src/rates/nem.py src/analysis/compare.py src/analysis/trueup.py src/analysis/nem_compare.py src/analysis/simulator.py src/optimization/battery_optimizer.py tests/
git commit -m "Model non-bypassable charges: NEM2 export credit is retail minus NBC"
```

---

### Task 6: PG&E holiday calendar for weekday-only peak (P1)

**Files:**
- Create: `src/rates/holidays.py`
- Modify: `src/rates/tou.py:34-70` (optional `date_str` param)
- Modify call sites to pass dates: `src/analysis/compare.py:91`, `src/analysis/trueup.py:68`, `src/analysis/nem_compare.py:78`, `src/analysis/usage.py:61`, `src/analysis/strategy.py:48`, `src/analysis/simulator.py:211,388`, `src/optimization/battery_optimizer.py:141`
- Test: `tests/test_holidays.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_holidays.py
"""PG&E observed holidays — weekday-only peak schedules treat them as off-peak."""

from datetime import date

from src.rates.holidays import is_pge_holiday
from src.rates.tou import classify_tou_period


class TestHolidayCalendar:
    def test_fixed_holidays(self):
        assert is_pge_holiday(date(2026, 1, 1))     # New Year's (Thu)
        assert is_pge_holiday(date(2026, 7, 4))     # Independence Day
        assert is_pge_holiday(date(2026, 11, 11))   # Veterans Day
        assert is_pge_holiday(date(2026, 12, 25))   # Christmas

    def test_floating_holidays_2026(self):
        assert is_pge_holiday(date(2026, 2, 16))    # Presidents Day (3rd Mon Feb)
        assert is_pge_holiday(date(2026, 5, 25))    # Memorial Day (last Mon May)
        assert is_pge_holiday(date(2026, 9, 7))     # Labor Day (1st Mon Sep)
        assert is_pge_holiday(date(2026, 11, 26))   # Thanksgiving (4th Thu Nov)

    def test_sunday_holiday_observed_monday(self):
        # July 4 2027 is a Sunday -> observed Monday July 5
        assert is_pge_holiday(date(2027, 7, 5))

    def test_ordinary_day_is_not_holiday(self):
        assert not is_pge_holiday(date(2026, 6, 12))


class TestTouHolidayClassification:
    def test_etoud_holiday_weekday_peak_hour_is_offpeak(self):
        # Christmas 2026 is a Friday (day_of_week=4); 5-8PM would be peak
        period, _ = classify_tou_period(18, 12, 4, schedule="E-TOU-D",
                                        date_str="2026-12-25")
        assert period == "off_peak"

    def test_etoud_normal_weekday_peak_unchanged(self):
        period, _ = classify_tou_period(18, 12, 4, schedule="E-TOU-D",
                                        date_str="2026-12-18")
        assert period == "peak"

    def test_ev2a_holiday_still_has_peak(self):
        # EV2-A peak applies every day including holidays
        period, _ = classify_tou_period(18, 12, 4, schedule="EV2-A",
                                        date_str="2026-12-25")
        assert period == "peak"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_holidays.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.rates.holidays'`

- [ ] **Step 3: Implement the holiday calendar**

```python
# src/rates/holidays.py
"""PG&E observed holidays for TOU classification.

Per PG&E tariff sheets, weekday-only peak schedules (E-TOU-D) treat these
holidays as off-peak: New Year's Day, Presidents' Day, Memorial Day,
Independence Day, Labor Day, Veterans Day, Thanksgiving Day, Christmas Day.
A holiday falling on Sunday is observed the following Monday. (Saturday
holidays need no observation — Saturdays are already off-peak.)
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """nth occurrence of weekday (0=Mon) in month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


@lru_cache(maxsize=None)
def pge_holidays(year: int) -> frozenset[date]:
    holidays = set()
    for d in (date(year, 1, 1), date(year, 7, 4),
              date(year, 11, 11), date(year, 12, 25)):
        holidays.add(d)
        if d.weekday() == 6:  # Sunday -> observed Monday
            holidays.add(d + timedelta(days=1))
    holidays.add(_nth_weekday(year, 2, 0, 3))    # Presidents' Day
    holidays.add(_last_weekday(year, 5, 0))      # Memorial Day
    holidays.add(_nth_weekday(year, 9, 0, 1))    # Labor Day
    holidays.add(_nth_weekday(year, 11, 3, 4))   # Thanksgiving
    return frozenset(holidays)


def is_pge_holiday(d: date) -> bool:
    return d in pge_holidays(d.year)
```

- [ ] **Step 4: Add date awareness to classify_tou_period**

In `src/rates/tou.py`, change the signature and the weekdays_only check:

```python
def classify_tou_period(hour: int, month: int, day_of_week: int,
                        schedule: str | None = None,
                        schedule_config: dict | None = None,
                        date_str: str | None = None) -> tuple[str, str]:
```

Docstring gains: `date_str: Optional ISO date (YYYY-MM-DD). Enables holiday handling for weekday-only peak schedules (E-TOU-D).`

Replace the weekdays_only block inside the loop:

```python
        # E-TOU-D has weekdays_only peak — weekends AND PG&E holidays are off-peak
        if window.get("weekdays_only", False):
            if day_of_week >= 5:
                continue
            if date_str is not None:
                from src.rates.holidays import is_pge_holiday
                if is_pge_holiday(date.fromisoformat(date_str)):
                    continue
```

(`from datetime import date` is already imported at the top of tou.py.)

- [ ] **Step 5: Pass date_str at every interval-loop call site**

Add `date_str=iv["date"]` (or the local equivalent) to the `classify_tou_period` calls at:
- `src/analysis/compare.py` ~line 91 (`date_str=dt`)
- `src/analysis/trueup.py` ~line 68 (`date_str=iv["date"]`)
- `src/analysis/nem_compare.py` ~line 78 (`date_str=dt`)
- `src/analysis/usage.py` line 61 (`date_str=dt`) — NOT line 115 (that call is a season probe with synthetic hour/dow; no date exists)
- `src/analysis/strategy.py` line 48 (`date_str=iv["date"]`)
- `src/analysis/simulator.py` line 211 (`date_str=iv["date"]`) and line 388 (`date_str=iv["date"]`)
- `src/optimization/battery_optimizer.py` line 141 (`date_str=iv.get("date")`)

- [ ] **Step 6: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass. (Existing tests use EV2-A/E-ELEC fixtures whose peak applies daily, so holiday handling shouldn't shift their numbers; if an E-TOU-D fixture covers a holiday, recompute its expectation — holiday peak hours become off-peak.)

- [ ] **Step 7: Commit**

```bash
git add src/rates/holidays.py src/rates/tou.py src/analysis/ src/optimization/battery_optimizer.py tests/test_holidays.py
git commit -m "Treat PG&E holidays as off-peak for weekday-only peak schedules"
```

---

### Task 7: ACC bounds validation + climate-zone parameter (P1)

**Files:**
- Modify: `src/rates/nem.py:46-85,103+`
- Test: `tests/test_rates.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rates.py`:

```python
# ── ACC table: zones and bounds ───────────────────────────────────────


class TestACC:
    def test_bad_month_raises(self):
        from src.rates.nem import get_acc_rate
        with pytest.raises(ValueError, match="month"):
            get_acc_rate(hour=12, month=0)
        with pytest.raises(ValueError, match="month"):
            get_acc_rate(hour=12, month=13)

    def test_bad_hour_raises(self):
        from src.rates.nem import get_acc_rate
        with pytest.raises(ValueError, match="hour"):
            get_acc_rate(hour=24, month=6)
        with pytest.raises(ValueError, match="hour"):
            get_acc_rate(hour=-1, month=6)

    def test_unknown_zone_raises_with_available_zones(self):
        from src.rates.nem import get_acc_rate
        with pytest.raises(ValueError, match="zone"):
            get_acc_rate(hour=12, month=6, climate_zone=12)

    def test_zone_3_default_unchanged(self):
        from src.rates.nem import get_acc_rate
        assert get_acc_rate(hour=18, month=7) == pytest.approx(0.280)
        assert get_acc_rate(hour=18, month=7, climate_zone=3) == pytest.approx(0.280)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rates.py -q -k TestACC`
Expected: FAIL — no bounds checking, no `climate_zone` kwarg

- [ ] **Step 3: Implement**

In `src/rates/nem.py`, replace `get_acc_rate` and `get_acc_summary`:

```python
def get_acc_rate(hour: int = None, month: int = None,
                 climate_zone: int = 3) -> float:
    """
    Look up the Avoided Cost Calculator rate for a given hour and month.

    Args:
        hour: 0-23 (None defaults to average)
        month: 1-12 (None defaults to average)
        climate_zone: CEC climate zone. Only zone 3 (coastal Bay Area) has
            data today; other PG&E zones (e.g. 11-13 inland) raise until
            their tables are loaded. Zone 3 values overstate export value
            for inland customers — do not silently substitute.

    Returns:
        ACC rate in $/kWh
    """
    if climate_zone not in _ACC_TABLES:
        raise ValueError(
            f"No ACC data loaded for climate zone {climate_zone}. "
            f"Available zones: {sorted(_ACC_TABLES)}. PG&E spans several "
            f"CEC climate zones; zone 3 (coastal Bay Area) is loaded.")
    table = _ACC_TABLES[climate_zone]

    if hour is None or month is None:
        return round(sum(v for row in table for v in row) / (12 * 24), 4)

    if not isinstance(month, int) or not 1 <= month <= 12:
        raise ValueError(f"month must be 1-12, got {month!r}")
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError(f"hour must be 0-23, got {hour!r}")

    return table[month - 1][hour]


def get_acc_summary(climate_zone: int = 3) -> dict:
    """Return summary statistics for the ACC table."""
    if climate_zone not in _ACC_TABLES:
        raise ValueError(f"No ACC data loaded for climate zone {climate_zone}. "
                         f"Available zones: {sorted(_ACC_TABLES)}.")
    table = _ACC_TABLES[climate_zone]
    all_values = [v for row in table for v in row]
    summer_peak = [table[m - 1][h] for m in [6, 7, 8, 9] for h in range(16, 21)]
    winter_offpeak = [table[m - 1][h] for m in [11, 12, 1, 2] for h in range(0, 15)]

    return {
        "annual_average": round(sum(all_values) / len(all_values), 4),
        "min": round(min(all_values), 4),
        "max": round(max(all_values), 4),
        "summer_peak_avg": round(sum(summer_peak) / len(summer_peak), 4),
        "winter_offpeak_avg": round(sum(winter_offpeak) / len(winter_offpeak), 4),
        "climate_zone": climate_zone,
        "note": "PG&E Climate Zone 3 (Bay Area), based on CPUC ACC 2025-2026",
    }
```

After the `_ACC_TABLE` definition, add:

```python
# Keyed by CEC climate zone. Only zone 3 loaded; add inland zones (11-13)
# from CPUC ACC workbooks when needed.
_ACC_TABLES = {3: _ACC_TABLE}
```

`_ACC_ANNUAL_AVERAGE` stays (still used as the no-args default via the computation above — delete the module-level constant if nothing references it after this change; check with grep).

- [ ] **Step 4: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/rates/nem.py tests/test_rates.py
git commit -m "Validate ACC lookups and key the table by climate zone"
```

---

### Task 8: E-TOU-C baseline credit (P1)

PG&E implements baseline pricing on E-TOU-C as a flat per-kWh credit on monthly net usage up to the baseline allowance. From CLAUDE.md rates: above $0.52 vs below $0.44 summer peak → credit ≈ $0.08/kWh.

**Files:**
- Create: `config/baselines.json`
- Create: `src/rates/baseline.py`
- Modify: `config/pge_rates.json` (E-TOU-C block)
- Modify: `src/rates/engine.py` (pass through `baseline_credit_per_kwh`)
- Modify: `src/analysis/compare.py` (apply monthly credit)
- Modify: `server.py` `compare_plans` docstring (mention plan keys `baseline_territory`, `heat_source`)
- Test: `tests/test_rates.py`, `tests/test_compare.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rates.py`:

```python
# ── Baseline allowances ───────────────────────────────────────────────


class TestBaseline:
    def test_territory_t_allowance(self):
        from src.rates.baseline import get_daily_allowance
        assert get_daily_allowance("T", "winter", all_electric=True) > \
            get_daily_allowance("T", "winter", all_electric=False)
        assert get_daily_allowance("T", "summer", all_electric=False) > 0

    def test_unknown_territory_raises(self):
        from src.rates.baseline import get_daily_allowance
        with pytest.raises(ValueError, match="territory"):
            get_daily_allowance("ZZ", "winter")

    def test_etouc_exposes_baseline_credit(self):
        r = lookup_rates("E-TOU-C", "PGE_BUNDLED", income_tier=3)
        assert r["baseline_credit_per_kwh"] == pytest.approx(0.08)

    def test_ev2a_has_no_baseline_credit(self):
        r = lookup_rates("EV2-A", "PGE_BUNDLED", income_tier=3)
        assert r["baseline_credit_per_kwh"] == 0.0
```

Append to `tests/test_compare.py`:

```python
def test_etouc_baseline_credit_reduces_cost():
    """30 winter days of 10 kWh/day net usage on E-TOU-C should earn a
    baseline credit of credit_rate * min(net, allowance*days)."""
    from src.analysis.compare import compare

    intervals = []
    for day in range(1, 31):
        dt = f"2026-01-{day:02d}"
        for hour in range(24):
            intervals.append({
                "date": dt, "hour": hour, "month": 1,
                "day_of_week": (day - 1) % 7,
                "import_kwh": 10.0 / 24, "export_kwh": 0.0,
            })

    plan = {"schedule": "E-TOU-C", "provider": "PGE_BUNDLED",
            "income_tier": 3, "baseline_territory": "T",
            "heat_source": "electric"}
    result = compare(intervals, [plan], nem_version="NEM2")
    r = result["plans"][0]
    assert r["baseline_credit"] > 0
    # 300 kWh net for the month is under any plausible allowance,
    # so the whole month is credited at $0.08/kWh = $24.00
    assert r["baseline_credit"] == pytest.approx(300 * 0.08, abs=0.5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rates.py -q -k Baseline && python -m pytest tests/test_compare.py -q -k baseline`
Expected: FAIL — no `src.rates.baseline` module, no `baseline_credit` key

- [ ] **Step 3: Create config/baselines.json**

```json
{
  "_meta": {
    "source": "PG&E baseline allowance table (kWh/day)",
    "notes": "ESTIMATES for territory T (SF peninsula coastal) — verify against PG&E's current baseline quantities (Schedule E-1 preliminary statement) before relying on absolute numbers. all_electric = permanently-installed electric heat (PG&E code H). Add other territories (P,Q,R,S,V,W,X,Y,Z) as users need them."
  },
  "territories": {
    "T": {
      "basic": {"summer": 6.5, "winter": 7.5},
      "all_electric": {"summer": 6.5, "winter": 11.9}
    }
  }
}
```

- [ ] **Step 4: Create src/rates/baseline.py**

```python
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
```

- [ ] **Step 5: Add credit rate to E-TOU-C config and engine passthrough**

In `config/pge_rates.json`, inside the `"E-TOU-C"` schedule object (sibling of `"total_bundled"`), add:

```json
      "baseline_credit_per_kwh": 0.08,
```

with a note appended to the existing E-TOU-C `_notes`: `"Baseline credit = above-baseline minus below-baseline rate ($0.52-$0.44). Applied to monthly net usage up to the territory allowance."`

In `src/rates/engine.py` `lookup_rates`, add to BOTH return dicts:

```python
        "baseline_credit_per_kwh": sched.get("baseline_credit_per_kwh", 0.0),
```

- [ ] **Step 6: Apply the credit in compare.py**

In `_calculate_annual_cost`, add monthly net tracking to the existing loop. Near the other accumulators (line ~73):

```python
    monthly_net_kwh = defaultdict(float)   # YYYY-MM -> net kWh
    monthly_days = defaultdict(set)
```

Inside the loop (after `days.add(dt)`):

```python
        ym = dt[:7]
        monthly_net_kwh[ym] += imp - exp
        monthly_days[ym].add(dt)
```

After the loop, before `annual_total` is computed, insert:

```python
    # Baseline credit (E-TOU-C): flat credit on monthly net usage up to the
    # territory allowance.
    baseline_credit_total = 0.0
    credit_rate = base_rate_info.get("baseline_credit_per_kwh", 0.0)
    if credit_rate:
        from src.rates.baseline import get_daily_allowance
        territory = plan.get("baseline_territory", "T")
        all_electric = plan.get("heat_source") == "electric"
        for ym, net_kwh in monthly_net_kwh.items():
            month_num = int(ym[5:7])
            season = ("summer" if month_num in base_rate_info["summer_months"]
                      else "winter")
            allowance = (get_daily_allowance(territory, season, all_electric)
                         * len(monthly_days[ym]))
            baseline_credit_total += credit_rate * min(max(net_kwh, 0.0),
                                                       allowance)
```

Change the total line to:

```python
    annual_total = net_energy_cost + bsc_total - baseline_credit_total
```

and add to the returned dict:

```python
        "baseline_credit": round(baseline_credit_total, 2),
```

- [ ] **Step 7: Document the plan keys in server.py**

In the `compare_plans` docstring Args, extend the `plans` line:

```
        plans: List of plan configs, each with {schedule, provider, vintage_year,
               income_tier}. For baseline-tiered schedules (E-TOU-C) optionally
               add {baseline_territory: "T", heat_source: "electric"|"gas"} —
               territory is printed on the PG&E bill.
```

- [ ] **Step 8: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass (other plans get `baseline_credit: 0.0`; E-TOU-C comparisons in existing tests may shift — recompute expectations with the credit formula above if any assert absolute E-TOU-C totals)

- [ ] **Step 9: Commit**

```bash
git add config/baselines.json config/pge_rates.json src/rates/baseline.py src/rates/engine.py src/analysis/compare.py server.py tests/
git commit -m "Apply E-TOU-C baseline credit to monthly net usage in plan comparisons"
```

---

### Task 9: Graceful CCA errors + provider transparency (P1)

**Files:**
- Modify: `src/rates/engine.py:64-72` (error message), add `providers_with_rates()`
- Modify: `server.py` `get_rates` (catch ValueError), `INSTRUCTIONS` (supported-provider line), `get_rates`/`extract_bill_details`/`compare_plans` docstrings
- Test: `tests/test_rates.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rates.py`:

```python
# ── Provider coverage ─────────────────────────────────────────────────


class TestProviderCoverage:
    def test_providers_with_rates(self):
        from src.rates.engine import providers_with_rates
        supported = providers_with_rates("EV2-A")
        assert "PCE" in supported
        assert "PGE_BUNDLED" in supported
        assert "SVCE" not in supported

    def test_unloaded_cca_error_is_actionable(self):
        with pytest.raises(ValueError) as exc:
            lookup_rates("EV2-A", "SVCE", 2017, 3)
        msg = str(exc.value)
        assert "not loaded" in msg
        assert "PCE" in msg            # tells the user what IS supported
        assert "svcleanenergy.org" in msg  # tells the operator where to get rates
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rates.py -q -k ProviderCoverage`
Expected: FAIL — no `providers_with_rates`, current message says "has no rates for schedule"

- [ ] **Step 3: Implement in engine.py**

Add after `_deep_copy_rates`:

```python
def providers_with_rates(schedule: str) -> list[str]:
    """Providers that have real rate data loaded for a schedule."""
    cca = _load_json("cca_rates.json")["providers"]
    supported = ["PGE_BUNDLED"]
    for code, data in cca.items():
        if code == "PGE_BUNDLED":
            continue
        sched = data.get("schedules", {}).get(schedule)
        if isinstance(sched, dict) and ("summer" in sched or "winter" in sched):
            supported.append(code)
    return supported
```

Replace the `if not cca_sched:` branch in `lookup_rates` (~line 70):

```python
        cca_sched = provider_data["schedules"].get(schedule)
        if not isinstance(cca_sched, dict) or not (
                "summer" in cca_sched or "winter" in cca_sched):
            supported = providers_with_rates(schedule)
            website = provider_data.get("website", "the provider's rate page")
            raise ValueError(
                f"{provider} ({provider_data.get('name', provider)}) rates are "
                f"not loaded yet for {schedule}. Providers with rates for "
                f"{schedule}: {', '.join(supported)}. {provider} rates can be "
                f"added to config/cca_rates.json from {website}.")
```

- [ ] **Step 4: Make get_rates return errors instead of raising**

In `server.py` `get_rates`, replace the body:

```python
    from src.rates.engine import lookup_rates
    try:
        return lookup_rates(schedule, provider, vintage_year, income_tier)
    except ValueError as e:
        return {"error": str(e)}
```

Update docstrings: in `get_rates` and `extract_bill_details`, change the provider arg line to:

```
        provider: Electricity provider — "PGE_BUNDLED" or "PCE" (full rates
                  loaded). SVCE/MCE/SJCE/EBCE are recognized but their rates
                  are not loaded yet — the tool returns an actionable error.
```

In `INSTRUCTIONS` (server.py), under SUPPORTED RATES add:

```
- CCA generation rates loaded: PCE (Peninsula Clean Energy). SVCE, MCE,
  SJCE, EBCE are recognized but rates are pending — tools return a clear
  error naming the supported providers.
```

- [ ] **Step 5: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/rates/engine.py server.py tests/test_rates.py
git commit -m "Return actionable errors for CCA providers without loaded rates"
```

---

### Task 10: Input hardening (P1)

**Files:**
- Modify: `src/rates/engine.py:56` (income_tier validation)
- Modify: `src/parsers/green_button.py:56-76` (skip malformed rows)
- Modify: `src/analysis/simulator.py:78` (string-inverter ac_watts fallback)
- Test: `tests/test_rates.py`, `tests/test_parsers.py`, `tests/test_simulator.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rates.py` inside `TestBSC` (or as a new test class):

```python
class TestInputValidation:
    def test_bad_income_tier_raises_value_error(self):
        with pytest.raises(ValueError, match="income_tier"):
            lookup_rates("EV2-A", "PCE", 2016, income_tier=4)
```

Append to `tests/test_parsers.py`:

```python
def test_green_button_skips_malformed_rows():
    from src.parsers.green_button import parse
    csv_content = (
        "Name,TEST USER\n"
        "TYPE,DATE,START TIME,END TIME,IMPORT (kWh),EXPORT (kWh),COST,NOTES\n"
        "Electric usage,2025-03-20,00:00,00:59,2.94,0.00,$1.02\n"
        "Electric usage,NOT-A-DATE,01:00,01:59,1.00,0.00,$0.50\n"
        "Electric usage,2025-03-20,02:00,02:59,1.50,0.00,$0.75\n"
    )
    result = parse(csv_content)
    assert result["summary"]["num_intervals"] == 2
    assert result["summary"]["skipped_rows"] == 1
    assert "NOT-A-DATE" in result["summary"]["skipped_samples"][0]["error"] \
        or result["summary"]["skipped_samples"][0]["row"] == 2
```

Append to `tests/test_simulator.py`:

```python
def test_string_array_ac_watts_fallback():
    """When ac_watts is omitted on a string array, fall back to the string
    inverter's total watts — NOT inverter_watts x panels."""
    from src.analysis.simulator import estimate_array_hourly_kwh
    with_ac = {"panels": 12, "panel_watts": 315, "type": "string",
               "inverter_watts_ac": 4000, "ac_watts": 4000}
    without_ac = {"panels": 12, "panel_watts": 315, "type": "string",
                  "inverter_watts_ac": 4000}
    for hour in (10, 12, 14):
        assert estimate_array_hourly_kwh(without_ac, 6, hour) == \
            pytest.approx(estimate_array_hourly_kwh(with_ac, 6, hour))
```

(Ensure `import pytest` exists at the top of each test file; add it if missing.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rates.py -k InputValidation tests/test_parsers.py -k malformed tests/test_simulator.py -k fallback -q`
Expected: FAIL — KeyError(4) for tier, KeyError on skipped_rows, mismatch on fallback (48 kW vs 4 kW cap)

- [ ] **Step 3: Implement the three fixes**

`src/rates/engine.py` — before `tier_key = {...}[income_tier]` (line ~56):

```python
    if income_tier not in (1, 2, 3):
        raise ValueError(
            f"income_tier must be 1 (CARE), 2 (FERA), or 3 (standard); "
            f"got {income_tier!r}")
```

`src/parsers/green_button.py` — wrap the row loop body:

```python
    skipped = []
    for row_num, row in enumerate(reader, start=1):
        try:
            dt = date.fromisoformat(row["DATE"])
            hour = int(row["START TIME"].split(":")[0])
            import_kwh = _clean_number(row["IMPORT (kWh)"])
            export_kwh = _clean_number(row["EXPORT (kWh)"])
            cost = _clean_number(row["COST"])
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            skipped.append({"row": row_num, "error": f"{type(e).__name__}: {e}"})
            continue

        intervals.append({
            "date": row["DATE"],
            "hour": hour,
            "month": dt.month,
            "day_of_week": dt.weekday(),  # 0=Mon, 6=Sun
            "import_kwh": import_kwh,
            "export_kwh": export_kwh,
            "cost": cost,
        })

        total_import += import_kwh
        total_export += export_kwh
        total_cost += cost
```

and add to the summary dict:

```python
            "skipped_rows": len(skipped),
            "skipped_samples": skipped[:5],
```

`src/analysis/simulator.py` line 78 — replace:

```python
    ac_cap_kw = array.get("ac_watts", inv_w * panels) / 1000
```

with:

```python
    # micro: inverter_watts_ac is per panel; string: it's the whole inverter
    default_ac_watts = inv_w * panels if inv_type == "micro" else inv_w
    ac_cap_kw = array.get("ac_watts", default_ac_watts) / 1000
```

- [ ] **Step 4: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/rates/engine.py src/parsers/green_button.py src/analysis/simulator.py tests/
git commit -m "Harden inputs: validate income_tier, skip malformed CSV rows, fix string-inverter capacity fallback"
```

---

### Task 11: Rate freshness surfacing (P2)

**Files:**
- Modify: `src/rates/engine.py` (add `rates_freshness()`, include in lookup output)
- Modify: `server.py` `INSTRUCTIONS`
- Test: `tests/test_rates.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rates.py`:

```python
# ── Rate freshness ────────────────────────────────────────────────────


class TestRateFreshness:
    def test_lookup_includes_rates_meta(self):
        r = lookup_rates("EV2-A", "PCE", 2016, 3)
        meta = r["rates_meta"]
        assert meta["last_updated"] == "2026-03-21"
        assert isinstance(meta["age_days"], int)
        assert isinstance(meta["stale"], bool)

    def test_stale_flag_carries_warning(self):
        from src.rates.engine import rates_freshness
        meta = rates_freshness()
        if meta["stale"]:
            assert "warning" in meta
        else:
            assert "warning" not in meta
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_rates.py -q -k Freshness`
Expected: FAIL — `KeyError: 'rates_meta'`

- [ ] **Step 3: Implement**

In `src/rates/engine.py`, add near the top: `from datetime import date as _date`, then:

```python
def rates_freshness() -> dict:
    """How fresh the loaded tariff data is. PG&E changes rates ~2x/year."""
    meta = _load_json("pge_rates.json").get("_meta", {})
    last_updated = meta.get("last_updated")
    out = {"source": meta.get("source", ""), "last_updated": last_updated,
           "age_days": None, "stale": False}
    if last_updated:
        age = (_date.today() - _date.fromisoformat(last_updated)).days
        out["age_days"] = age
        out["stale"] = age > 180
        if out["stale"]:
            out["warning"] = (
                f"Rate data was last updated {last_updated} ({age} days ago). "
                "PG&E and CCA rates change ~2x/year — absolute cost estimates "
                "may be off. Plan-vs-plan comparisons are less sensitive.")
    return out
```

Add `"rates_meta": rates_freshness(),` to BOTH return dicts in `lookup_rates`.

In `server.py` `INSTRUCTIONS`, append under KEY DOMAIN RULES:

```
- Every get_rates/extract_bill_details result includes rates_meta. If
  rates_meta.stale is true, tell the user the tariff data is dated and
  absolute costs may drift; relative plan comparisons remain useful.
```

- [ ] **Step 4: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/rates/engine.py server.py tests/test_rates.py
git commit -m "Surface rate-data freshness in every rate lookup"
```

---

### Task 12: Payback/ROI in simulate_system (P2)

**Files:**
- Modify: `src/analysis/simulator.py` `simulate()` (params + financials)
- Modify: `server.py` `simulate_system` tool (params passthrough + docstring)
- Test: `tests/test_simulator.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_simulator.py` (reuse whatever interval fixture the file already builds; otherwise build a minimal one):

```python
def test_simulate_financials():
    from src.analysis.simulator import simulate

    intervals = []
    for day in range(1, 8):
        for hour in range(24):
            intervals.append({
                "date": f"2026-01-{day:02d}", "hour": hour, "month": 1,
                "day_of_week": (day - 1) % 7,
                "import_kwh": 1.5, "export_kwh": 0.0,
            })
    rate_config = {
        "effective_rates": {
            "summer": {"peak": 0.50, "partial_peak": 0.40, "off_peak": 0.20},
            "winter": {"peak": 0.40, "partial_peak": 0.35, "off_peak": 0.20},
        },
        "tou_windows": {
            "peak": {"hours": [16, 17, 18, 19, 20]},
            "partial_peak": {"hours": [15, 21, 22, 23]},
            "off_peak": {"hours": list(range(0, 15))},
        },
        "summer_months": [6, 7, 8, 9],
        "base_services_charge_daily": 0.79,
        "nbc_per_kwh": 0.0345,
    }
    system_config = {
        "current_system": {"arrays": [], "batteries": []},
        "proposed_system": {
            "arrays": [{"panels": 8, "panel_watts": 400, "type": "micro",
                        "inverter_watts_ac": 350, "ac_watts": 2800}],
            "batteries": [],
        },
    }
    result = simulate(intervals, system_config, rate_config,
                      project_cost=8000, rate_escalation=0.03)
    fin = result["financials"]
    assert fin["project_cost"] == 8000
    assert fin["annual_savings_year1"] == result["estimated_savings"]
    if result["estimated_savings"] > 0:
        assert fin["simple_payback_years"] == pytest.approx(
            8000 / result["estimated_savings"], rel=0.01)
    expected_10yr = sum(result["estimated_savings"] * 1.03 ** i
                        for i in range(10))
    assert fin["ten_year_savings"] == pytest.approx(expected_10yr, abs=1.0)
    assert fin["ten_year_net"] == pytest.approx(expected_10yr - 8000, abs=1.0)


def test_simulate_no_cost_means_no_financials():
    from src.analysis.simulator import simulate
    intervals = [{"date": "2026-01-01", "hour": h, "month": 1,
                  "day_of_week": 0, "import_kwh": 1.0, "export_kwh": 0.0}
                 for h in range(24)]
    rate_config = {
        "effective_rates": {"winter": {"off_peak": 0.2, "peak": 0.4,
                                        "partial_peak": 0.3},
                            "summer": {"off_peak": 0.2, "peak": 0.5,
                                        "partial_peak": 0.4}},
        "tou_windows": {"peak": {"hours": [16, 17, 18, 19, 20]},
                        "partial_peak": {"hours": [15, 21, 22, 23]},
                        "off_peak": {"hours": list(range(0, 15))}},
        "summer_months": [6, 7, 8, 9],
        "base_services_charge_daily": 0.79,
    }
    result = simulate(intervals, {"current_system": {}, "proposed_system": {}},
                      rate_config)
    assert result["financials"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_simulator.py -q -k financials`
Expected: FAIL — `TypeError: simulate() got an unexpected keyword argument 'project_cost'`

- [ ] **Step 3: Implement in simulate()**

Change the signature:

```python
def simulate(interval_data: list, system_config: dict,
             rate_config: dict, nem_version: str = "NEM2",
             project_cost: float = None,
             rate_escalation: float = 0.03) -> dict:
```

Docstring Args gain:

```
        project_cost: Optional out-of-pocket cost of the proposed upgrade ($).
            When provided, the result includes payback and 10-year economics.
        rate_escalation: Annual electricity rate escalation for the 10-year
            projection (default 3%).
```

Before the final `return`, add:

```python
    financials = None
    if project_cost is not None and project_cost > 0:
        annual = sim_savings
        ten_year_savings = sum(annual * (1 + rate_escalation) ** i
                               for i in range(10))
        financials = {
            "project_cost": round(project_cost, 2),
            "annual_savings_year1": annual,
            "simple_payback_years": (round(project_cost / annual, 1)
                                     if annual > 0 else None),
            "ten_year_savings": round(ten_year_savings, 2),
            "ten_year_net": round(ten_year_savings - project_cost, 2),
            "rate_escalation": rate_escalation,
            "note": ("Simple payback on year-1 savings; 10-year figures "
                     "escalate savings at the given rate. Excludes financing, "
                     "tax credits (ITC), and panel degradation."),
        }
```

and add `"financials": financials,` to the returned dict.

- [ ] **Step 4: Pass through in server.py**

`simulate_system` tool signature gains `project_cost: float = None, rate_escalation: float = 0.03` (before `config_id`), docstring Args gain the same two lines as above, and the call becomes:

```python
    return simulate(interval_data, system_config, rate_config, nem_version,
                    project_cost=project_cost, rate_escalation=rate_escalation)
```

Docstring Returns adds: `financials (payback_years, ten_year_net) when project_cost is given`.

- [ ] **Step 5: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/analysis/simulator.py server.py tests/test_simulator.py
git commit -m "Add payback and 10-year economics to system simulation"
```

---

### Task 13: EV charging detection in usage_profile (P2)

**Files:**
- Modify: `src/analysis/usage.py` (new `detect_ev_charging`, wire into `profile`)
- Modify: `server.py` `usage_profile` docstring (mention ev_charging)
- Test: `tests/test_usage.py` (new file)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_usage.py
"""Usage profiling — EV charging detection."""

import pytest

from src.analysis.usage import detect_ev_charging, profile
from src.rates.tou import get_schedule_config


def _build_intervals(ev_nights):
    """30 days of flat 0.8 kWh/h baseload; on ev_nights, add 7.2 kWh/h
    during hours 0-3 (L2 charging signature)."""
    intervals = []
    for day in range(1, 31):
        dt = f"2026-01-{day:02d}"
        for hour in range(24):
            imp = 0.8
            if day in ev_nights and hour in (0, 1, 2, 3):
                imp += 7.2
            intervals.append({
                "date": dt, "hour": hour, "month": 1,
                "day_of_week": (day - 1) % 7,
                "import_kwh": imp, "export_kwh": 0.0,
            })
    return intervals


class TestEVDetection:
    def test_detects_sessions(self):
        ev_nights = {2, 5, 9, 12, 16, 19, 23, 26}
        result = detect_ev_charging(
            _build_intervals(ev_nights), get_schedule_config("EV2-A"))
        assert result["detected"] is True
        assert result["num_sessions"] == len(ev_nights)
        # 4 h x 7.2 kWh above baseload per session
        assert result["estimated_ev_kwh"] == pytest.approx(
            len(ev_nights) * 4 * 7.2, rel=0.1)
        assert result["typical_start_hour"] == 0
        # Overnight charging on EV2-A is 100% off-peak
        assert result["pct_in_off_peak"] == pytest.approx(100.0)

    def test_no_ev_no_detection(self):
        result = detect_ev_charging(
            _build_intervals(set()), get_schedule_config("EV2-A"))
        assert result["detected"] is False

    def test_profile_includes_ev_block(self):
        result = profile(_build_intervals({3, 7}))
        assert "ev_charging" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_usage.py -q`
Expected: FAIL with `ImportError: cannot import name 'detect_ev_charging'`

- [ ] **Step 3: Implement detect_ev_charging**

Append to `src/analysis/usage.py`:

```python
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
```

Wire into `profile()` — before the final return:

```python
    ev_charging = detect_ev_charging(interval_data, sched_config)
```

and add `"ev_charging": ev_charging,` to the returned dict.

- [ ] **Step 4: Update the tool docstring**

`server.py` `usage_profile` Returns line gains: `ev_charging (detected sessions, kWh, timing vs off-peak window, recommendations)`.

- [ ] **Step 5: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/analysis/usage.py server.py tests/test_usage.py
git commit -m "Detect EV charging sessions and timing in usage profiles"
```

---

### Task 14: Bill validation tool (P2 — flagship trust feature)

**Files:**
- Create: `src/analysis/bill_validation.py`
- Modify: `server.py` (new `validate_bill` tool, mention in INSTRUCTIONS)
- Test: `tests/test_bill_validation.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bill_validation.py
"""Bill validation: recompute expected bill components from interval data."""

import pytest

from src.analysis.bill_validation import validate_bill


def _intervals(year_month="2026-01", days=30, import_per_hour=1.2,
               export_hours=(11, 12, 13), export_kwh=0.8):
    intervals = []
    for day in range(1, days + 1):
        dt = f"{year_month}-{day:02d}"
        for hour in range(24):
            intervals.append({
                "date": dt, "hour": hour, "month": int(year_month[5:7]),
                "day_of_week": (day - 1) % 7,
                "import_kwh": import_per_hour,
                "export_kwh": export_kwh if hour in export_hours else 0.0,
            })
    return intervals


PLAN = {"schedule": "EV2-A", "provider": "PCE", "vintage_year": 2016,
        "income_tier": 3}


class TestValidateBill:
    def test_components_present_and_consistent(self):
        result = validate_bill(
            _intervals(), PLAN, "2026-01-01", "2026-01-30",
            actual_charges={"total": None})
        exp = result["expected"]
        for key in ("pge_delivery", "generation", "pcia", "nbc_on_exports",
                    "base_services_charge", "total"):
            assert key in exp
        assert exp["total"] == pytest.approx(
            exp["pge_delivery"] + exp["generation"] + exp["pcia"]
            + exp["nbc_on_exports"] + exp["base_services_charge"], abs=0.05)

    def test_deltas_against_actuals(self):
        first = validate_bill(_intervals(), PLAN, "2026-01-01", "2026-01-30",
                              actual_charges={"total": None})
        actual_total = first["expected"]["total"]
        result = validate_bill(_intervals(), PLAN, "2026-01-01", "2026-01-30",
                               actual_charges={"total": actual_total})
        assert result["deltas"]["total"]["delta"] == pytest.approx(0.0, abs=0.01)
        assert result["match_quality"] == "good"

    def test_poor_match_flagged(self):
        result = validate_bill(_intervals(), PLAN, "2026-01-01", "2026-01-30",
                               actual_charges={"total": 9999.0})
        assert result["match_quality"] == "poor"
        assert result["notes"]  # explains the biggest contributor

    def test_no_data_in_period_errors(self):
        result = validate_bill(_intervals(), PLAN, "2025-06-01", "2025-06-30",
                               actual_charges={"total": 100.0})
        assert "error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_bill_validation.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the module**

```python
# src/analysis/bill_validation.py
"""Validate the rate engine against an actual PG&E bill.

Recomputes expected bill components from hourly interval data for one
billing period and compares them with amounts read off the bill. When the
engine reproduces the bill, every other tool's output inherits that trust;
when it doesn't, the per-component deltas show whether rates are stale, the
plan details are wrong, or the interval data is incomplete.

Component model (matches the bill structure documented in CLAUDE.md):
- pge_delivery: net kWh per TOU period x PG&E delivery rate
  (for bundled customers the generation component is listed separately)
- generation: net kWh x CCA generation rate (CCA) or PG&E generation (bundled)
- pcia: net kWh x vintage PCIA (CCA only; 0 for bundled)
- nbc_on_exports: exported kWh x NBC — the non-offsettable remainder after
  the bill's "NBC on gross imports minus net-usage adjustment" algebra
- base_services_charge: daily BSC x days in period
"""

from __future__ import annotations

from src.rates.engine import RateCache
from src.rates.tou import classify_tou_period


def validate_bill(interval_data: list[dict], plan: dict,
                  period_start: str, period_end: str,
                  actual_charges: dict, nem_version: str = "NEM2") -> dict:
    rows = [iv for iv in interval_data
            if period_start <= iv["date"] <= period_end]
    if not rows:
        return {
            "error": "no_data_in_period",
            "message": (f"No interval data between {period_start} and "
                        f"{period_end}. Check the billing period dates on the "
                        f"bill and the date range of the uploaded CSV."),
        }

    rc = RateCache.from_plan(plan)
    schedule_config = rc.schedule_config

    expected_delivery = 0.0
    expected_generation = 0.0
    net_total = 0.0
    export_total = 0.0
    import_total = 0.0
    days = set()

    for iv in rows:
        period, season = classify_tou_period(
            iv["hour"], iv["month"], iv["day_of_week"],
            schedule_config=schedule_config, date_str=iv["date"])
        info = rc.for_date(iv["date"])
        comp = info["components"]
        net = iv["import_kwh"] - iv["export_kwh"]

        delivery_rate = comp["delivery"].get(season, {}).get(period, 0.0)
        gen_rate = comp["generation"].get(season, {}).get(period, 0.0)

        expected_delivery += net * delivery_rate
        expected_generation += net * gen_rate
        net_total += net
        export_total += iv["export_kwh"]
        import_total += iv["import_kwh"]
        days.add(iv["date"])

    base = rc.base
    pcia = net_total * base["components"]["pcia_per_kwh"]
    nbc_on_exports = export_total * base.get("nbc_per_kwh", 0.0)
    bsc = base["base_services_charge_daily"] * len(days)

    expected = {
        "pge_delivery": round(expected_delivery, 2),
        "generation": round(expected_generation, 2),
        "pcia": round(pcia, 2),
        "nbc_on_exports": round(nbc_on_exports, 2),
        "base_services_charge": round(bsc, 2),
        "total": round(expected_delivery + expected_generation + pcia
                       + nbc_on_exports + bsc, 2),
    }

    deltas = {}
    for key, actual in (actual_charges or {}).items():
        if actual is None or key not in expected:
            continue
        delta = round(expected[key] - actual, 2)
        pct = round(abs(delta) / abs(actual) * 100, 1) if actual else None
        deltas[key] = {"expected": expected[key], "actual": actual,
                       "delta": delta, "pct": pct}

    worst_pct = max((d["pct"] for d in deltas.values()
                     if d["pct"] is not None), default=0.0)
    if not deltas:
        match_quality = "no_actuals_provided"
    elif worst_pct < 2.0:
        match_quality = "good"
    elif worst_pct < 5.0:
        match_quality = "fair"
    else:
        match_quality = "poor"

    notes = []
    if match_quality == "good":
        notes.append("The rate engine reproduces this bill — analysis built "
                     "on these rates can be trusted.")
    elif deltas:
        worst_key = max(deltas, key=lambda k: deltas[k]["pct"] or 0)
        d = deltas[worst_key]
        notes.append(
            f"Biggest mismatch is {worst_key}: expected ${d['expected']:,.2f} "
            f"vs actual ${d['actual']:,.2f} ({d['pct']}% off).")
        hints = {
            "pge_delivery": "Delivery rates may be stale for this period, or "
                            "the schedule on the bill differs from the plan.",
            "generation": "Check the provider — CCA generation rates change "
                          "independently of PG&E and several are estimates.",
            "pcia": "Check the PCIA vintage year printed on the bill.",
            "nbc_on_exports": "NBC per-kWh in config may need updating from "
                              "the current PUC sheets.",
            "base_services_charge": "Check the income tier (CARE/FERA/standard) "
                                    "and the number of days in the period.",
            "total": "Compare the per-component lines on the bill to narrow "
                     "down which piece drifts.",
        }
        if worst_key in hints:
            notes.append(hints[worst_key])

    return {
        "period": {"start": period_start, "end": period_end,
                   "days": len(days),
                   "net_kwh": round(net_total, 1),
                   "import_kwh": round(import_total, 1),
                   "export_kwh": round(export_total, 1)},
        "plan": plan,
        "expected": expected,
        "actual": actual_charges,
        "deltas": deltas,
        "match_quality": match_quality,
        "notes": notes,
    }
```

- [ ] **Step 4: Add the MCP tool**

In `server.py`, after `compare_nem_versions`:

```python
@mcp.tool(tags={"analysis", "billing", "rates"}, annotations={"title": "Validate against actual bill", "readOnlyHint": True, "openWorldHint": False})
async def validate_bill(
    interval_data: list[dict],
    plan: dict,
    period_start: str,
    period_end: str,
    actual_charges: dict,
    nem_version: str = "NEM2",
) -> dict:
    """
    Recompute a billing period from interval data and compare with the actual bill.

    This is the trust check: if the engine reproduces the user's real bill
    within a few percent, every comparison and projection built on these
    rates is credible. Run it once after parsing a bill.

    Reading actual_charges off the bill:
    - "pge_delivery": the Net Usage lines in the PG&E NEM section (sum of
      peak/part-peak/off-peak amounts)
    - "generation": Total CCA charges (e.g. "Total PCE Charges") for CCA
      customers, or the generation portion for bundled
    - "pcia": the "Power Charge Indifference Adjustment" line
    - "nbc_on_exports": State Mandated NBC line PLUS the (negative) "NBC Net
      Usage Adjustment" line — their sum
    - "base_services_charge": the Base Services Charge line
    - "total": monthly NEM charges + CCA charges + BSC
    Omit (or pass null for) lines you can't find — only provided keys are compared.

    Args:
        interval_data: Hourly records from parse_green_button
        plan: {schedule, provider, vintage_year, income_tier}
        period_start: Billing period start date (YYYY-MM-DD, from the bill)
        period_end: Billing period end date (YYYY-MM-DD)
        actual_charges: Dollar amounts read off the bill (see above)
        nem_version: "NEM2" or "NEM3"

    Returns:
        Dict with expected components, per-component deltas vs actuals,
        match_quality (good/fair/poor), and notes naming the biggest drift.
    """
    from src.analysis.bill_validation import validate_bill as compute
    return compute(interval_data, plan, period_start, period_end,
                   actual_charges, nem_version)
```

In `INSTRUCTIONS` TYPICAL FLOW, after step 4 (bill extraction), insert:

```
5. Trust check: run validate_bill with the bill's line items and billing
   period. If match_quality is "good", say so — it makes every later
   number credible. If "poor", investigate before drawing conclusions.
```

(renumber the old step 5 to 6).

- [ ] **Step 5: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/analysis/bill_validation.py server.py tests/test_bill_validation.py
git commit -m "Add validate_bill: reproduce an actual PG&E bill from interval data"
```

---

### Task 15: Pip-installable solver for the optimizer (P3)

**Files:**
- Modify: `src/optimization/battery_optimizer.py:77-104` (solver selection)
- Modify: `pyproject.toml`, `requirements.txt`
- Test: `tests/test_optimizer.py`

- [ ] **Step 1: Add highspy to dependencies**

`pyproject.toml` dependencies list gains:

```toml
    "highspy>=1.7.0",
```

`requirements.txt` gains:

```
highspy>=1.7.0
```

Install locally: `pip install highspy` (or `pip install -e .`).

- [ ] **Step 2: Write the failing test**

Append to `tests/test_optimizer.py`:

```python
def test_solver_selection_prefers_available_solver():
    """With highspy installed, the optimizer must find a solver and not
    return the 'No compatible solver' error."""
    from src.optimization.battery_optimizer import optimize_dispatch

    intervals = [{"date": "2026-01-01", "hour": h, "month": 1,
                  "day_of_week": 0, "import_kwh": 1.0, "export_kwh": 0.0}
                 for h in range(24)]
    rate_config = {
        "effective_rates": {"winter": {"peak": 0.40, "partial_peak": 0.30,
                                        "off_peak": 0.20},
                            "summer": {"peak": 0.50, "partial_peak": 0.40,
                                        "off_peak": 0.20}},
        "tou_windows": {"peak": {"hours": [16, 17, 18, 19, 20]},
                        "partial_peak": {"hours": [15, 21, 22, 23]},
                        "off_peak": {"hours": list(range(0, 15))}},
        "summer_months": [6, 7, 8, 9],
    }
    system = {"arrays": [], "batteries": [{"kwh": 13.5, "kw": 5.0,
                                           "efficiency": 0.9,
                                           "status": "working"}]}
    result = optimize_dispatch(intervals, system, rate_config,
                               horizon_days=1)
    assert "No compatible solver" not in str(result.get("error", ""))
    assert result.get("model_status", {}).get("solver") in (
        "appsi_highs", "cbc", "glpk")
```

- [ ] **Step 3: Run test**

Run: `python -m pytest tests/test_optimizer.py -q -k solver_selection`
Expected: depends on local CBC. If CBC is installed locally it may pass via cbc already — the implementation step still matters for Railway. If highspy isn't installed yet, this fails; install first.

- [ ] **Step 4: Implement ordered solver fallback**

In `src/optimization/battery_optimizer.py`, replace the solver-selection block (lines ~85-104):

```python
    # Pick the first available solver. appsi_highs ships as a pip wheel
    # (highspy) so it works on Railway without system packages; CBC/GLPK
    # remain supported for local installs.
    solver_name = None
    for candidate in ("appsi_highs", "cbc", "glpk"):
        try:
            if pyo.SolverFactory(candidate).available():
                solver_name = candidate
                break
        except Exception:
            continue
    if solver_name is None:
        return {
            "error": "No compatible solver found.",
            "hint": "pip install highspy (pure-pip, recommended), or install "
                    "CBC (brew install cbc / apt-get install coinor-cbc) or "
                    "GLPK (brew install glpk / apt-get install glpk-utils).",
        }
```

(The old `solver = pyo.SolverFactory("cbc") ... else: solver_name = "cbc"` block is fully replaced; `solve_model(model, solver_name=solver_name)` downstream is unchanged.)

- [ ] **Step 5: Run full optimizer suite**

Run: `python -m pytest tests/test_optimizer.py -q`
Expected: all pass, solver reported as `appsi_highs` (or `cbc` if locally installed and HiGHS unavailable)

- [ ] **Step 6: Update README solver note**

In README's Getting Started section, replace the "Install CBC solver for battery optimizer" comment block with:

```markdown
# Battery optimizer solver: highspy installs automatically with the package.
# CBC/GLPK also work if you prefer: brew install cbc
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml requirements.txt src/optimization/battery_optimizer.py tests/test_optimizer.py README.md
git commit -m "Use pip-installable HiGHS solver with CBC/GLPK fallback"
```

---

### Task 16: Web app decision — keep local-only, remove dead mounting code (P2)

Decision: browsers can't send bearer headers, so mounting the unauthenticated web app on the public deployment is unsafe and pointless. The web UI stays available locally via `python server.py --web`; the never-used `create_combined_app()` goes away.

**Files:**
- Modify: `server.py:910-922` (delete `create_combined_app`)
- Modify: `README.md` (web section note)

- [ ] **Step 1: Confirm nothing references it**

Run: `grep -rn "create_combined_app" --include="*.py" --include="*.md" --include="Procfile" . | grep -v .venv`
Expected: only the definition in server.py

- [ ] **Step 2: Delete the function**

Remove the entire `create_combined_app()` definition (server.py lines 910-922, the function and its docstring).

- [ ] **Step 3: Note the decision in README**

In the README section that mentions the web UI (near line 204, `# Open http://localhost:8001`), add:

```markdown
> The web UI is local-only by design — it has no authentication, so it is
> not mounted on the deployed MCP server. Run it with `python server.py --web`.
```

- [ ] **Step 4: Run full suite and commit**

Run: `python -m pytest tests/ -q`
Expected: all pass

```bash
git add server.py README.md
git commit -m "Keep web UI local-only and drop unused combined-app mounting"
```

---

### Task 17: CLAUDE.md refresh (P3)

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the stale sections**

Make these specific edits (leave the tariff-rates and billing sections alone — they're the crown jewels):

1. **Key Design Principles** — replace the "Stateless Phase 1" bullet with:

```markdown
- **Stateless analysis, opt-in persistence.** CSV data comes in via tool
  parameters. System configs and PG&E OAuth tokens persist in SQLite at
  `$DATA_DIR/configs.db` (mount a volume in production). Live integrations
  (Powerwall, Solcast, PG&E Share My Data) activate via env credentials and
  degrade to setup instructions when unconfigured.
- **Auth:** `MCP_AUTH_TOKEN` enables bearer auth on the HTTP endpoint.
  Never configure Tesla/PG&E credentials on a deployment without it.
```

2. **Architecture tree** — replace with the actual layout:

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
│   ├── pcia_vintages.json
│   ├── baselines.json         # Baseline allowances by territory
│   └── rate_history.json      # Historical rate periods
├── web/                       # Local-only FastAPI UI (python server.py --web)
└── tests/
```

3. **MCP Tools section** — replace the Phase 1/2/3 lists with:

```markdown
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
```

4. **NEM 2.0 section** — update the NBC bullet:

```markdown
- NBC (~$0.0345/kWh, config `pge_rates.json:nbc`) cannot be offset by
  export credits — modeled as NEM2 export credit = retail − NBC
```

5. **Development Notes** — add:

```markdown
- NBC per-kWh and baseline allowances live in config — update with tariff changes.
- `rates_meta` in every lookup carries last_updated/stale so Claude can warn users.
- E-TOU-C baseline credit: $0.08/kWh on monthly net usage up to territory allowance.
- E-TOU-D peak skips PG&E holidays (src/rates/holidays.py).
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "Refresh CLAUDE.md to match the implemented architecture"
```

---

### Task 18: Final verification

- [ ] **Step 1: Full suite + count**

Run: `python -m pytest tests/ -q`
Expected: all pass, count > 234. Update the README line `# Run tests (234 passing)` with the new count.

- [ ] **Step 2: Boot smoke test**

Run: `timeout 5 python server.py & sleep 2 && curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/icon.svg; wait`
Expected: 200, plus the startup warning about MCP_AUTH_TOKEN being unset.

- [ ] **Step 3: Commit any README count fix**

```bash
git add README.md
git commit -m "Update test count in README"
```

---

## Self-Review Notes

- **Spec coverage:** P0 → Tasks 1, 2. P1 → Tasks 5 (NBC), 6 (holidays), 7 (ACC zones/bounds), 8 (baseline), 9 (CCA errors), 10 (hardening). P2 → Tasks 11 (freshness), 12 (ROI), 13 (EV), 14 (bill validation), 16 (web decision). P3 → Tasks 3 (dedupe), 4 (history ordering), 15 (solver), 17 (CLAUDE.md). Token encryption from the review's "concerns" list is intentionally deferred (documented in README Security as operator guidance) — bearer auth + volume + no-creds-on-open-deployments covers the attack path.
- **Type consistency:** `RateCache.from_plan` introduced in Task 3 is used by Task 14; `nbc_per_kwh` key introduced in Task 5 is consumed by Tasks 12 (test fixture) and 14; `date_str` kwarg from Task 6 is used in Tasks 13 and 14. Tasks must execute in order.
- **Known judgment calls:** NBC value $0.0345 and territory-T baseline allowances are bill-derived/estimated and flagged `_notes` for verification — consistent with existing config conventions. claude.ai connectors and bearer headers: documented limitation, not a blocker.
