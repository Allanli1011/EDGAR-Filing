"""
Storage — persists analysis results to JSON and CSV files.
"""

import csv
import json
import logging
from datetime import date
from pathlib import Path

from .models import AnalysisResult

logger = logging.getLogger(__name__)


class ResultStorage:
    """Saves and loads analysis results from the output directory."""

    def __init__(self, output_dir: str = "output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save_daily_results(
        self, results: list[AnalysisResult], run_date: date
    ) -> dict[str, Path]:
        """
        Save a day's analysis results to both JSON and CSV.

        Returns a dict with 'json' and 'csv' keys pointing to saved files.
        """
        date_str = run_date.strftime("%Y-%m-%d")
        json_path = self.output_dir / f"edgar_analysis_{date_str}.json"
        csv_path = self.output_dir / f"edgar_analysis_{date_str}.csv"

        records = [r.to_dict() for r in results]

        # JSON
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "run_date": date_str,
                    "total_filings": len(results),
                    "results": records,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        logger.info("Saved JSON: %s", json_path)

        # CSV (flattened)
        if records:
            flat_fields = [
                "accession_no", "form_type", "company_name", "cik",
                "filed_at", "filing_url", "summary", "sentiment",
                "confidence", "tokens_used", "error",
            ]
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=flat_fields, extrasaction="ignore")
                writer.writeheader()
                for r in records:
                    row = {k: r.get(k, "") for k in flat_fields}
                    # Flatten lists to semicolon-separated strings for CSV
                    row["summary"] = (r.get("summary") or "").replace("\n", " ")
                    writer.writerow(row)
            logger.info("Saved CSV: %s", csv_path)

        return {"json": json_path, "csv": csv_path}

    def load_daily_results(self, run_date: date) -> list[dict]:
        """Load previously saved results for a given date."""
        date_str = run_date.strftime("%Y-%m-%d")
        json_path = self.output_dir / f"edgar_analysis_{date_str}.json"
        if not json_path.exists():
            return []
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("results", [])

    def append_result(self, result: AnalysisResult, run_date: date) -> None:
        """Append a single result to the daily JSON file (for incremental runs)."""
        date_str = run_date.strftime("%Y-%m-%d")
        json_path = self.output_dir / f"edgar_analysis_{date_str}.json"

        if json_path.exists():
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {"run_date": date_str, "total_filings": 0, "results": []}

        data["results"].append(result.to_dict())
        data["total_filings"] = len(data["results"])

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
