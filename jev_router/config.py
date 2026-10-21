"""Configuration loading for jev-router.

Defaults live in this file. Users override any key with JSON files, merged in
this order (later wins):

1. ~/.claude/jev-router.json            (user-wide)
2. <project>/.claude/jev-router.json    (per project)
3. the file named by $JEV_ROUTER_CONFIG  (explicit)

`/jev-effort-router:enable` and `/jev-effort-router:disable` write a small state file that
overrides `enabled` from all of these, until the next one of those commands.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

# Effort levels, lowest first. A level's name is what [jev:<name>] forces.
# A request goes to the first level whose max_difficulty is >= its difficulty,
# which is Jev's effort answer scaled to 0-1 (0, 0.33, 0.67, 1).
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
        {"name": "low", "effort": "low", "max_difficulty": 0.0},
        {"name": "medium", "effort": "medium", "max_difficulty": 0.34},
        {"name": "high", "effort": "high", "max_difficulty": 0.67},
        {"name": "xhigh", "effort": "xhigh", "max_difficulty": 1.0},
    ],
    # A risky request never gets less effort than this, whatever its difficulty.
    "risk_floor": {"min_risk": 0.75, "tier": "high"},
    # The offline heuristic is less reliable than Jev, so its pick moves up
    # this many effort levels.
    "heuristic_tier_bump": 1,
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
    enabled = read_state().get("enabled")
    if isinstance(enabled, bool):
        config["enabled"] = enabled
    return config


def state_path() -> Path:
    """The on/off switch. A fixed path, so the hook and the slash command agree on it."""
    return Path.home() / ".claude" / "jev-router" / "state.json"


def read_state() -> dict[str, Any]:
    return _read_json(state_path())


def set_enabled(enabled: bool) -> Path:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state = read_state()
    state["enabled"] = enabled
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return path


def data_dir() -> Path:
    """Where decision logs go. Survives plugin updates when run as a plugin."""
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        return Path(plugin_data)
    return Path.home() / ".claude" / "jev-router"
