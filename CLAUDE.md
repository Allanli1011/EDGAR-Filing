# CLAUDE.md — EDGAR Filing Project

This file provides guidance for AI assistants (Claude and others) working in this repository.

## Project Overview

This repository is for an **EDGAR Filing** project — tooling related to the SEC's Electronic Data Gathering, Analysis, and Retrieval (EDGAR) system. EDGAR is the primary system for companies and individuals to submit filings to the U.S. Securities and Exchange Commission (SEC).

Typical use cases include:
- Downloading/parsing SEC EDGAR filings (10-K, 10-Q, 8-K, proxy statements, etc.)
- Extracting structured data from XBRL or HTML filings
- Automating submission workflows
- Analyzing financial disclosures

The primary use case is **daily monitoring of SEC EDGAR filings** with AI-powered analysis via Claude to surface investment-relevant signals for US equities.

---

## Repository Structure

```
EDGAR-Filing/
├── CLAUDE.md                  # This file
├── .env.example               # Template for required environment variables
├── .gitignore
├── requirements.txt           # Python dependencies
├── run_monitor.py             # CLI entry point
├── src/
│   ├── __init__.py
│   ├── config.py              # MonitorConfig (reads from env vars)
│   ├── models.py              # Filing, AnalysisResult dataclasses
│   ├── collector.py           # EDGARCollector — fetches filings via EFTS API
│   ├── analyzer.py            # FilingAnalyzer — multi-backend LLM analysis
│   ├── openclaw_config.py     # OpenClaw config reader (openclaw.json parser)
│   ├── monitor.py             # DailyMonitor — orchestrates the pipeline
│   └── storage.py             # ResultStorage — saves JSON + CSV output
├── tests/
│   ├── test_collector.py
│   ├── test_analyzer.py
│   ├── test_models.py
│   └── test_openclaw_config.py
└── output/                    # Daily results (gitignored)
    └── edgar_analysis_YYYY-MM-DD.{json,csv}
```

---

## Development Setup

### Prerequisites

