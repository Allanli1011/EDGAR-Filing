"""
Filing Analyzer — extracts investment-relevant insights from SEC EDGAR filings.

Supports two LLM backends, switchable via LLM_BACKEND env var:
  - "claude"   (default) : Anthropic Claude API (claude-opus-4-6, adaptive thinking)
  - "openclaw"           : OpenClaw gateway — OpenAI-compatible API at port 18789,
                           routes to whichever model is configured in openclaw.json

Set LLM_BACKEND=openclaw in your .env to use OpenClaw.
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

import anthropic
import openai

from .models import Filing, AnalysisResult

logger = logging.getLogger(__name__)

# ── Shared prompts (backend-agnostic) ─────────────────────────────────────────

_SYSTEM_PROMPT = """You are an expert securities analyst specializing in SEC EDGAR filings.
Your task is to analyze filings and extract actionable investment insights for US stock investors.

Always respond with valid JSON matching the schema provided in the user message.
Be concise, factual, and focus on material information that could affect stock price or investment thesis.
Never fabricate data — if information is not present in the filing, omit it."""

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


# ── Backend abstraction ────────────────────────────────────────────────────────

class _LLMBackend(ABC):
    """Abstract base for LLM inference backends."""

    @abstractmethod
    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int,
    ) -> tuple[str, int]:
        """
        Call the LLM and return (response_text, total_tokens_used).
        Raises on unrecoverable errors; callers handle retries/logging.
        """


class _ClaudeBackend(_LLMBackend):
    """Calls Anthropic Claude API with adaptive thinking + streaming."""

    MODEL = "claude-opus-4-6"

    def __init__(self, api_key: Optional[str] = None):
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(self, system: str, user: str, max_tokens: int) -> tuple[str, int]:
        with self._client.messages.stream(
            model=self.MODEL,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            response = stream.get_final_message()

        text = next(
            (b.text for b in response.content if b.type == "text"), ""
        )
        tokens = response.usage.input_tokens + response.usage.output_tokens
        return text, tokens


class _OpenClawBackend(_LLMBackend):
    """
    Reads a model config from ~/.openclaw/openclaw.json by model ID, then calls
    that provider's API directly — no separate API key or URL configuration needed.

    The model_id must match the "id" field of one of the models defined in
    openclaw.json's models.providers section (e.g. "hf:zai-org/GLM-4.7").

    Set OPENCLAW_MODEL_ID in .env to select which model to use.
    The config file path defaults to ~/.openclaw/openclaw.json (override with
    OPENCLAW_CONFIG_PATH or OPENCLAW_STATE_DIR).

    Supports all three API types openclaw.json uses:
      openai-completions / openai-responses  → openai SDK
      anthropic-messages                     → anthropic SDK
    """

    def __init__(self, model_id: Optional[str] = None):
        from .openclaw_config import load_model_config, get_default_model_id

        self._model_id = model_id or os.environ.get("OPENCLAW_MODEL_ID", "")
        if not self._model_id:
            # Fall back to agents.defaults.model.primary from openclaw.json
            self._model_id = get_default_model_id() or ""
            if self._model_id:
                logger.info("Using OpenClaw global default model: %s", self._model_id)
        if not self._model_id:
            raise ValueError(
                "No model selected. Set OPENCLAW_MODEL_ID in .env, "
                "or configure agents.defaults.model.primary in openclaw.json."
            )

        cfg = load_model_config(self._model_id)
        self._cfg = cfg
        self.model = cfg.model_id

        logger.info(
            "OpenClaw backend: provider=%s model=%s api_type=%s base_url=%s",
            cfg.provider_name, cfg.model_id, cfg.api_type, cfg.base_url,
        )

        # Build the right SDK client based on the provider's api type
        if cfg.api_type == "anthropic-messages":
            self._call = self._call_anthropic
            self._anthropic_client = anthropic.Anthropic(api_key=cfg.api_key)
        else:
            # openai-completions or openai-responses both use the OpenAI SDK
            self._call = self._call_openai
            self._openai_client = openai.OpenAI(
                api_key=cfg.api_key or "openclaw",
                base_url=cfg.base_url,
            )

    def complete(self, system: str, user: str, max_tokens: int) -> tuple[str, int]:
        return self._call(system, user, max_tokens)

    def _call_openai(self, system: str, user: str, max_tokens: int) -> tuple[str, int]:
        response = self._openai_client.chat.completions.create(
            model=self._cfg.model_id,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = response.choices[0].message.content or ""
        tokens = 0
        if response.usage:
            tokens = response.usage.prompt_tokens + response.usage.completion_tokens
        return text, tokens

    def _call_anthropic(self, system: str, user: str, max_tokens: int) -> tuple[str, int]:
        """Used when the openclaw provider uses anthropic-messages api type."""
        client = self._anthropic_client
        # Point the anthropic client at the provider's base_url
        # (strip /v1 suffix if present — anthropic SDK adds its own path)
        base = self._cfg.base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        client.base_url = base  # type: ignore[attr-defined]

        with client.messages.stream(
            model=self._cfg.model_id,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            response = stream.get_final_message()

        text = next((b.text for b in response.content if b.type == "text"), "")
        tokens = response.usage.input_tokens + response.usage.output_tokens
        return text, tokens


def _build_backend(
    backend_name: str,
    anthropic_api_key: Optional[str] = None,
    openclaw_model_id: Optional[str] = None,
) -> _LLMBackend:
    """Factory: returns the right backend based on backend_name."""
    name = backend_name.lower().strip()
    if name == "openclaw":
        return _OpenClawBackend(model_id=openclaw_model_id)
    if name == "claude":
        return _ClaudeBackend(api_key=anthropic_api_key)
    raise ValueError(
        f"Unknown LLM_BACKEND '{backend_name}'. Valid values: claude, openclaw"
    )


# ── Public analyzer ────────────────────────────────────────────────────────────

class FilingAnalyzer:
    """
    Analyzes SEC filings and returns structured investment insights.

    Backend is selected at construction time via the `backend` argument
    or the LLM_BACKEND environment variable ("claude" | "openclaw").
    """

    def __init__(
        self,
        # Claude backend settings
        api_key: Optional[str] = None,
        # OpenClaw backend settings
        openclaw_model_id: Optional[str] = None,
        # Backend selector: "claude" | "openclaw" (reads LLM_BACKEND env if None)
        backend: Optional[str] = None,
    ):
        backend_name = backend or os.environ.get("LLM_BACKEND", "claude")
        self._backend = _build_backend(
            backend_name,
            anthropic_api_key=api_key,
            openclaw_model_id=openclaw_model_id,
        )
        self._backend_name = backend_name.lower()
        logger.info("FilingAnalyzer using backend: %s", self._backend_name)

    @property
    def backend_name(self) -> str:
        return self._backend_name

    def analyze(
        self,
        filing: Filing,
        filing_text: str,
        max_tokens: int = 4096,
    ) -> AnalysisResult:
        """Analyze a filing and return structured investment insights."""
        form_key = self._normalize_form_key(filing.form_type)
        form_prompt = _FORM_PROMPTS.get(form_key, _FORM_PROMPTS.get("8-K", ""))
        user_message = self._build_user_message(filing, filing_text, form_prompt)

        logger.info(
            "Analyzing %s (%s) for %s [backend=%s]",
            filing.form_type,
            filing.accession_no,
            filing.company_name,
            self._backend_name,
        )

        try:
            raw_text, tokens_used = self._backend.complete(
                system=_SYSTEM_PROMPT,
                user=user_message,
                max_tokens=max_tokens,
            )
        except anthropic.RateLimitError as exc:
            logger.error("Rate limit hit: %s", exc)
            return self._error_result(filing, f"Rate limit: {exc}")
        except anthropic.APIError as exc:
            logger.error("Anthropic API error: %s", exc)
            return self._error_result(filing, f"API error: {exc}")
        except openai.RateLimitError as exc:
            logger.error("OpenClaw rate limit hit: %s", exc)
            return self._error_result(filing, f"Rate limit: {exc}")
        except openai.APIError as exc:
            logger.error("OpenClaw API error: %s", exc)
            return self._error_result(filing, f"OpenClaw API error: {exc}")
        except Exception as exc:
            logger.error("Unexpected error: %s", exc, exc_info=True)
            return self._error_result(filing, str(exc))

        parsed = self._parse_response(raw_text)
        model_label = (
            _ClaudeBackend.MODEL
            if self._backend_name == "claude"
            else getattr(self._backend, "model", self._backend_name)
        )
        logger.info(
            "  → %s | %s | tokens: %d",
            parsed.get("sentiment", "?").upper(),
            (parsed.get("summary") or "")[:80],
            tokens_used,
        )

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
            model_used=model_label,
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

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
        mapping = {
            "SC 13D/A": "SC 13D",
            "SC 13G/A": "SC 13G",
            "13F-HR/A": "13F-HR",
            "S-1/A": "S-1",
            "10-K/A": "10-K",
            "10-Q/A": "10-Q",
            "4/A": "4",
        }
        return mapping.get(form_type, form_type)

    def _parse_response(self, raw_text: str) -> dict:
        """Extract JSON from LLM response text."""
        raw_text = raw_text.strip()

        # Strip markdown code fences
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw_text = "\n".join(lines)

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
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
