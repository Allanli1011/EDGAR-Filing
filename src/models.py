"""Data models for EDGAR filings and analysis results."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Filing:
    """Represents a single SEC EDGAR filing."""
    accession_no: str          # e.g. "0001234567-24-000001"
    form_type: str             # e.g. "8-K", "Form 4", "10-K"
    cik: str                   # Zero-padded CIK, e.g. "0000320193"
    company_name: str
    filed_at: str              # ISO datetime string from EDGAR
    period_of_report: Optional[str] = None
    filing_url: str = ""       # URL to the filing index
    document_url: str = ""     # URL to the primary document
    items: Optional[str] = None  # For 8-K: comma-separated item numbers
    size_bytes: int = 0

    @property
    def edgar_index_url(self) -> str:
        """Returns the EDGAR filing index URL."""
        accession_clean = self.accession_no.replace("-", "")
        return (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{self.cik.lstrip('0')}/{accession_clean}/"
        )

    @property
    def priority(self) -> int:
        """Returns processing priority (lower = higher priority)."""
        priority_map = {
            "4": 1,         # Form 4: insider transactions
            "8-K": 1,       # Current report: material events
            "SC 13D": 1,    # Large shareholder (activist)
            "SC 13G": 1,    # Large shareholder (passive)
            "SC 13D/A": 1,
            "SC 13G/A": 1,
            "10-K": 2,      # Annual report
            "10-Q": 2,      # Quarterly report
            "DEF 14A": 2,   # Proxy statement
            "13F-HR": 3,    # Institutional holdings
            "13F-HR/A": 3,
            "S-1": 3,       # IPO registration
            "S-1/A": 3,
            "424B4": 3,     # Final prospectus
            "SC TO-T": 4,   # Tender offer (target)
            "SC TO-I": 4,   # Tender offer (issuer)
            "15-12G": 4,    # Deregistration
            "NT 10-K": 4,   # Late filing notice (warning signal)
            "NT 10-Q": 4,
        }
        return priority_map.get(self.form_type, 5)


@dataclass
class AnalysisResult:
    """Structured output from Claude's analysis of a filing."""
    filing: Filing
    summary: str
    investment_signals: list[str] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    sentiment: str = "neutral"   # bullish / bearish / neutral / watch
    confidence: str = "medium"   # high / medium / low
    key_metrics: dict = field(default_factory=dict)
    analyzed_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
    model_used: str = "claude-opus-4-6"
    tokens_used: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "accession_no": self.filing.accession_no,
            "form_type": self.filing.form_type,
            "company_name": self.filing.company_name,
            "cik": self.filing.cik,
            "filed_at": self.filing.filed_at,
            "filing_url": self.filing.filing_url,
            "summary": self.summary,
            "investment_signals": self.investment_signals,
            "risk_factors": self.risk_factors,
            "action_items": self.action_items,
            "sentiment": self.sentiment,
            "confidence": self.confidence,
            "key_metrics": self.key_metrics,
            "analyzed_at": self.analyzed_at,
            "model_used": self.model_used,
            "tokens_used": self.tokens_used,
            "error": self.error,
        }