- **Python 3.11+**
- A valid **SEC EDGAR User-Agent** string (required by SEC policy)
- **One of the following LLM backends:**
  - **Anthropic API key** (from https://console.anthropic.com) — for `LLM_BACKEND=claude` (default)
  - **OpenClaw** configured with at least one provider — for `LLM_BACKEND=openclaw`

### Installation

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Configure secrets
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY and EDGAR_USER_AGENT
```

---

## Running the Monitor

```bash
# Monitor today's filings (all configured form types)
python run_monitor.py

# Monitor a specific date
python run_monitor.py --date 2024-12-01

# Dry run — fetch filings but skip Claude analysis
python run_monitor.py --dry-run

# Override form types
python run_monitor.py --forms 8-K 4 "SC 13D"

# Limit filings per run (useful for testing)
python run_monitor.py --max-filings 5

# Verbose logging
python run_monitor.py -v
```

Output is saved to `output/edgar_analysis_YYYY-MM-DD.json` and `.csv`.

---

## Monitored Form Types

| Priority | Form Types | Rationale |
|----------|-----------|-----------|
| 1 (real-time) | Form 4, 8-K, SC 13D/G | Insider trades, material events, activist stakes |
| 2 (fundamental) | 10-K, 10-Q, DEF 14A | Earnings, annual reports, governance |
| 3 (market structure) | 13F-HR, S-1, 424B4 | Institutional holdings, IPOs |
| 4 (special events) | SC TO-T/I, NT 10-K/Q, 15-12G | Tender offers, late filings, deregistration |

All form types are configured in `src/config.py` (`DEFAULT_FORM_TYPES`).

---

## Architecture

```
run_monitor.py
    └── DailyMonitor.run()
            ├── EDGARCollector.fetch_recent_filings()   ← EDGAR EFTS API
            │       └── EDGARCollector.fetch_filing_text()
            ├── FilingAnalyzer.analyze()                ← LLM backend (Claude or OpenClaw)
            └── ResultStorage.save_daily_results()      ← JSON + CSV
```

- **Collector** throttles requests at ≥110ms per call (SEC 10 req/s limit)
- **Analyzer** supports two backends, selected via `LLM_BACKEND` env var:
  - `claude` (default) — `claude-opus-4-6` with adaptive thinking and streaming
  - `openclaw` — reads `~/.openclaw/openclaw.json`, calls provider API directly
- **OpenClaw config** (`src/openclaw_config.py`) parses JSON5 config, resolves model by ID, supports `openai-completions`, `openai-responses`, and `anthropic-messages` API types
- Each form type has a specialized analysis prompt in `src/analyzer.py`
- Results include: summary, investment_signals, risk_factors, sentiment, key_metrics

---

## Key Conventions

### Git Workflow

- **Main branch**: `main` (or `master`) — production-ready code only
- **Feature branches**: `feature/<description>` or `claude/<description>-<id>`
- **Commit messages**: Use imperative mood, e.g. `Add 10-K parser`, `Fix XBRL namespace handling`
- Always commit `CLAUDE.md` updates when the codebase changes significantly

### Branch Naming

Claude-generated branches follow the pattern: `claude/<description>-<sessionId>`

### Code Style

- **Language**: Python 3.11+
- **Formatter**: `black` (line length 100)
- **Linter**: `ruff`
- **Type hints**: encouraged throughout `src/`

### Environment Variables

Never commit secrets or credentials. Use a `.env` file (gitignored). Required variables:
- `EDGAR_USER_AGENT` — required by SEC (`User-Agent: Your Name your@email.com`)
- `ANTHROPIC_API_KEY` — Claude API key; required when `LLM_BACKEND=claude` (default)

LLM backend selection:
- `LLM_BACKEND` — `claude` (default) or `openclaw`
- `OPENCLAW_MODEL_ID` — model ID from `openclaw.json`; optional when `LLM_BACKEND=openclaw` if `agents.defaults.model.primary` is set in `openclaw.json`
- `OPENCLAW_CONFIG_PATH` — override path to `openclaw.json` (default: `~/.openclaw/openclaw.json`)
- `OPENCLAW_STATE_DIR` / `OPENCLAW_HOME` — override openclaw home directory

See `.env.example` for the full list of optional configuration variables.

The SEC requires a valid `User-Agent` header on all EDGAR HTTP requests. See [EDGAR full-text search API](https://efts.sec.gov/LATEST/search-index?q=%22full+text+search%22&dateRange=custom&startdt=2021-01-01&enddt=2021-12-31).

---

## SEC EDGAR API Notes

### Rate Limits

The SEC enforces a **10 requests/second** limit per IP. Throttle all HTTP clients accordingly.

```python
# Example: respectful rate limiting
import time
import requests

HEADERS = {"User-Agent": "YourName your@email.com"}

def edgar_get(url):
    resp = requests.get(url, headers=HEADERS)
    time.sleep(0.11)  # stay under 10 req/s
    return resp
```

### Key Endpoints

| Resource | URL |
|----------|-----|
| Company search | `https://efts.sec.gov/LATEST/search-index?q=<query>` |
| Company facts (XBRL) | `https://data.sec.gov/api/xbrl/companyfacts/CIK<10-digit>.json` |
| Submissions | `https://data.sec.gov/submissions/CIK<10-digit>.json` |
| Filing index | `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=<cik>&type=10-K` |
| Full-text search | `https://efts.sec.gov/LATEST/search-index` |

CIK numbers must be zero-padded to 10 digits (e.g., Apple = `0000320193`).

---

## Testing

```bash
pytest tests/ -v
pytest tests/ -v --cov=src --cov-report=term-missing
```

- Tests live in `tests/`, mirroring `src/` structure
- HTTP calls to EDGAR are mocked with the `responses` library — never hit live EDGAR in CI
- Claude and OpenAI API calls are mocked with `unittest.mock.patch`
- `test_openclaw_config.py` uses `tmp_path` fixtures — no real `openclaw.json` needed

---

## Common Tasks

### Run the Daily Monitor

```bash
python run_monitor.py --date 2024-12-01
```

### Switch to OpenClaw Backend

```bash
# In .env, set:
LLM_BACKEND=openclaw
# Optionally set a specific model (if not set, agents.defaults.model.primary is used):
# OPENCLAW_MODEL_ID=qwen2.5:72b

# List all models available in your openclaw.json:
python -c "from src.openclaw_config import list_available_models; [print(m['provider'], m['id']) for m in list_available_models()]"
```

OpenClaw reads `~/.openclaw/openclaw.json` directly — no separate API key or URL config needed. API credentials are resolved from the provider's `apiKey` field or `~/.openclaw/credentials/<provider_name>`.

### Add a New Form Type

1. Add the form type string to `DEFAULT_FORM_TYPES` in `src/config.py`
2. Add a specialized prompt in `_FORM_PROMPTS` dict in `src/analyzer.py`
3. Add a priority mapping in `Filing.priority` in `src/models.py`
4. Add tests in `tests/test_collector.py` and `tests/test_analyzer.py`

### Fetch Company Facts (XBRL)

```python
import requests

CIK = "0000320193"  # Apple
url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"
data = requests.get(url, headers={"User-Agent": "YourName your@email.com"}).json()
```

### Add a Cron Job for Daily Runs

```bash
# Run at 6 AM UTC every weekday (markets open)
0 6 * * 1-5 cd /path/to/EDGAR-Filing && .venv/bin/python run_monitor.py >> logs/monitor.log 2>&1
```

---

## AI Assistant Instructions

When working in this repository:

1. **Read this file first** before making changes.
2. **Update `CLAUDE.md`** whenever you add new modules, change conventions, or introduce new dependencies.
3. **Respect SEC rate limits** in any code that calls EDGAR APIs.
4. **Never hardcode credentials** — use environment variables.
5. **Follow the established code style** once it is defined in this file.
6. **Write tests** for any parsing or data transformation logic.
7. **Prefer small, focused commits** with descriptive messages.
8. **Branch target**: All Claude-generated changes go to the branch specified in the task context (`claude/add-claude-documentation-fApnX`).
