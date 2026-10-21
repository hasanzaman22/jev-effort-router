"""The four judgments the router needs about a prompt, from Jev or a heuristic.

Both sources return the same shape: a probability distribution over the levels
of each Score question, plus the probability that the prompt depends on the
earlier conversation.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

COMPLEXITY_LEVELS = [
    "A conversational reply, a factual question, or a lookup that needs no code changes",
    "A small, well-specified change in one place, such as a typo fix, a rename, or a one-function edit",
    "A moderate task: a feature or bug fix that touches a few files and needs some investigation",
    "A large task: changes across many files, debugging an unclear failure, or writing a substantial component",
    "A very hard task: system architecture, a subtle concurrency or security problem, or novel algorithm design",
]

REASONING_LEVELS = [
    "The answer is direct; no planning or analysis is needed",
    "A few straightforward steps that follow an obvious path",
    "Careful analysis: comparing approaches, tracing code paths, or handling edge cases",
    "Deep reasoning: many interacting constraints, hard trade-offs, or a proof-like argument",
]

RISK_LEVELS = [
    "Read-only: explaining, answering, or planning without changing anything",
    "Ordinary, easily reversed changes in a development environment",
    "Changes where a subtle mistake is costly: public APIs, data handling, authentication, or payments",
    "Production, security, or irreversible operations: deploys, migrations, deleting data, or handling secrets",
]

QUESTIONS: dict[str, dict[str, Any]] = {
    "complexity": {
        "type": "score",
        "instructions": (
            "How much work does it take a skilled software engineer using an AI coding "
            "assistant to complete the request in `request.prompt`?"
        ),
        "criteria": COMPLEXITY_LEVELS,
    },
    "reasoning": {
        "type": "score",
        "instructions": (
            "How much careful reasoning does the request in `request.prompt` need to be "
            "done correctly, regardless of how much text it produces?"
        ),
        "criteria": REASONING_LEVELS,
    },
    "risk": {
        "type": "score",
        "instructions": (
            "If the request in `request.prompt` were done with a subtle mistake, how costly "
            "would that mistake be?"
        ),
        "criteria": RISK_LEVELS,
    },
    "context_dependent": {
        "type": "noul",
        "instructions": (
            "The request in `request.prompt` cannot be understood without the earlier "
            "conversation, for example it says \"do that\", \"fix it\", \"continue\", or "
            "answers a question the assistant asked."
        ),
    },
}

SCORE_LEVELS = {
    "complexity": len(COMPLEXITY_LEVELS),
    "reasoning": len(REASONING_LEVELS),
    "risk": len(RISK_LEVELS),
}


@dataclass
class Judgments:
    source: str  # "jev" or "heuristic"
    # Score question id -> probability of each level, index = level number.
    distributions: dict[str, list[float]] = field(default_factory=dict)
    context_dependent: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)


class JevUnavailable(Exception):
    """Raised when Jev cannot answer, so the caller falls back to the heuristic."""


def ask_jev(prompt: str, cwd: str | None, jev_config: dict[str, Any]) -> Judgments:
    api_key = os.environ.get(jev_config.get("api_key_env", "TYPESAFE_API_KEY"), "")
    if not api_key:
        raise JevUnavailable("no API key")

    state: dict[str, Any] = {"request": {"prompt": prompt}}
    if cwd:
        state["request"]["project_directory"] = os.path.basename(cwd.rstrip("/"))
    body = json.dumps({"model": jev_config["model"], "state": state, "questions": QUESTIONS})
    request = urllib.request.Request(
        jev_config["endpoint"],
        data=body.encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=float(jev_config["timeout_seconds"])) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise JevUnavailable(str(exc)) from exc

    return parse_jev_response(payload)


def parse_jev_response(payload: dict[str, Any]) -> Judgments:
    answers = payload.get("answers") or {}
    judgments = Judgments(source="jev", detail={"model": payload.get("model")})
    try:
        for qid, levels in SCORE_LEVELS.items():
            probabilities = answers[qid]["probabilities"]
            judgments.distributions[qid] = [float(probabilities.get(str(i), 0.0)) for i in range(levels)]
            judgments.detail[qid] = {
                "score": answers[qid].get("score"),
                "confidence": answers[qid].get("confidence"),
            }
        judgments.context_dependent = float(answers["context_dependent"]["noul"])
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise JevUnavailable(f"unexpected response: {exc}") from exc
    return judgments


# --- Offline heuristic -------------------------------------------------------

_HARD = re.compile(
    r"\b(architect\w*|redesign|refactor\w*|migrat\w*|concurren\w*|race condition|deadlock|"
    r"distributed|scalab\w*|optimi[sz]\w*|performance|memory leak|root cause|algorithm\w*|"
    r"vulnerab\w*|exploit|cryptograph\w*|prove|proof|across (the|all|every|many)|whole (codebase|repo|project)|"
    r"end[- ]to[- ]end|from scratch|full[- ]stack)\b",
    re.I,
)
_MEDIUM = re.compile(
    r"\b(implement\w*|build|create|add (a |an )?(feature|endpoint|page|command|test)|write (tests?|a)|"
    r"fix(es|ing)? (the |a )?bug|debug\w*|integrat\w*|investigat\w*|update|change|modify|support)\b",
    re.I,
)
_TRIVIAL = re.compile(
    r"^\s*(hi|hello|hey|thanks?|thank you|ok(ay)?|yes|no|what is|what's|what does|who|when|where|"
    r"define|explain briefly|summari[sz]e|list|show me)\b",
    re.I,
)
_SMALL_EDIT = re.compile(r"\b(typos?|rename|spelling|reword|bump (the )?version|one[- ]line|comment out)\b", re.I)
_RISKY = re.compile(
    r"\b(prod(uction)?|deploy\w*|release|migration|drop (table|database)|delete|rm -rf|truncate|"
    r"secret\w*|credential\w*|api key|password|auth\w*|payment\w*|billing|security|permission\w*|"
    r"force[- ]push|rollback)\b",
    re.I,
)
_REASONING = re.compile(
    r"\b(why|trade-?offs?|compare|evaluate|design|plan|edge cases?|correct(ness)?|analy[sz]\w*|"
    r"reason about|figure out|tricky|subtle|complex)\b",
    re.I,
)
_CONTEXT_REF = re.compile(
    r"^\s*(do (it|that|this)|fix (it|that|this)|continue|go on|go ahead|proceed|same (for|with)|"
    r"try again|again|yes|no|ok(ay)?|sure|the (first|second|last) one|option \d|both)\b",
    re.I,
)
_FILE_REF = re.compile(r"[\w./-]+\.(py|js|ts|tsx|jsx|go|rs|java|kt|rb|php|c|cc|cpp|h|cs|swift|sql|ya?ml|json|toml|md)\b")


def _peaked(level: int, levels: int, spread: float = 0.1) -> list[float]:
    """A distribution centred on `level`, with a little mass on the next level up.

    The spread stays under 1 - quantile, so the quantile reads `level` itself;
    the config's heuristic_tier_bump handles the heuristic's uncertainty.
    """
    dist = [0.0] * levels
    dist[level] = 1.0 - spread
    dist[min(level + 1, levels - 1)] += spread
    return dist


def heuristic(prompt: str) -> Judgments:
    text = prompt.strip()
    words = len(text.split())
    files = len(set(_FILE_REF.findall(text)))
    hard = len(_HARD.findall(text))
    medium = len(_MEDIUM.findall(text))
    has_code = "```" in text

    if hard >= 2 or (hard and (words > 120 or files >= 3)):
        complexity = 4
    elif hard or files >= 3 or words > 250:
        complexity = 3
    elif _SMALL_EDIT.search(text) and words <= 40:
        complexity = 1
    elif medium or has_code or files >= 2 or words > 60:
        complexity = 2
    elif _TRIVIAL.search(text) or (words <= 12 and not files):
        complexity = 0
    else:
        complexity = 1

    reasoning_hits = len(_REASONING.findall(text)) + hard
    reasoning = 3 if reasoning_hits >= 3 else 2 if reasoning_hits >= 1 else 1 if complexity >= 2 else 0

    risk_hits = len(_RISKY.findall(text))
    if risk_hits >= 2:
        risk = 3
    elif risk_hits:
        risk = 2
    elif complexity >= 1:
        risk = 1
    else:
        risk = 0

    context = 0.9 if (_CONTEXT_REF.search(text) and words <= 20) else 0.1

    return Judgments(
        source="heuristic",
        distributions={
            "complexity": _peaked(complexity, SCORE_LEVELS["complexity"]),
            "reasoning": _peaked(reasoning, SCORE_LEVELS["reasoning"]),
            "risk": _peaked(risk, SCORE_LEVELS["risk"]),
        },
        context_dependent=context,
        detail={"words": words, "files": files, "levels": [complexity, reasoning, risk]},
    )
