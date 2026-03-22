"""Tests for the FilingAnalyzer module."""

import json
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

import src.analyzer as analyzer_module
from src.analyzer import FilingAnalyzer


class _AnthropicShim:
    Anthropic = None


analyzer_module.anthropic = _AnthropicShim()
from src.models import Filing, AnalysisResult


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_filing(form_type: str = "8-K", company: str = "Test Corp") -> Filing:
    return Filing(
        accession_no="0001234567-24-000001",
        form_type=form_type,
        cik="0000320193",
        company_name=company,
        filed_at="2024-01-15",
        period_of_report="2024-01-15",
        items="2.02",
        filing_url="https://www.sec.gov/Archives/edgar/data/320193/000123/",
    )


SAMPLE_CLAUDE_RESPONSE = json.dumps({
    "summary": "Apple reported record Q1 revenue of $119.6B, beating estimates.",
    "investment_signals": [
        "Revenue beat by 3% vs. consensus",
        "Services segment grew 11% YoY to $23.1B",
    ],
    "risk_factors": [
        "China revenue declined 13% YoY",
        "Gross margin compression in hardware",
    ],
    "action_items": ["Research further before adding to position"],
    "sentiment": "bullish",
    "confidence": "high",
    "key_metrics": {
        "revenue": "$119.6B",
        "eps": "$2.18",
        "guidance_revenue": "$90-96B",
    },
})


# ── Tests: _normalize_form_key ─────────────────────────────────────────────────

class TestNormalizeFormKey:
    def setup_method(self):
        self.analyzer = FilingAnalyzer.__new__(FilingAnalyzer)

    def test_amendment_maps_to_base(self):
        assert self.analyzer._normalize_form_key("SC 13D/A") == "SC 13D"
        assert self.analyzer._normalize_form_key("10-K/A") == "10-K"
        assert self.analyzer._normalize_form_key("4/A") == "4"

    def test_passthrough_for_known_forms(self):
        assert self.analyzer._normalize_form_key("8-K") == "8-K"
        assert self.analyzer._normalize_form_key("DEF 14A") == "DEF 14A"

    def test_unknown_form_passes_through(self):
        assert self.analyzer._normalize_form_key("NSAR-A") == "NSAR-A"


# ── Tests: _parse_response ─────────────────────────────────────────────────────

class TestParseResponse:
    def setup_method(self):
        self.analyzer = FilingAnalyzer.__new__(FilingAnalyzer)

    def test_parses_clean_json(self):
        result = self.analyzer._parse_response(SAMPLE_CLAUDE_RESPONSE)
        assert result["sentiment"] == "bullish"
        assert result["confidence"] == "high"
        assert len(result["investment_signals"]) == 2

    def test_parses_json_with_code_fence(self):
        fenced = f"```json\n{SAMPLE_CLAUDE_RESPONSE}\n```"
        result = self.analyzer._parse_response(fenced)
        assert result["sentiment"] == "bullish"

    def test_parses_json_with_plain_code_fence(self):
        fenced = f"```\n{SAMPLE_CLAUDE_RESPONSE}\n```"
        result = self.analyzer._parse_response(fenced)
        assert result["sentiment"] == "bullish"

    def test_extracts_json_from_surrounding_text(self):
        wrapped = f"Here is the analysis:\n{SAMPLE_CLAUDE_RESPONSE}\nEnd of analysis."
        result = self.analyzer._parse_response(wrapped)
        assert result["sentiment"] == "bullish"

    def test_invalid_json_returns_fallback(self):
        result = self.analyzer._parse_response("not valid json at all")
        assert result["sentiment"] == "neutral"
        assert result["confidence"] == "low"
        assert "not valid json" in result["summary"]


# ── Tests: analyze (mocked Claude API) ────────────────────────────────────────

class TestAnalyze:
    def _make_mock_response(self, text: str, input_tokens=500, output_tokens=300):
        """Build a mock anthropic Message response."""
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(type="text", text=text)]
        mock_resp.usage = MagicMock(
            input_tokens=input_tokens, output_tokens=output_tokens
        )
        return mock_resp

    @patch("src.analyzer.anthropic.Anthropic")
    def test_successful_analysis(self, MockAnthropic):
        mock_client = MagicMock()
        MockAnthropic.return_value = mock_client

        # Mock streaming context manager
        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.get_final_message.return_value = self._make_mock_response(
            SAMPLE_CLAUDE_RESPONSE
        )
        mock_client.messages.stream.return_value = mock_stream

        analyzer = FilingAnalyzer(api_key="test-key")
        filing = make_filing("8-K")
        result = analyzer.analyze(filing, filing_text="Revenue was $119.6B")

        assert isinstance(result, AnalysisResult)
        assert result.sentiment == "bullish"
        assert result.confidence == "high"
        assert result.error is None
        assert result.tokens_used == 800  # 500 + 300

    @patch("src.analyzer.anthropic.Anthropic")
    def test_api_error_returns_error_result(self, MockAnthropic):
        import anthropic as anthropic_lib

        mock_client = MagicMock()
        MockAnthropic.return_value = mock_client

        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.get_final_message.side_effect = anthropic_lib.APIError(
            message="Server error", request=MagicMock(), body={}
        )
        mock_client.messages.stream.return_value = mock_stream

        analyzer = FilingAnalyzer(api_key="test-key")
        filing = make_filing("8-K")
        result = analyzer.analyze(filing, filing_text="")

        assert result.error is not None
        assert "Server error" in result.error or "API error" in result.error

    @patch("src.analyzer.anthropic.Anthropic")
    def test_form4_uses_correct_prompt(self, MockAnthropic):
        mock_client = MagicMock()
        MockAnthropic.return_value = mock_client

        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.get_final_message.return_value = self._make_mock_response(
            json.dumps({
                "summary": "CEO bought shares",
                "investment_signals": ["Open-market purchase by CEO"],
                "risk_factors": [],
                "action_items": ["Monitor"],
                "sentiment": "bullish",
                "confidence": "medium",
                "key_metrics": {},
            })
        )
        mock_client.messages.stream.return_value = mock_stream

        analyzer = FilingAnalyzer(api_key="test-key")
        filing = make_filing("4")
        result = analyzer.analyze(filing, filing_text="CEO bought 10,000 shares")

        # Verify user message contained Form 4 specific instructions
        call_kwargs = mock_client.messages.stream.call_args.kwargs
        user_content = call_kwargs["messages"][0]["content"]
        assert "insider transaction" in user_content.lower()
        assert result.sentiment == "bullish"


# ── Tests: _build_user_message ────────────────────────────────────────────────

class TestBuildUserMessage:
    def setup_method(self):
        self.analyzer = FilingAnalyzer.__new__(FilingAnalyzer)

    def test_includes_filing_metadata(self):
        filing = make_filing("10-K", "Alphabet Inc.")
        msg = self.analyzer._build_user_message(filing, "Annual report text here.", "Analyze this.")
        assert "Alphabet Inc." in msg
        assert "10-K" in msg
        assert "0000320193" in msg

    def test_truncates_long_filing_text(self):
        filing = make_filing()
        long_text = "x" * 100_000
        msg = self.analyzer._build_user_message(filing, long_text, "Instructions")
        # Should be truncated to max_chars
        assert len(msg) < 60_000 + 2000  # text limit + header overhead

    def test_handles_missing_filing_text(self):
        filing = make_filing()
        msg = self.analyzer._build_user_message(filing, "", "Instructions")
        assert "not available" in msg.lower()
