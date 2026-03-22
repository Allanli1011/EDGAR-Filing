"""Tests for the ResultStorage module."""

import json
import csv
from datetime import date
from pathlib import Path

import pytest
from src.storage import ResultStorage
from src.models import AnalysisResult, Filing

@pytest.fixture
def temp_output_dir(tmp_path):
    """Fixture to provide a temporary output directory."""
    return tmp_path / "output"

@pytest.fixture
def storage(temp_output_dir):
    """Fixture to provide a ResultStorage instance."""
    return ResultStorage(output_dir=str(temp_output_dir))

@pytest.fixture
def sample_result():
    """Fixture to provide a sample AnalysisResult."""
    filing = Filing(
        accession_no="0001234567-24-000001",
        form_type="8-K",
        company_name="Apple Inc.",
        cik="0000320193",
        filed_at="2024-01-15",
        filing_url="https://www.sec.gov/Archives/edgar/data/320193/000123456724000001/",
    )
    return AnalysisResult(
        filing=filing,
        summary="Test summary",
        sentiment="positive",
        confidence="high",
        tokens_used=500
    )

class TestResultStorage:
    def test_init_creates_dir(self, temp_output_dir):
        ResultStorage(output_dir=str(temp_output_dir))
        assert temp_output_dir.exists()

    def test_save_daily_results(self, storage, sample_result, temp_output_dir):
        run_date = date(2024, 1, 15)
        results = [sample_result]
        
        saved = storage.save_daily_results(results, run_date)
        
        assert saved["json"].exists()
        assert saved["csv"].exists()
        
        # Verify JSON content
        with open(saved["json"], "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["run_date"] == "2024-01-15"
            assert data["total_filings"] == 1
            assert data["results"][0]["accession_no"] == sample_result.filing.accession_no

        # Verify CSV content
        with open(saved["csv"], "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) == 1
            assert rows[0]["accession_no"] == sample_result.filing.accession_no
            assert rows[0]["company_name"] == sample_result.filing.company_name

    def test_append_result(self, storage, sample_result, temp_output_dir):
        run_date = date(2024, 1, 15)
        
        # Append first result
        storage.append_result(sample_result, run_date)
        
        json_path = temp_output_dir / "edgar_analysis_2024-01-15.json"
        assert json_path.exists()
        
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert len(data["results"]) == 1
            
        # Append second result
        storage.append_result(sample_result, run_date)
        
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert len(data["results"]) == 2
            assert data["total_filings"] == 2

    def test_load_daily_results(self, storage, sample_result):
        run_date = date(2024, 1, 15)
        storage.save_daily_results([sample_result], run_date)
        
        loaded = storage.load_daily_results(run_date)
        assert len(loaded) == 1
        assert loaded[0]["accession_no"] == sample_result.filing.accession_no

    def test_load_nonexistent_results(self, storage):
        run_date = date(1999, 1, 1)
        loaded = storage.load_daily_results(run_date)
        assert loaded == []
