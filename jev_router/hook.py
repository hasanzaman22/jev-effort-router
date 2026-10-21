"""Pick the effort the Claude Code session switches to, for the jev-effort-router mod.

The mod (hooks/register.ts) runs scripts/route_switch.py on every prompt. This
module routes the prompt, logs the decision, and prints the effort the mod then
sets on each request of the main conversation. The model is never changed.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from typing import Any

from .config import data_dir, load_config
from .router import Decision, route


def log_decision(payload: dict[str, Any], decision: Decision, why: str, config: dict[str, Any]) -> None:
    if not config.get("log", True):
        return
    prompt = payload.get("prompt", "")
    record: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "session_id": payload.get("session_id"),
        "tier": decision.name,
        "difficulty": decision.difficulty,
        "source": decision.source,
        "context_dependent": round(decision.context_dependent, 3),
        "why": why,
        "reasons": decision.reasons,
        "detail": decision.detail,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
        "prompt_words": len(prompt.split()),
    }
    if config.get("log_prompt_text"):
        record["prompt"] = prompt
    try:
        directory = data_dir()
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "decisions.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


def handle_switch(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The effort the session should switch to, for the jev-effort-router mod.

    Returns None when routing is off for this prompt. `keep` is true when the
    prompt only makes sense with the earlier conversation ("yes, do that"): the
    session then keeps the effort of the work it continues.
    """
    prompt = str(payload.get("prompt") or "")
    cwd = payload.get("cwd")
    config = load_config(cwd)
    if not config.get("enabled", True) or not prompt.strip() or prompt.lstrip().startswith("/"):
        return None

    decision = route(prompt, config, cwd)
    if decision is None:
        return None

    keep = decision.source != "override" and decision.context_dependent >= 0.5
    why = "kept the current effort: the prompt depends on the conversation" if keep else "switched the effort"
    log_decision(payload, decision, why, config)
    effort = decision.tier["effort"]
    score = "forced" if decision.source == "override" else f"difficulty {decision.difficulty:.2f}"
    return {
        "effort": effort,
        "keep": keep,
        "message": f"jev-router: effort {effort} ({score}, {decision.source})" if config.get("show_decision", True) else "",
    }


def main_switch() -> int:
    """Entry point for the mod: reads {prompt, cwd} on stdin, writes the effort as JSON."""
    try:
        payload = json.load(sys.stdin)
        output = handle_switch(payload if isinstance(payload, dict) else {})
    except Exception as exc:  # noqa: BLE001
        print(f"jev-router: skipped ({exc.__class__.__name__})", file=sys.stderr)
        output = None
    json.dump(output, sys.stdout)
    return 0
