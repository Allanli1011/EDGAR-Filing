# CLAUDE.md — EDGAR Filing Project

This file provides guidance for AI assistants (Claude and others) working in this repository.

## Project Overview

This repository is for an **EDGAR Filing** project — tooling related to the SEC's Electronic Data Gathering, Analysis, and Retrieval (EDGAR) system. EDGAR is the primary system for companies and individuals to submit filings to the U.S. Securities and Exchange Commission (SEC).

Typical use cases include:
- Downloading/parsing SEC EDGAR filings (10-K, 10-Q, 8-K, proxy statements, etc.)
- Extracting structured data from XBRL or HTML filings
- Automating submission workflows
- Analyzing financial disclosures

> **Note:** This repository was initialized without source files. Update this document as the codebase evolves.

---

## Repository Structure

```
EDGAR-Filing/
├── CLAUDE.md          # This file
├── README.md          # (to be created) User-facing documentation
├── .gitignore         # (to be created)
└── src/               # (to be created) Source code
```

Update this section as directories and files are added.

---

## Development Setup

### Prerequisites

Document prerequisites here when the tech stack is decided. Common choices for EDGAR projects:

- **Python** (recommended): `requests`, `beautifulsoup4`, `lxml`, `pandas`, `sec-edgar-downloader`
- **Node.js**: `axios`, `cheerio`, `puppeteer`

### Installation

```bash
# Python example
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Update with actual commands once dependencies are defined.

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

Adopt and document a linter/formatter once the language is chosen:
- Python: `ruff` + `black`, type hints encouraged
- JavaScript/TypeScript: `eslint` + `prettier`

### Environment Variables

Never commit secrets or credentials. Use a `.env` file (gitignored) for:
- `SEC_API_KEY` — if using a third-party SEC data API
- `EDGAR_USER_AGENT` — required by SEC EDGAR (`User-Agent: Your Name your@email.com`)

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

Document the test framework and how to run tests once they exist:

```bash
# Python example
pytest tests/ -v

# JavaScript example
npm test
```

- Place tests in a `tests/` directory mirroring the `src/` structure
- Aim for unit tests on parsers and integration tests on API clients
- Mock HTTP calls in unit tests; avoid hitting EDGAR live in CI

---

## Common Tasks

### Download a Filing

```python
# Example using sec-edgar-downloader
from sec_edgar_downloader import Downloader

dl = Downloader("YourCompany", "your@email.com")
dl.get("10-K", "AAPL", limit=5)  # Download last 5 Apple 10-Ks
```

### Fetch Company Facts (XBRL)

```python
import requests

CIK = "0000320193"  # Apple
url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"
data = requests.get(url, headers={"User-Agent": "YourName your@email.com"}).json()
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
