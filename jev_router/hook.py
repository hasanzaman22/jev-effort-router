"""UserPromptSubmit hook: route each prompt and tell Claude where to send it.

Claude Code hooks cannot switch the model or effort of a running session. So
the hook adds context that asks the main session to delegate the task to the
plugin subagent pinned to the routed model and effort (agents/<tier>.md).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from typing import Any

from .config import data_dir, load_config
from .router import Decision, route, session_rank, should_delegate

PLUGIN_NAME = "jev-router"


def last_session_model(transcript_path: str | None, max_bytes: int = 256_000) -> str | None:
    """Model id of the most recent assistant message in the transcript, if any."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return None
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            lines = f.read().decode("utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") if isinstance(entry, dict) else None
        if isinstance(message, dict) and message.get("role") == "assistant" and message.get("model"):
            return str(message["model"])
    return None


def current_session_rank(payload: dict[str, Any], config: dict[str, Any]) -> int | None:
    """Tier rank of the main session.

    The transcript shows the model the session last used. The tier that
    jev-claude started the session with also gives the effort, so it wins
    while its model still matches (the user may have run /model since).
    """
    tiers = config["tiers"]
    launched = os.environ.get("JEV_ROUTER_SESSION_TIER")
    model = last_session_model(payload.get("transcript_path"))
    if launched and model:
        launched_model = next((t["model"] for t in tiers if t["name"] == launched), None)
        if launched_model and launched_model in model.lower():
            return session_rank(launched, tiers)
        return session_rank(model, tiers)
    return session_rank(launched or model, tiers)


def build_context(decision: Decision, delegate: bool, why: str, tiers: list[dict[str, Any]]) -> str:
    tier = decision.tier
    names = [t["name"] for t in tiers]
    stronger = names[names.index(tier["name"]) + 1:] if tier["name"] in names else []
    escalate = (
        f" If the subagent's reply starts with \"ESCALATE:\" or its result fails a check, rerun the "
        f"task on \"{PLUGIN_NAME}:{stronger[0]}\"."
        if stronger else ""
    )
    if not delegate:
        return (
            f"[jev-router] Routed tier: {tier['name']} ({tier['model']}, effort {tier['effort']}). "
            f"Handle this request in the main session ({why})."
        )
    return (
        f"[jev-router] This request was routed to tier {tier['name']} "
        f"(model {tier['model']}, effort {tier['effort']}). Delegate the whole task with the Agent "
        f"tool to subagent_type \"{PLUGIN_NAME}:{tier['name']}\". Give it a self-contained brief: "
        "the user's request word for word, plus every fact from this conversation it needs "
        "(file paths, decisions, constraints). Then relay its result to the user."
        f"{escalate} Do the task yourself instead only if the user asked you not to delegate, or "
        "if it needs conversation context that you cannot pass on in the brief."
    )


def log_decision(payload: dict[str, Any], decision: Decision, delegate: bool, why: str,
                 config: dict[str, Any]) -> None:
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
        "delegate": delegate,
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


def handle(payload: dict[str, Any]) -> dict[str, Any] | None:
    prompt = str(payload.get("prompt") or "")
    cwd = payload.get("cwd")
    config = load_config(cwd)
    if not config.get("enabled", True) or not prompt.strip() or prompt.lstrip().startswith("/"):
        return None

    decision = route(prompt, config, cwd)
    if decision is None:
        return None

    delegate, why = should_delegate(decision, current_session_rank(payload, config), config)
    log_decision(payload, decision, delegate, why, config)

    output: dict[str, Any] = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": build_context(decision, delegate, why, config["tiers"]),
        }
    }
    if config.get("show_decision", True):
        action = "delegating" if delegate else "main session"
        score = "forced" if decision.source == "override" else f"difficulty {decision.difficulty:.2f}"
        output["systemMessage"] = f"jev-router: {decision.name} ({score}, {decision.source}; {action})"
    return output


def main() -> int:
    # A routing failure must never block the user's prompt.
    try:
        payload = json.load(sys.stdin)
        output = handle(payload if isinstance(payload, dict) else {})
    except Exception as exc:  # noqa: BLE001
        print(f"jev-router: skipped ({exc.__class__.__name__})", file=sys.stderr)
        return 0
    if output:
        json.dump(output, sys.stdout)
    return 0
