"""
OpenClaw configuration reader.

Parses ~/.openclaw/openclaw.json and resolves a model by ID into the
provider settings needed to make a direct API call (baseUrl, apiKey,
api format).

openclaw.json structure (JSON5 — comments and trailing commas allowed):
{
  "models": {
    "providers": {
      "<provider_name>": {
        "baseUrl": "https://...",
        "api": "openai-completions" | "openai-responses" | "anthropic-messages",
        "apiKey": "sk-..."  // optional; may live in credentials file instead
        "models": [
          { "id": "provider/model-name", "maxTokens": 8192, ... }
        ]
      }
    }
  }
}

API credentials may alternatively live in plain-text files at:
  ~/.openclaw/credentials/<provider_name>
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Default paths ──────────────────────────────────────────────────────────────

def _openclaw_home() -> Path:
    """Return the openclaw config directory, respecting OPENCLAW_HOME / OPENCLAW_STATE_DIR."""
    for env_var in ("OPENCLAW_STATE_DIR", "OPENCLAW_HOME"):
        val = os.environ.get(env_var)
        if val:
            return Path(val).expanduser()
    return Path.home() / ".openclaw"


def default_config_path() -> Path:
    custom = os.environ.get("OPENCLAW_CONFIG_PATH")
    if custom:
        return Path(custom).expanduser()
    return _openclaw_home() / "openclaw.json"


# ── Parsed result ──────────────────────────────────────────────────────────────

@dataclass
class OpenClawModelConfig:
    """Resolved model configuration, ready to pass to an LLM client."""
    model_id: str          # e.g. "hf:zai-org/GLM-4.7" or "qwen2.5:72b"
    provider_name: str     # key in models.providers, e.g. "synthetic"
    base_url: str          # e.g. "https://api.synthetic.new/anthropic"
    api_key: str           # resolved API key (from config or credentials file)
    api_type: str          # "openai-completions" | "openai-responses" | "anthropic-messages"
    max_tokens: int        # from model definition, default 4096


# ── Parser ─────────────────────────────────────────────────────────────────────

def load_model_config(
    model_id: str,
    config_path: Optional[Path] = None,
) -> OpenClawModelConfig:
    """
    Read openclaw.json and resolve the given model_id to a provider config.

    Args:
        model_id:    The model ID as defined in openclaw.json providers[x].models[y].id
                     Also accepts "provider_name/model_id" shorthand.
        config_path: Override path to openclaw.json (default: ~/.openclaw/openclaw.json).

    Raises:
        FileNotFoundError: If openclaw.json does not exist.
        KeyError:          If model_id is not found in any provider.
        ValueError:        If the provider config is missing required fields.
    """
    path = config_path or default_config_path()
    raw = _read_json5(path)
    providers: dict = (
        raw.get("models", {}).get("providers", {})
    )
    if not providers:
        raise ValueError(
            f"No models.providers section found in {path}"
        )

    # Search all providers for the model id
    for provider_name, provider_cfg in providers.items():
        if not isinstance(provider_cfg, dict):
            continue
        models_list = provider_cfg.get("models", [])
        for model_def in models_list:
            if not isinstance(model_def, dict):
                continue
            if model_def.get("id") == model_id:
                return _build_config(
                    model_id=model_id,
                    model_def=model_def,
                    provider_name=provider_name,
                    provider_cfg=provider_cfg,
                    openclaw_home=path.parent,
                )

    # Also accept shorthand "provider_name/..." to disambiguate
    available = _list_model_ids(providers)
    raise KeyError(
        f"Model '{model_id}' not found in {path}.\n"
        f"Available models: {', '.join(available) or '(none)'}"
    )


def list_available_models(config_path: Optional[Path] = None) -> list[dict]:
    """
    Return all models defined across all providers in openclaw.json.

    Each entry is a dict: {provider, id, name, api_type, base_url}.
    """
    path = config_path or default_config_path()
    raw = _read_json5(path)
    providers = raw.get("models", {}).get("providers", {})
    result = []
    for provider_name, provider_cfg in providers.items():
        if not isinstance(provider_cfg, dict):
            continue
        for model_def in provider_cfg.get("models", []):
            if not isinstance(model_def, dict):
                continue
            result.append({
                "provider": provider_name,
                "id": model_def.get("id", ""),
                "name": model_def.get("name", ""),
                "api_type": provider_cfg.get("api", "openai-completions"),
                "base_url": provider_cfg.get("baseUrl", ""),
            })
    return result


# ── Internal helpers ───────────────────────────────────────────────────────────

def _build_config(
    model_id: str,
    model_def: dict,
    provider_name: str,
    provider_cfg: dict,
    openclaw_home: Path,
) -> OpenClawModelConfig:
    base_url = provider_cfg.get("baseUrl", "").rstrip("/")
    if not base_url:
        raise ValueError(
            f"Provider '{provider_name}' has no baseUrl in openclaw.json"
        )

    api_type = provider_cfg.get("api", "openai-completions")

    # Resolve API key: inline config takes priority, then credentials file
    api_key = (
        provider_cfg.get("apiKey")
        or _read_credentials_file(openclaw_home, provider_name)
        or ""
    )
    if not api_key:
        logger.warning(
            "No API key found for provider '%s'. "
            "Set it in openclaw.json or ~/.openclaw/credentials/%s",
            provider_name, provider_name,
        )

    max_tokens = model_def.get("maxTokens", 4096)

    # For openai-responses api, append /v1 if the URL doesn't already end with it
    # (openai-completions and openai-responses both use the OpenAI SDK)
    if api_type in ("openai-completions", "openai-responses"):
        if not base_url.endswith("/v1"):
            base_url = base_url + "/v1"

    return OpenClawModelConfig(
        model_id=model_id,
        provider_name=provider_name,
        base_url=base_url,
        api_key=api_key,
        api_type=api_type,
        max_tokens=max_tokens,
    )


def _read_credentials_file(openclaw_home: Path, provider_name: str) -> str:
    """Read API key from ~/.openclaw/credentials/<provider_name> (plain text)."""
    creds_file = openclaw_home / "credentials" / provider_name
    if creds_file.exists():
        try:
            return creds_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("Could not read credentials file %s: %s", creds_file, exc)
    return ""


def _read_json5(path: Path) -> dict:
    """Read a JSON5 file (strips // line comments and /* block comments *)."""
    if not path.exists():
        raise FileNotFoundError(
            f"openclaw.json not found at {path}. "
            f"Set OPENCLAW_CONFIG_PATH or OPENCLAW_STATE_DIR if it is elsewhere."
        )
    text = path.read_text(encoding="utf-8")
    text = _strip_json5_comments(text)
    # Remove trailing commas before } or ] (JSON5 allows them)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse {path}: {exc}") from exc


def _strip_json5_comments(text: str) -> str:
    """Remove // line comments and /* block comments */ from JSON5 text."""
    # Block comments first
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # Line comments (not inside strings — simple heuristic)
    lines = []
    for line in text.splitlines():
        # Remove // comments that are not inside a string
        # (simple approach: split on //, keep left part if not inside quotes)
        stripped = _strip_line_comment(line)
        lines.append(stripped)
    return "\n".join(lines)


def _strip_line_comment(line: str) -> str:
    """Remove a // comment from a single line, respecting quoted strings."""
    in_string = False
    escape_next = False
    for i, ch in enumerate(line):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string and ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
            return line[:i]
    return line


def _list_model_ids(providers: dict) -> list[str]:
    ids = []
    for provider_cfg in providers.values():
        if isinstance(provider_cfg, dict):
            for m in provider_cfg.get("models", []):
                if isinstance(m, dict) and m.get("id"):
                    ids.append(m["id"])
    return ids
