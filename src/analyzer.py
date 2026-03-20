"""
Filing Analyzer — uses Claude to extract investment-relevant insights
from SEC EDGAR filings.

Uses claude-opus-4-6 with adaptive thinking and streaming for long documents.
"""

import json
import logging
from typing import Optional

import anthropic

from .models import Filing, AnalysisResult

logger = logging.getLogger(__name__)

# System prompt shared across all form types
_SYSTEM_PROMPT = """You are an expert securities analyst specializing in SEC EDGAR filings.
Your task is to analyze filings and extract actionable investment insights for US stock investors.

Always respond with valid JSON matching the schema provided in the user message.
Be concise, factual, and focus on material information that could affect stock price or investment thesis.
Never fabricate data — if information is not present in the filing, omit it."""

# Per-form-type analysis instructions
_FORM_PROMPTS: dict[str, str] = {
    "4": """Analyze this Form 4 (insider transaction) filing.

Focus on:
- Transaction type (purchase/sale/gift/exercise)
- Dollar amount and share count
- Whether this is open-market purchase (most bullish) vs. exercise of options
- Pattern context: first purchase? Unusual size? Part of a 10b5-1 plan?
- Role of the insider (CEO/CFO/Director/10% holder — rank significance)

Bearish signals: large open-market sales by multiple insiders, CEO selling after poor guidance.
Bullish signals: open-market purchases, especially by CEO/CFO during market weakness.""",

    "8-K": """Analyze this Form 8-K (current report) filing.

Identify the Item number(s) and focus on:
- Item 1.01: Material agreements — is this expansion or risk?
- Item 1.03: Bankruptcy — immediate sell signal
- Item 2.02: Earnings results — beat/miss vs. expectations, guidance
- Item 2.06: Impairment — how material to balance sheet?
- Item 5.02: Management changes — CEO departure is high risk; activist replacement?
- Item 7.01: Reg FD / forward guidance
- Item 9.01: Financial exhibits attached

For earnings (2.02): extract revenue, EPS, and any guidance metrics if present.""",

    "10-K": """Analyze this Form 10-K (annual report) filing.

Focus on the most material sections:
1. Business overview and competitive position changes from prior year
2. Revenue trend and profitability (gross margin, operating margin)
3. Key risk factors that are NEW or escalated this year
4. Management's Discussion: forward-looking signals, capex plans
5. Balance sheet health: debt levels, cash position, FCF
6. Auditor opinion — any going concern or restatement red flags

Extract key financial metrics if visible in the text.""",

    "10-Q": """Analyze this Form 10-Q (quarterly report) filing.

Focus on:
1. Quarter-over-quarter and year-over-year revenue/profit trends
2. Any new or escalated risk factors vs. prior quarter
3. Management guidance or forward-looking commentary
4. Balance sheet changes: debt, cash, working capital
5. Any unusual items: restructuring charges, write-downs, one-time gains
6. Legal proceedings or contingent liabilities""",

    "DEF 14A": """Analyze this DEF 14A (proxy statement) filing.

Focus on:
1. Executive compensation structure — is it aligned with shareholder returns?
2. CEO pay ratio and total compensation level vs. peers
3. Shareholder proposals — any activist items? Say-on-pay vote outcome?
4. Board composition changes — independence, diversity, expertise
5. Related-party transactions — any conflicts of interest?
6. Capital allocation proposals: buybacks, dividends, equity issuance""",

    "SC 13D": """Analyze this SC 13D (activist shareholder) filing.

This is a 5%+ ownership disclosure by an activist investor. Focus on:
1. Who is the filer? (Identify hedge fund / activist track record)
2. What percentage stake and at what average cost?
3. Stated purpose: passive investment vs. active engagement?
4. Any disclosed intentions: board seats, M&A, divestitures, buybacks?
5. Historical outcome when this activist takes a similar stake?

SC 13D (vs. 13G) signals active intent — treat as high-priority.""",

    "SC 13G": """Analyze this SC 13G (passive large shareholder) filing.

This is a 5%+ ownership disclosure by a passive investor. Focus on:
1. Who is the filer? Institutional investor, index fund, or hedge fund?
2. Percentage stake — initial filing or amendment showing increase/decrease?
3. If amendment: is the stake growing (bullish conviction) or shrinking?
4. Any known investment thesis for this fund in this sector?""",

    "13F-HR": """Analyze this Form 13F (institutional holdings) filing.

Focus on significant position changes from prior quarter:
1. New positions initiated (high conviction buys)
2. Positions fully exited (potential red flag or sector rotation)
3. Largest increases as % of portfolio (conviction adds)
4. Largest decreases as % of portfolio (loss of conviction)
5. Notable concentrated bets (>5% of portfolio in one name)

Context: 13F is filed 45 days after quarter-end — positions may have changed.""",

    "S-1": """Analyze this S-1 (IPO registration) filing.

Focus on:
1. Business model and revenue quality (recurring vs. one-time)
2. Growth rate and path to profitability
3. Unit economics: CAC, LTV, gross margin
4. Key risks: competition, regulatory, customer concentration
5. Use of proceeds — growth investment vs. insider liquidity?
6. Cap table: insider ownership post-IPO, lockup periods
7. Comparable public companies and implied valuation multiple""",

    "NT 10-K": """Analyze this NT 10-K (late annual report filing) notification.

This is a warning signal. Focus on:
1. Stated reason for the delay — is it a red flag (restatement, audit issues)?
2. Has the company filed NT forms before?
3. Timeline: how long is the extension requested?
4. Any hints of material changes or restatements in the notification?

A late filing due to "complex accounting" or "audit review" is a serious red flag.""",

    "NT 10-Q": """Analyze this NT 10-Q (late quarterly report filing) notification.

Similar to NT 10-K but quarterly. Focus on the stated reason for delay and
any indication of restatement or going-concern issues.""",
}

