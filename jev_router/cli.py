"""jev-claude: pick the model and effort for a new Claude Code session.

    jev-claude "fix the typo in README.md"
    jev-claude --dry-run "redesign the auth flow"
    jev-claude "add tests for parser.py" -- --permission-mode acceptEdits

Arguments after "--" pass through to `claude`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from .config import load_config
from .router import route


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    passthrough: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, passthrough = argv[:split], argv[split + 1:]

    parser = argparse.ArgumentParser(prog="jev-claude", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prompt", help="the first prompt of the session")
    parser.add_argument("--dry-run", action="store_true", help="print the decision and exit")
    parser.add_argument("--json", action="store_true", help="with --dry-run, print JSON")
    args = parser.parse_args(argv)

    cwd = os.getcwd()
    config = load_config(cwd)
    decision = route(args.prompt, config, cwd)
    if decision is None:
        command = ["claude", *passthrough, args.prompt]
        tier_name = ""
    else:
        tier = decision.tier
        command = ["claude", "--model", tier["model"], "--effort", tier["effort"], *passthrough, args.prompt]
        tier_name = tier["name"]

    if args.dry_run:
        if args.json:
            print(json.dumps({
                "tier": tier_name or None,
                "difficulty": decision.difficulty if decision else None,
                "source": decision.source if decision else None,
                "reasons": decision.reasons if decision else [],
                "detail": decision.detail if decision else {},
                "command": command,
            }, indent=2))
        else:
            if decision:
                print(f"tier: {tier_name}  difficulty: {decision.difficulty:.2f}  source: {decision.source}")
                print("reasons: " + "; ".join(decision.reasons))
            print("command: " + " ".join(command[:-1]) + " <prompt>")
        return 0

    claude = shutil.which("claude")
    if not claude:
        print("jev-claude: `claude` is not on PATH", file=sys.stderr)
        return 127
    if decision:
        print(f"jev-router: starting {tier_name} (difficulty {decision.difficulty:.2f}, {decision.source})",
              file=sys.stderr)
    env = dict(os.environ)
    if tier_name:
        env["JEV_ROUTER_SESSION_TIER"] = tier_name
    os.execvpe(claude, command, env)
    return 0  # not reached
