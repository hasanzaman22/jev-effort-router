"""The /jev-effort-router:enable, :disable, and :status slash commands.

    /jev-effort-router:enable    route every prompt until disabled
    /jev-effort-router:disable   stop routing until enabled
    /jev-effort-router:status    show whether routing is on, and the Jev service in use
"""

from __future__ import annotations

import os
import sys

from .config import load_config, read_state, set_enabled, state_path
from .judgments import resolve_jev

USAGE = "usage: jevrouter.py enable | disable | status"


def status_text(cwd: str | None) -> str:
    config = load_config(cwd)
    api_key, provider, _, model = resolve_jev(config["jev"])
    on = "enabled" if config.get("enabled", True) else "disabled"
    source = "set by command" if "enabled" in read_state() else "default"
    jev = f"Jev via {provider} ({model})" if api_key else "no Jev API key, using the offline heuristic"
    return f"jev-router is {on} ({source}). {jev}."


def main(argv: list[str] | None = None) -> int:
    args = [a.lower() for a in (sys.argv[1:] if argv is None else argv)]
    action = args[0] if args else "status"
    cwd = os.getcwd()
    if action in ("enable", "on"):
        set_enabled(True)
        print("jev-router enabled. Every prompt is routed until you run /jev-effort-router:disable.")
    elif action in ("disable", "off"):
        set_enabled(False)
        print("jev-router disabled. Prompts are not routed until you run /jev-effort-router:enable.")
    elif action == "status":
        print(status_text(cwd))
        print(f"State file: {state_path()}")
    else:
        print(USAGE)
        return 2
    return 0