# JSON output schema for all analyses
_OUTPUT_SCHEMA = """{
    "summary": "2-3 sentence plain-English summary of the filing",
    "investment_signals": ["bullish/bearish signal 1", "signal 2", ...],
    "risk_factors": ["key risk 1", "key risk 2", ...],
    "action_items": ["suggested action for investor: monitor/research/buy/sell/avoid"],
    "sentiment": "bullish | bearish | neutral | watch",
    "confidence": "high | medium | low",
    "key_metrics": {
        "metric_name": "value",
        "...": "..."
    }
}"""


class FilingAnalyzer:
    """Analyzes SEC filings using Claude API."""

    def __init__(self, api_key: Optional[str] = None):
        self.client = anthropic.Anthropic(api_key=api_key)  # uses ANTHROPIC_API_KEY if None

    def analyze(
        self,
        filing: Filing,
        filing_text: str,
        max_tokens: int = 4096,
    ) -> AnalysisResult:
        """
        Analyze a filing and return structured investment insights.

        Uses streaming with get_final_message() to handle long responses
        and avoid HTTP timeouts.
        """
        form_key = self._normalize_form_key(filing.form_type)
        form_prompt = _FORM_PROMPTS.get(form_key, _FORM_PROMPTS.get("8-K", ""))

        user_message = self._build_user_message(filing, filing_text, form_prompt)

        try:
            logger.info(
                "Analyzing %s (%s) for %s",
                filing.form_type,
                filing.accession_no,
                filing.company_name,
            )

            with self.client.messages.stream(
                model="claude-opus-4-6",
                max_tokens=max_tokens,
                thinking={"type": "adaptive"},
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            ) as stream:
                response = stream.get_final_message()

            raw_text = ""
            for block in response.content:
                if block.type == "text":
                    raw_text = block.text
                    break

            parsed = self._parse_response(raw_text)
            tokens_used = response.usage.input_tokens + response.usage.output_tokens

            return AnalysisResult(
                filing=filing,
                summary=parsed.get("summary", ""),
                investment_signals=parsed.get("investment_signals", []),
                risk_factors=parsed.get("risk_factors", []),
                action_items=parsed.get("action_items", []),
                sentiment=parsed.get("sentiment", "neutral"),
                confidence=parsed.get("confidence", "medium"),
                key_metrics=parsed.get("key_metrics", {}),
                tokens_used=tokens_used,
            )

        except anthropic.RateLimitError as exc:
            logger.error("Rate limit hit analyzing %s: %s", filing.accession_no, exc)
            return self._error_result(filing, f"Rate limit: {exc}")
        except anthropic.APIError as exc:
            logger.error("API error analyzing %s: %s", filing.accession_no, exc)
            return self._error_result(filing, f"API error: {exc}")
        except Exception as exc:
            logger.error("Unexpected error analyzing %s: %s", filing.accession_no, exc, exc_info=True)
            return self._error_result(filing, str(exc))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_user_message(
        self, filing: Filing, filing_text: str, form_instructions: str
    ) -> str:
        header = (
            f"**Filing Details**\n"
            f"- Company: {filing.company_name}\n"
            f"- CIK: {filing.cik}\n"
            f"- Form Type: {filing.form_type}\n"
            f"- Filed: {filing.filed_at}\n"
            f"- Period: {filing.period_of_report or 'N/A'}\n"
        )
        if filing.items:
            header += f"- 8-K Items: {filing.items}\n"

        text_section = (
            f"\n**Filing Text (truncated to 50,000 chars)**\n"
            f"```\n{filing_text[:50_000]}\n```\n"
            if filing_text
            else "\n[Filing text not available — analyze based on metadata only]\n"
        )

        instructions = (
            f"\n**Analysis Instructions**\n{form_instructions}\n"
            f"\n**Required Output Format** (respond ONLY with valid JSON):\n"
            f"{_OUTPUT_SCHEMA}"
        )

        return header + text_section + instructions

    def _normalize_form_key(self, form_type: str) -> str:
        """Map form type variants to a canonical key."""
        mapping = {
            "SC 13D/A": "SC 13D",
            "SC 13G/A": "SC 13G",
            "13F-HR/A": "13F-HR",
            "S-1/A": "S-1",
            "10-K/A": "10-K",
            "10-Q/A": "10-Q",
            "4/A": "4",
            "NT 10-K": "NT 10-K",
            "NT 10-Q": "NT 10-Q",
        }
        return mapping.get(form_type, form_type)

    def _parse_response(self, raw_text: str) -> dict:
        """Extract JSON from Claude's response text."""
        raw_text = raw_text.strip()

        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            # Remove first and last line if they're fences
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw_text = "\n".join(lines)

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            # Try to find JSON object within text
            start = raw_text.find("{")
            end = raw_text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(raw_text[start:end])
                except json.JSONDecodeError:
                    pass

        logger.warning("Could not parse JSON from response: %s...", raw_text[:200])
        return {
            "summary": raw_text[:500],
            "investment_signals": [],
            "risk_factors": [],
            "action_items": [],
            "sentiment": "neutral",
            "confidence": "low",
            "key_metrics": {},
        }

    @staticmethod
    def _error_result(filing: Filing, error_msg: str) -> AnalysisResult:
        return AnalysisResult(
            filing=filing,
            summary="Analysis failed.",
            error=error_msg,
        )
