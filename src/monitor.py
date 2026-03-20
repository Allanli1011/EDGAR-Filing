"""
Daily Monitor — orchestrates the EDGAR filing collection and analysis pipeline.

Usage:
    python run_monitor.py                    # run for today
    python run_monitor.py --date 2024-12-01  # run for a specific date
    python run_monitor.py --dry-run          # fetch filings but skip analysis
    python run_monitor.py --forms 8-K 4      # override form types
"""

import logging
import time
from datetime import date, datetime
from typing import Optional

from .analyzer import FilingAnalyzer
from .collector import EDGARCollector
from .config import MonitorConfig
from .models import Filing
from .storage import ResultStorage

logger = logging.getLogger(__name__)


class DailyMonitor:
    """
    Orchestrates daily EDGAR filing monitoring and analysis.

    Processing order (lower priority number = processed first):
      Priority 1: Form 4, 8-K, SC 13D/G   — real-time signals
      Priority 2: 10-K, 10-Q, DEF 14A     — fundamental analysis
      Priority 3: 13F, S-1, 424B4         — market structure
      Priority 4: SC TO-T/I, NT 10-K/Q   — special events
    """

    def __init__(self, config: MonitorConfig):
        self.config = config
        self.collector = EDGARCollector(user_agent=config.edgar_user_agent)
        self.analyzer = FilingAnalyzer(api_key=config.anthropic_api_key)
        self.storage = ResultStorage(output_dir=config.output_dir)

    def run(
        self,
        target_date: Optional[date] = None,
        dry_run: bool = False,
        form_types: Optional[list[str]] = None,
        max_filings: Optional[int] = None,
    ) -> dict:
        """
        Run the full monitoring pipeline for a given date.

        Args:
            target_date: Date to monitor (defaults to today).
            dry_run: If True, fetch filings but skip Claude analysis.
            form_types: Override the default form type list from config.
            max_filings: Cap total filings analyzed (useful for testing).

        Returns:
            Summary dict with counts and saved file paths.
        """
        if target_date is None:
            target_date = date.today()

        forms = form_types or self.config.form_types
        logger.info(
            "=== EDGAR Monitor starting for %s | forms: %s ===",
            target_date,
            ", ".join(forms),
        )

        # --- Step 1: Collect filings ---
        start_time = time.time()
        filings = self.collector.fetch_recent_filings(
            form_types=forms,
            target_date=target_date,
            days_back=self.config.days_back,
            max_per_form=self.config.max_filings_per_form,
        )

        logger.info("Collected %d filings in %.1fs", len(filings), time.time() - start_time)

        if max_filings:
            filings = filings[:max_filings]
            logger.info("Capped to %d filings (--max-filings)", max_filings)

        if dry_run:
            logger.info("[DRY RUN] Skipping analysis.")
            return {
                "date": str(target_date),
                "filings_found": len(filings),
                "filings_analyzed": 0,
                "dry_run": True,
                "filings": [
                    {
                        "form_type": f.form_type,
                        "company": f.company_name,
                        "filed_at": f.filed_at,
                        "accession_no": f.accession_no,
                    }
                    for f in filings
                ],
            }

        # --- Step 2: Analyze each filing ---
        results = []
        errors = 0
        start_time = time.time()

        for i, filing in enumerate(filings, 1):
            logger.info(
                "[%d/%d] %s | %s | %s",
                i,
                len(filings),
                filing.form_type,
                filing.company_name,
                filing.accession_no,
            )

            # Fetch filing text
            filing_text = ""
            if self.config.fetch_filing_text:
                filing_text = self.collector.fetch_filing_text(
                    filing, max_chars=self.config.max_text_chars
                )
                if filing_text:
                    logger.debug("  Fetched %d chars of filing text", len(filing_text))

            # Analyze
            result = self.analyzer.analyze(
                filing=filing,
                filing_text=filing_text,
                max_tokens=self.config.max_analysis_tokens,
            )

            if result.error:
                errors += 1
                logger.warning("  Analysis error: %s", result.error)
            else:
                logger.info(
                    "  → %s | %s | tokens: %d",
                    result.sentiment.upper(),
                    result.summary[:80],
                    result.tokens_used,
                )

            results.append(result)

            # Append incrementally so partial results are not lost
            self.storage.append_result(result, target_date)

            # Respect Claude rate limits between requests
            if i < len(filings):
                time.sleep(self.config.analysis_delay_secs)

        elapsed = time.time() - start_time
        saved = self.storage.save_daily_results(results, target_date)

        summary = {
            "date": str(target_date),
            "filings_found": len(filings),
            "filings_analyzed": len(results),
            "errors": errors,
            "elapsed_seconds": round(elapsed, 1),
            "output_json": str(saved["json"]),
            "output_csv": str(saved["csv"]),
            "sentiment_breakdown": _count_sentiment(results),
        }

        logger.info(
            "=== Done: %d analyzed, %d errors, %.0fs elapsed ===",
            len(results),
            errors,
            elapsed,
        )
        logger.info("Results saved to: %s", saved["json"])
        return summary

    def print_summary(self, summary: dict) -> None:
        """Print a human-readable summary to stdout."""
        print("\n" + "=" * 60)
        print(f"EDGAR Monitor Summary — {summary['date']}")
        print("=" * 60)
        print(f"  Filings found:    {summary['filings_found']}")
        print(f"  Filings analyzed: {summary['filings_analyzed']}")
        if summary.get("errors"):
            print(f"  Errors:           {summary['errors']}")
        print(f"  Time elapsed:     {summary.get('elapsed_seconds', '?')}s")

        sentiment = summary.get("sentiment_breakdown", {})
        if sentiment:
            print("\n  Sentiment breakdown:")
            for s, count in sorted(sentiment.items()):
                print(f"    {s:10s}: {count}")

        if "output_json" in summary:
            print(f"\n  Output JSON: {summary['output_json']}")
            print(f"  Output CSV:  {summary['output_csv']}")
        print("=" * 60 + "\n")


def _count_sentiment(results) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in results:
        s = r.sentiment or "unknown"
        counts[s] = counts.get(s, 0) + 1
    return counts
