"""Tests for the OpenClaw config reader."""

import json
import tempfile
from pathlib import Path

import pytest

from src.openclaw_config import (
    OpenClawModelConfig,
    load_model_config,
    list_available_models,
    _read_json5,
    _strip_json5_comments,
)


# ── Sample openclaw.json fixtures ─────────────────────────────────────────────

SAMPLE_CONFIG = {
    "models": {
        "mode": "merge",
        "providers": {
            "synthetic": {
                "baseUrl": "https://api.synthetic.new/anthropic",
                "api": "anthropic-messages",
                "apiKey": "sk-synthetic-key",
                "models": [
                    {
                        "id": "hf:zai-org/GLM-4.7",
                        "name": "GLM-4.7",
                        "maxTokens": 128000,
                    }
                ],
            },
            "local_ollama": {
                "baseUrl": "http://127.0.0.1:11434",
                "api": "openai-completions",
                "models": [
                    {
                        "id": "qwen2.5:72b",
                        "name": "Qwen 2.5 72B",
                        "maxTokens": 8192,
                    },
                    {
                        "id": "llama3.3:70b",
                        "name": "Llama 3.3 70B",
                        "maxTokens": 8192,
                    },
                ],
            },
        },
    }
}

SAMPLE_CONFIG_JSON5 = """
{
  // This is a JSON5 comment
  "models": {
    "mode": "merge", // trailing comma below
    "providers": {
      "myProvider": {
        "baseUrl": "http://localhost:8000/v1",
        "api": "openai-completions",
        /* block comment */
        "apiKey": "sk-local",
        "models": [
          {
            "id": "my-model",
            "name": "My Local Model",
            "maxTokens": 4096,  // trailing comma
          },
        ],
      },
    },
  },
}
"""


def _write_config(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "openclaw.json"
    p.write_text(json.dumps(data))
    return p


# ── Tests: load_model_config ───────────────────────────────────────────────────

class TestLoadModelConfig:
    def test_resolves_openai_provider(self, tmp_path):
        cfg_path = _write_config(tmp_path, SAMPLE_CONFIG)
        cfg = load_model_config("qwen2.5:72b", config_path=cfg_path)

        assert isinstance(cfg, OpenClawModelConfig)
        assert cfg.model_id == "qwen2.5:72b"
        assert cfg.provider_name == "local_ollama"
        assert cfg.api_type == "openai-completions"
        assert cfg.max_tokens == 8192
        # Should have /v1 appended for openai-completions
        assert cfg.base_url.endswith("/v1")

    def test_resolves_anthropic_provider(self, tmp_path):
        cfg_path = _write_config(tmp_path, SAMPLE_CONFIG)
        cfg = load_model_config("hf:zai-org/GLM-4.7", config_path=cfg_path)

        assert cfg.provider_name == "synthetic"
        assert cfg.api_type == "anthropic-messages"
        assert cfg.api_key == "sk-synthetic-key"
        assert cfg.max_tokens == 128000

    def test_raises_for_unknown_model(self, tmp_path):
        cfg_path = _write_config(tmp_path, SAMPLE_CONFIG)
        with pytest.raises(KeyError, match="nonexistent-model"):
            load_model_config("nonexistent-model", config_path=cfg_path)

    def test_raises_for_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_model_config("any-model", config_path=tmp_path / "missing.json")

    def test_reads_inline_api_key(self, tmp_path):
        cfg_path = _write_config(tmp_path, SAMPLE_CONFIG)
        cfg = load_model_config("hf:zai-org/GLM-4.7", config_path=cfg_path)
        assert cfg.api_key == "sk-synthetic-key"

    def test_reads_credentials_file(self, tmp_path):
        """API key from credentials file takes effect when not inline."""
        config = {
            "models": {
                "providers": {
                    "myprovider": {
                        "baseUrl": "http://localhost:8000",
                        "api": "openai-completions",
                        # No apiKey inline
                        "models": [{"id": "my-model", "name": "M"}],
                    }
                }
            }
        }
        cfg_path = tmp_path / "openclaw.json"
        cfg_path.write_text(json.dumps(config))

        # Create credentials file
        creds_dir = tmp_path / "credentials"
        creds_dir.mkdir()
        (creds_dir / "myprovider").write_text("sk-from-creds-file\n")

        cfg = load_model_config("my-model", config_path=cfg_path)
        assert cfg.api_key == "sk-from-creds-file"

    def test_v1_not_duplicated_if_already_present(self, tmp_path):
        """Don't append /v1 twice if baseUrl already ends with /v1."""
        config = {
            "models": {
                "providers": {
                    "p": {
                        "baseUrl": "http://localhost:8000/v1",
                        "api": "openai-completions",
                        "models": [{"id": "m", "name": "M"}],
                    }
                }
            }
        }
        cfg_path = tmp_path / "openclaw.json"
        cfg_path.write_text(json.dumps(config))
        cfg = load_model_config("m", config_path=cfg_path)
        assert cfg.base_url == "http://localhost:8000/v1"
        assert "/v1/v1" not in cfg.base_url


# ── Tests: list_available_models ──────────────────────────────────────────────

class TestListAvailableModels:
    def test_lists_all_models(self, tmp_path):
        cfg_path = _write_config(tmp_path, SAMPLE_CONFIG)
        models = list_available_models(config_path=cfg_path)

        ids = [m["id"] for m in models]
        assert "hf:zai-org/GLM-4.7" in ids
        assert "qwen2.5:72b" in ids
        assert "llama3.3:70b" in ids
        assert len(models) == 3

    def test_model_entry_has_required_fields(self, tmp_path):
        cfg_path = _write_config(tmp_path, SAMPLE_CONFIG)
        models = list_available_models(config_path=cfg_path)
        for m in models:
            assert "id" in m
            assert "provider" in m
            assert "api_type" in m
            assert "base_url" in m


# ── Tests: JSON5 parsing ───────────────────────────────────────────────────────

class TestJson5Parsing:
    def test_strips_line_comments(self):
        text = '{"key": "value" // comment\n}'
        result = _strip_json5_comments(text)
        assert "//" not in result
        assert '"value"' in result

    def test_strips_block_comments(self):
        text = '{"key": /* block */ "value"}'
        result = _strip_json5_comments(text)
        assert "/*" not in result
        assert '"value"' in result

    def test_parses_json5_with_trailing_commas(self, tmp_path):
        p = tmp_path / "openclaw.json"
        p.write_text(SAMPLE_CONFIG_JSON5)
        data = _read_json5(p)

        assert "models" in data
        providers = data["models"]["providers"]
        assert "myProvider" in providers
        assert providers["myProvider"]["models"][0]["id"] == "my-model"

    def test_preserves_url_with_double_slash(self):
        """URL http:// should not be affected by comment stripping."""
        text = '{"url": "http://localhost:8000/v1"}'
        result = _strip_json5_comments(text)
        assert "http://localhost:8000/v1" in result
