"""Configuration loading for jev-router.

Defaults live in this file. Users override any key with JSON files, merged in
this order (later wins):

1. ~/.claude/jev-router.json            (user-wide)
2. <project>/.claude/jev-router.json    (per project)
3. the file named by $JEV_ROUTER_CONFIG  (explicit)
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

# Tiers are ordered from cheapest to strongest. Each tier has a matching
# subagent in agents/<name>.md, so tier names are fixed. Thresholds are not:
# a request goes to the first tier whose max_difficulty is >= its difficulty,
# which is Jev's capability level scaled to 0-1 (0, 0.25, 0.5, 0.75, 1).
DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "jev": {
        # The key is read from api_key_env, then TYPESAFE_API_KEY,
        # OPENROUTER_API_KEY, and OPENCODE_API_KEY. Its prefix picks the
        # service (sk-or- OpenRouter, oc_sk_ OpenCode, anything else TypeSafe).
        # Set provider, endpoint, or model only to override that choice.
        "api_key_env": "JEV_API_KEY",
        "provider": None,
        "endpoint": None,
        "model": None,
        "timeout_seconds": 4.0,
    },
    # Each Score is read at this quantile of its probability distribution
    # instead of its mean. 0.85 means "the level the task is at or below with
    # 85% probability", so an uncertain answer resolves toward the harder level.
    "quantile": 0.85,
    "tiers": [
        {"name": "haiku-low", "model": "haiku", "effort": "low", "max_difficulty": 0.0},
        {"name": "sonnet-medium", "model": "sonnet", "effort": "medium", "max_difficulty": 0.25},
        {"name": "sonnet-high", "model": "sonnet", "effort": "high", "max_difficulty": 0.5},
        {"name": "opus-high", "model": "opus", "effort": "high", "max_difficulty": 0.75},
        {"name": "opus-xhigh", "model": "opus", "effort": "xhigh", "max_difficulty": 1.0},
    ],
    # A risky request never goes below this tier, whatever its difficulty.
    "risk_floor": {"min_risk": 0.75, "tier": "opus-high"},
    # The offline heuristic is less reliable than Jev, so its pick moves up
    # this many tiers.
    "heuristic_tier_bump": 1,
    "delegate": {
        "enabled": True,
        # Only hand work down to a cheaper subagent when the complexity level
        # is at least this (0 = trivial question, 1 = small edit). Answering a
        # trivial question directly is cheaper than spawning a subagent.
        "down_min_complexity_level": 1,
    },
    "show_decision": True,
    "log": True,
    "log_prompt_text": False,
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def config_paths(cwd: str | None = None) -> list[Path]:
    paths = [Path.home() / ".claude" / "jev-router.json"]
    if cwd:
        paths.append(Path(cwd) / ".claude" / "jev-router.json")
    explicit = os.environ.get("JEV_ROUTER_CONFIG")
    if explicit:
        paths.append(Path(explicit).expanduser())
    return paths


def load_config(cwd: str | None = None) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULTS)
    for path in config_paths(cwd):
        if path.is_file():
            _deep_merge(config, _read_json(path))
    return config


def data_dir() -> Path:
    """Where decision logs go. Survives plugin updates when run as a plugin."""
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        return Path(plugin_data)
    return Path.home() / ".claude" / "jev-router"
