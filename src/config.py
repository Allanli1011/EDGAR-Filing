"""
Configuration for the EDGAR Monitor.

Values are loaded from environment variables (via .env) with sensible defaults.
All credential/key values must come from the environment — never hardcoded.
"""

import os
from dataclasses import dataclass, field


# All high-priority form types recommended for US stock investment monitoring
DEFAULT_FORM_TYPES: list[str] = [
    # Priority 1 — real-time signals (most time-sensitive)
    "4",            # Insider transactions (Form 4)
    "8-K",          # Current report: earnings, M&A, CEO changes, etc.
    "SC 13D",       # Activist investor ≥5% stake
    "SC 13D/A",     # Amendment to SC 13D
    "SC 13G",       # Passive investor ≥5% stake
    "SC 13G/A",     # Amendment to SC 13G

    # Priority 2 — fundamental analysis
    "10-K",         # Annual report
    "10-K/A",       # Amended annual report
    "10-Q",         # Quarterly report
    "10-Q/A",       # Amended quarterly report
    "DEF 14A",      # Proxy statement (governance, exec comp)

    # Priority 3 — market structure
    "13F-HR",       # Institutional holdings (quarterly, 45-day lag)
    "13F-HR/A",     # Amended 13F
    "S-1",          # IPO registration
    "S-1/A",        # IPO amendment
    "424B4",        # Final IPO prospectus

    # Priority 4 — special events
    "SC TO-T",      # Tender offer (third party targeting company)
    "SC TO-I",      # Tender offer (issuer self-tender / buyback)
    "15-12G",       # Deregistration (going private)
    "NT 10-K",      # Late annual report (warning signal)
    "NT 10-Q",      # Late quarterly report (warning signal)
]


@dataclass
class MonitorConfig:
    """Runtime configuration for DailyMonitor."""

    # --- EDGAR settings ---
    edgar_user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "EDGAR_USER_AGENT", "EDGAR-Monitor contact@example.com"
        )
    )
    form_types: list[str] = field(default_factory=lambda: DEFAULT_FORM_TYPES)
    days_back: int = field(
        default_factory=lambda: int(os.environ.get("EDGAR_DAYS_BACK", "1"))
    )
    max_filings_per_form: int = field(
        default_factory=lambda: int(os.environ.get("EDGAR_MAX_PER_FORM", "40"))
    )

    # --- LLM backend selector ---
    llm_backend: str = field(
        default_factory=lambda: os.environ.get("LLM_BACKEND", "claude")
    )

    # --- Claude / Anthropic settings (used when llm_backend=claude) ---
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )

    # --- OpenClaw settings (used when llm_backend=openclaw) ---
    # Model ID must match the "id" field in openclaw.json models.providers
    openclaw_model_id: str = field(
        default_factory=lambda: os.environ.get("OPENCLAW_MODEL_ID", "")
    )

    # --- Analysis tuning ---
    max_analysis_tokens: int = field(
        default_factory=lambda: int(os.environ.get("MAX_ANALYSIS_TOKENS", "4096"))
    )
    analysis_delay_secs: float = field(
        default_factory=lambda: float(os.environ.get("ANALYSIS_DELAY_SECS", "0.5"))
    )

    # --- Filing text settings ---
    fetch_filing_text: bool = field(
        default_factory=lambda: os.environ.get("FETCH_FILING_TEXT", "true").lower()
        not in ("false", "0", "no")
    )
    max_text_chars: int = field(
        default_factory=lambda: int(os.environ.get("MAX_TEXT_CHARS", "50000"))
    )

    # --- Output settings ---
    output_dir: str = field(
        default_factory=lambda: os.environ.get("OUTPUT_DIR", "output")
    )

    def validate(self) -> None:
        """Raise ValueError if required configuration is missing."""
        backend = self.llm_backend.lower()
        if backend == "claude" and not self.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. "
                "Add it to your .env file, or set LLM_BACKEND=openclaw to use OpenClaw."
            )
        if backend == "openclaw" and not self.openclaw_model_id:
            raise ValueError(
                "OPENCLAW_MODEL_ID is not set. "
                "Set it to the model 'id' from your openclaw.json, "
                "e.g. OPENCLAW_MODEL_ID=hf:zai-org/GLM-4.7"
            )
        if not self.edgar_user_agent or "example.com" in self.edgar_user_agent:
            import warnings
            warnings.warn(
                "EDGAR_USER_AGENT is using the default placeholder. "
                "Set a real name/email in your .env to comply with SEC policy.",
                UserWarning,
                stacklevel=2,
            )
