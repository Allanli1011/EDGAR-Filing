"""Tests for the EDGAR collector module."""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import responses as rsps

from src.collector import EDGARCollector, _strip_html, _normalize_whitespace
from src.models import Filing


# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_EFTS_RESPONSE = {
    "hits": {
        "total": {"value": 2},
        "hits": [
            {
                "_source": {
                    "accession_no": "0001234567-24-000001",
                    "entity_id": "320193",
                    "display_names": [{"name": "Apple Inc."}],
                    "form_type": "8-K",
                    "file_date": "2024-01-15",
                    "period_of_report": "2024-01-15",
                    "items": "2.02",
                }
            },
            {
                "_source": {
                    "accession_no": "0009876543-24-000002",
                    "entity_id": "789012",
                    "display_names": [{"name": "Microsoft Corp."}],
                    "form_type": "8-K",
                    "file_date": "2024-01-15",
                    "period_of_report": "2024-01-15",
                    "items": "5.02",
                }
            },
        ],
    }
}


# ── Tests: _parse_hit ─────────────────────────────────────────────────────────

class TestParseHit:
    def setup_method(self):
        self.collector = EDGARCollector(user_agent="Test test@test.com")

    def test_basic_parsing(self):
        src = SAMPLE_EFTS_RESPONSE["hits"]["hits"][0]["_source"]
        filing = self.collector._parse_hit(src, "8-K")

        assert filing is not None
        assert filing.accession_no == "0001234567-24-000001"
        assert filing.cik == "0000320193"
        assert filing.company_name == "Apple Inc."
        assert filing.form_type == "8-K"
        assert filing.filed_at == "2024-01-15"
        assert filing.items == "2.02"

    def test_cik_zero_padding(self):
        src = {"accession_no": "0001-24-001", "entity_id": "1", "form_type": "4",
               "file_date": "2024-01-01", "display_names": [{"name": "Tiny Co"}]}
        filing = self.collector._parse_hit(src, "4")
        assert filing.cik == "0000000001"

    def test_missing_accession_returns_none(self):
        src = {"entity_id": "123", "form_type": "8-K", "file_date": "2024-01-01"}
        filing = self.collector._parse_hit(src, "8-K")
        assert filing is None

    def test_filing_url_construction(self):
        src = SAMPLE_EFTS_RESPONSE["hits"]["hits"][0]["_source"]
        filing = self.collector._parse_hit(src, "8-K")
        assert "320193" in filing.filing_url
        assert "Archives/edgar/data" in filing.filing_url


# ── Tests: Filing.priority ─────────────────────────────────────────────────────

class TestFilingPriority:
    def _make_filing(self, form_type: str) -> Filing:
        return Filing(
            accession_no="0001-24-001",
            form_type=form_type,
            cik="0000000001",
            company_name="Test Co",
            filed_at="2024-01-01",
        )

    def test_form4_is_priority1(self):
        assert self._make_filing("4").priority == 1

    def test_8k_is_priority1(self):
        assert self._make_filing("8-K").priority == 1

    def test_10k_is_priority2(self):
        assert self._make_filing("10-K").priority == 2

    def test_13f_is_priority3(self):
        assert self._make_filing("13F-HR").priority == 3

    def test_nt10k_is_priority4(self):
        assert self._make_filing("NT 10-K").priority == 4

    def test_unknown_form_is_priority5(self):
        assert self._make_filing("NSAR-A").priority == 5


# ── Tests: text utilities ─────────────────────────────────────────────────────

class TestTextUtilities:
    def test_strip_html_basic(self):
        html = "<html><body><p>Hello <b>World</b></p></body></html>"
        result = _strip_html(html)
        assert "<" not in result
        assert "Hello" in result
        assert "World" in result

    def test_normalize_whitespace(self):
        text = "Hello   World\t\t\nFoo"
        result = _normalize_whitespace(text)
        assert "  " not in result
        assert result.startswith("Hello")

    def test_strip_html_preserves_text(self):
        html = "<div>Revenue: $1.2B</div><p>Net Income: $300M</p>"
        result = _strip_html(html)
        assert "Revenue" in result
        assert "$1.2B" in result


# ── Tests: fetch_recent_filings (mocked HTTP) ─────────────────────────────────

class TestFetchRecentFilings:
    @rsps.activate
    def test_returns_filings_for_date(self):
        rsps.add(
            rsps.GET,
            "https://efts.sec.gov/LATEST/search-index",
            json=SAMPLE_EFTS_RESPONSE,
            status=200,
        )

        collector = EDGARCollector(user_agent="Test test@test.com")
        filings = collector.fetch_recent_filings(
            form_types=["8-K"],
            target_date=date(2024, 1, 15),
        )

        assert len(filings) == 2
        assert filings[0].form_type == "8-K"

    @rsps.activate
    def test_handles_empty_results(self):
        empty = {"hits": {"total": {"value": 0}, "hits": []}}
        rsps.add(
            rsps.GET,
            "https://efts.sec.gov/LATEST/search-index",
            json=empty,
            status=200,
        )

        collector = EDGARCollector(user_agent="Test test@test.com")
        filings = collector.fetch_recent_filings(
            form_types=["8-K"],
            target_date=date(2024, 1, 15),
        )
        assert filings == []

    @rsps.activate
    def test_http_error_does_not_raise(self):
        rsps.add(
            rsps.GET,
            "https://efts.sec.gov/LATEST/search-index",
            status=503,
        )

        collector = EDGARCollector(user_agent="Test test@test.com")
        # Should log error and return empty list, not raise
        filings = collector.fetch_recent_filings(
            form_types=["8-K"],
            target_date=date(2024, 1, 15),
        )
        assert filings == []
