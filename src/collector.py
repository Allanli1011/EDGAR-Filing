"""
EDGAR Collector — fetches recent filings from the SEC EDGAR API.

Rate limit: 10 requests/second per SEC policy.
"""

import time
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import requests

from .models import Filing

logger = logging.getLogger(__name__)

# SEC requires a descriptive User-Agent on every request.
# Set EDGAR_USER_AGENT in your .env, e.g. "YourName your@email.com"
_DEFAULT_USER_AGENT = "EDGAR-Filing-Monitor contact@example.com"

# EDGAR EFTS full-text search API
_EFTS_BASE = "https://efts.sec.gov/LATEST/search-index"

# Submissions API (company-level metadata)
_SUBMISSIONS_BASE = "https://data.sec.gov/submissions"

# Filing archive
_ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"

# Throttle: sleep at least this many seconds between requests (10 req/s limit)
_MIN_INTERVAL = 0.11


class EDGARCollector:
    """Fetches recent filings from SEC EDGAR."""

    def __init__(self, user_agent: str = _DEFAULT_USER_AGENT):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self._last_request_time: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_recent_filings(
        self,
        form_types: list[str],
        target_date: Optional[date] = None,
        days_back: int = 1,
        max_per_form: int = 40,
    ) -> list[Filing]:
        """
        Return filings of the given form types filed within the date range.

        Args:
            form_types: List of SEC form type strings, e.g. ["8-K", "4"].
            target_date: End date (defaults to today).
            days_back: How many calendar days back to look (default 1 = yesterday).
            max_per_form: Max results per form type per request.

        Returns:
            List of Filing objects, sorted by priority then filed_at descending.
        """
        if target_date is None:
            target_date = date.today()

        start_date = target_date - timedelta(days=days_back)
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = target_date.strftime("%Y-%m-%d")

        all_filings: list[Filing] = []
        for form_type in form_types:
            logger.info("Fetching %s filings from %s to %s", form_type, start_str, end_str)
            try:
                filings = self._fetch_by_form(
                    form_type, start_str, end_str, max_per_form
                )
                logger.info("  → %d filings found", len(filings))
                all_filings.extend(filings)
            except Exception as exc:
                logger.error("Failed to fetch %s: %s", form_type, exc)

        # Sort by priority asc, then filed_at desc
        all_filings.sort(key=lambda f: (f.priority, f.filed_at), reverse=False)
        all_filings.sort(key=lambda f: f.filed_at, reverse=True)
        all_filings.sort(key=lambda f: f.priority)

        return all_filings

    def fetch_filing_text(
        self, filing: Filing, max_chars: int = 50_000
    ) -> str:
        """
        Download the primary document text for a filing.

        Returns up to max_chars characters to keep token costs manageable.
        """
        url = filing.document_url or filing.filing_url
        if not url:
            return ""

        try:
            resp = self._get(url)
            text = resp.text
            # Strip HTML tags for cleaner text extraction
            text = _strip_html(text)
            text = _normalize_whitespace(text)
            return text[:max_chars]
        except Exception as exc:
            logger.warning("Could not fetch filing text from %s: %s", url, exc)
            return ""

    def fetch_filing_index(self, filing: Filing) -> dict:
        """Return the EDGAR filing index JSON for a given accession number."""
        cik_num = filing.cik.lstrip("0") or "0"
        accession_clean = filing.accession_no.replace("-", "")
        url = f"{_ARCHIVE_BASE}/{cik_num}/{accession_clean}/{accession_clean}-index.json"
        try:
            resp = self._get(url)
            return resp.json()
        except Exception as exc:
            logger.warning("Could not fetch filing index: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_by_form(
        self,
        form_type: str,
        start_date: str,
        end_date: str,
        max_results: int,
    ) -> list[Filing]:
        """Query EDGAR EFTS for filings of a specific form type."""
        params = {
            "q": "",
            "dateRange": "custom",
            "startdt": start_date,
            "enddt": end_date,
            "forms": form_type,
            "_source": (
                "period_of_report,entity_name,file_num,period_of_report,"
                "biz_location,inc_states,category,form_type,entity_id,"
                "file_date,accession_no,display_date_filed,display_names,"
                "period_of_report"
            ),
            "from": 0,
            "size": max_results,
        }

        url = f"{_EFTS_BASE}?{urlencode(params)}"
        resp = self._get(url)
        data = resp.json()

        if not isinstance(data, dict):
            logger.error("Expected API response to be dict, got %s: %s", type(data), data)
            return []

        hits_wrapper = data.get("hits", {})
        if not isinstance(hits_wrapper, dict):
            logger.error("Expected 'hits' wrapper to be dict, got %s: %s", type(hits_wrapper), hits_wrapper)
            return []

        hits = hits_wrapper.get("hits", [])
        if not isinstance(hits, list):
            logger.error("Expected 'hits' list to be list, got %s: %s", type(hits), hits)
            return []

        filings: list[Filing] = []
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            src = hit.get("_source", {})
            if not isinstance(src, dict):
                continue
            
            # Handle both accession_no (older/mock) and adsh (current live)
            acc = src.get("accession_no") or src.get("adsh")
            if acc:
                filing = self._parse_hit(src, form_type)
                if filing:
                    filings.append(filing)

        return filings

    def _parse_hit(self, src: dict, form_type: str) -> Optional[Filing]:
        """Convert a raw EFTS search hit into a Filing object."""
        # accession_no / adsh
        accession_no = src.get("accession_no") or src.get("adsh", "")
        if not accession_no:
            return None

        # entity_id / ciks
        cik = src.get("entity_id", "")
        if not cik:
            ciks = src.get("ciks", [])
            if isinstance(ciks, list) and ciks:
                cik = ciks[-1]  # Usually the issuer CIK is last in Form 4s

        # entity_id may be bare int; zero-pad to 10 digits
        try:
            cik = str(int(cik)).zfill(10)
        except (ValueError, TypeError):
            cik = cik.zfill(10) if cik else "0000000000"

        company_name = ""
        display_names = src.get("display_names", [])
        if isinstance(display_names, list) and display_names:
            first_name = display_names[0]
            if isinstance(first_name, dict):
                company_name = first_name.get("name", "")
            else:
                company_name = str(first_name)
            
            # If it contains "(CIK ...)", clean it up
            company_name = re.sub(r"\s*\(CIK\s+\d+\)", "", company_name).strip()
        
        if not company_name:
            company_name = src.get("entity_name", "Unknown")

        # file_date / display_date_filed
        filed_at = src.get("file_date", src.get("display_date_filed", ""))
        # period_of_report / period_ending
        period = src.get("period_of_report", src.get("period_ending", ""))
        # form_type / form / file_type
        actual_form = src.get("form_type", src.get("form", src.get("file_type", form_type)))
        items = src.get("items", None)

        # Build filing index URL
        cik_num = cik.lstrip("0") or "0"
        accession_clean = accession_no.replace("-", "")
        filing_url = f"{_ARCHIVE_BASE}/{cik_num}/{accession_clean}/"

        # Best-effort primary document URL (will be refined via index if needed)
        document_url = f"{filing_url}{accession_clean}-index.htm"

        return Filing(
            accession_no=accession_no,
            form_type=actual_form,
            cik=cik,
            company_name=company_name,
            filed_at=filed_at,
            period_of_report=period or None,
            filing_url=filing_url,
            document_url=document_url,
            items=items,
        )

    def _get(self, url: str, **kwargs) -> requests.Response:
        """Throttled GET request respecting SEC rate limits."""
        elapsed = time.time() - self._last_request_time
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)

        resp = self.session.get(url, timeout=30, **kwargs)
        self._last_request_time = time.time()
        resp.raise_for_status()
        return resp


# ------------------------------------------------------------------
# Text utilities
# ------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_WHITESPACE_RE = re.compile(r"\s{2,}")


def _strip_html(text: str) -> str:
    """Remove HTML/XML tags from text."""
    text = _HTML_TAG_RE.sub(" ", text)
    return text


def _normalize_whitespace(text: str) -> str:
    """Collapse multiple whitespace characters into single spaces."""
    return _WHITESPACE_RE.sub(" ", text).strip()
