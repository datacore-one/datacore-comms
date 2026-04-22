#!/usr/bin/env python3
"""Space-agnostic configuration loader for comms module.

Each space (or brand) provides its own config in:
  {SPACE_ROOT}/1-tracks/comms/comms-config.yaml

The engagement pipeline loads this config to get:
- Account credentials mapping
- Discovery queries
- Voice prompt template
- Brand identity (handle, name, excluded handles)
- Example replies path
- Daily/hourly limits
"""

import os
import yaml
from pathlib import Path
from typing import Optional, List, Dict

DEFAULT_CONFIG = {
    "brand": {
        "name": "Brand",
        "handle": "@brand",
        "tagline": "",
        "excluded_handles": [],
    },
    "accounts": {
        "default": {
            "env_prefix": "X_",
            "daily_limit": 50,
        }
    },
    "discovery": {
        "queries": [
            "Find recent tweets (last 24h) about AI agents, LLM memory, or context windows from developers and researchers. Exclude crypto promotions.",
        ],
        "queries_per_cycle": 3,
        "min_relevance": 7,
        "cooldown_hours": 24,
    },
    "voice": {
        "prompt_template": """You are drafting a reply tweet.

## Target tweet
Author: {author}
Tweet: {content}
URL: {url}

## Voice
- Punchy short openers that hook
- Conversational, not declarative
- Make ONE sharp, insightful point
- Under 280 characters. No emojis. No hashtags. No links.

Return ONLY the reply text.""",
        "example_replies_path": None,
        "max_length": 280,
    },
    "limits": {
        "max_per_hour": 5,
        "max_per_day": 15,
        "max_pending": 30,
        "pending_expiry_hours": 4,
        "max_autonomous_per_day": 15,
    },
    "guardrails": {
        "anti_patterns": ["WAGMI", "moon", "lambo", "guaranteed", "100x"],
        "max_exclamations": 1,
        "max_capitals_ratio": 0.3,
    },
}


def find_space_root() -> Path:
    """Determine the active space root from env or cwd."""
    # Priority: DATACORE_ROOT env > current working directory under ~/Data
    root = os.environ.get("DATACORE_ROOT")
    if root:
        return Path(root)
    # Try to infer from cwd
    cwd = Path.cwd()
    # If we're under ~/Data, walk up to find a space root
    data_root = Path.home() / "Data"
    if data_root in cwd.parents or cwd == data_root:
        # Check if cwd or any parent has a 1-tracks/comms directory
        for p in [cwd] + list(cwd.parents):
            if (p / "1-tracks" / "comms").exists():
                return p
        return data_root
    return cwd


def load_config(space_root: Optional[Path] = None) -> dict:
    """Load comms config for the current space.

    Falls back to DEFAULT_CONFIG if no space config found.
    """
    root = space_root or find_space_root()
    config_path = root / "1-tracks" / "comms" / "comms-config.yaml"

    config = _deep_merge(dict(DEFAULT_CONFIG), {})

    if config_path.exists():
        try:
            with open(config_path) as f:
                user_config = yaml.safe_load(f) or {}
            config = _deep_merge(config, user_config)
        except Exception as e:
            print(f"[comms_config] Warning: failed to load {config_path}: {e}")

    # Also check module-level override for shared defaults
    module_config_path = Path(__file__).parent.parent / "config" / "default.yaml"
    if module_config_path.exists():
        try:
            with open(module_config_path) as f:
                module_config = yaml.safe_load(f) or {}
            config = _deep_merge(config, module_config)
        except Exception:
            pass

    return config


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def get_account_credentials(account: str, config: dict) -> dict:
    """Get credentials for an account from env vars.

    Uses the env_prefix defined in config['accounts'][account].
    """
    accounts = config.get("accounts", {})
    if account not in accounts:
        raise ValueError(f"Unknown account: {account}. Known: {list(accounts.keys())}")

    prefix = accounts[account].get("env_prefix", f"{account.upper()}_X_")
    return {
        "consumer_key": os.environ.get(f"{prefix}API_KEY", os.environ.get(f"{prefix}CONSUMER_KEY")),
        "consumer_secret": os.environ.get(f"{prefix}API_SECRET", os.environ.get(f"{prefix}CONSUMER_SECRET")),
        "access_token": os.environ.get(f"{prefix}ACCESS_TOKEN"),
        "access_token_secret": os.environ.get(f"{prefix}ACCESS_TOKEN_SECRET"),
    }
