"""Tests for data models."""

from src.models import Filing, AnalysisResult


class TestFiling:
    def _make(self, **kwargs) -> Filing:
        defaults = {
            "accession_no": "0001234567-24-000001",
            "form_type": "8-K",
            "cik": "0000320193",
            "company_name": "Apple Inc.",
            "filed_at": "2024-01-15",
        }
        defaults.update(kwargs)
        return Filing(**defaults)

    def test_edgar_index_url(self):
        f = self._make(cik="0000320193", accession_no="0001234567-24-000001")
        url = f.edgar_index_url
        assert "320193" in url
        assert "000123456724000001" in url
        assert url.startswith("https://www.sec.gov/Archives/edgar/data/")

    def test_priority_ordering(self):
        p1 = self._make(form_type="4").priority
        p2 = self._make(form_type="10-K").priority
        p3 = self._make(form_type="13F-HR").priority
        p4 = self._make(form_type="NT 10-K").priority
        assert p1 < p2 < p3 < p4


class TestAnalysisResult:
    def test_to_dict_includes_filing_fields(self):
        filing = Filing(
            accession_no="0001-24-001",
            form_type="8-K",
            cik="0000000001",
            company_name="Tiny Co",
            filed_at="2024-01-01",
        )
        result = AnalysisResult(
            filing=filing,
            summary="Test summary",
            sentiment="bullish",
            confidence="high",
        )
        d = result.to_dict()
        assert d["company_name"] == "Tiny Co"
        assert d["form_type"] == "8-K"
        assert d["summary"] == "Test summary"
        assert d["sentiment"] == "bullish"
        assert d["error"] is None

    def test_to_dict_with_error(self):
        filing = Filing(
            accession_no="0001-24-001",
            form_type="8-K",
            cik="0000000001",
            company_name="Tiny Co",
            filed_at="2024-01-01",
        )
        result = AnalysisResult(
            filing=filing,
            summary="Analysis failed.",
            error="Rate limit exceeded",
        )
        d = result.to_dict()
        assert d["error"] == "Rate limit exceeded"
