"""The three judgments the router needs about a prompt, from Jev or a heuristic.

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

# Score levels are objects with a description and a few examples, which keeps
# Jev from splitting probability between neighbouring levels. The examples must
# stay generic: they steer every prompt, not just the ones they resemble.
def _level(what: str, examples: list[str]) -> dict[str, Any]:
    return {"what": what, "examples": examples}


# One level per effort setting, lowest first. This is the question the effort
# comes from; risk feeds the risk floor.
EFFORT_LEVELS = [
    _level("Minimal thinking: answer directly or make an obvious change",
           ["answer a factual question", "fix a typo", "rename a variable"]),
    _level("Moderate thinking: a few straightforward steps with an obvious approach",
           ["add a CLI flag", "write unit tests for a function", "add a simple endpoint"]),
    _level("Careful thinking: tracing code, comparing approaches, handling edge cases, or work where "
           "a mistake is costly",
           ["add OAuth login", "debug a flaky test", "zero-downtime database migration",
            "debug a production payment bug"]),
    _level("Extended deep thinking: many interacting constraints or subtle correctness",
           ["design a distributed system", "fix a subtle concurrency bug", "write a compiler"]),
]

RISK_LEVELS = [
    _level("Read-only: explaining, answering, or planning without changing anything",
           ["explain a function", "review a plan"]),
    _level("Ordinary, easily reversed changes in a development environment, including deleting "
           "generated or temporary files",
           ["edit source code under git", "delete a build or temp folder", "clear a local cache"]),
    _level("Changes where a subtle mistake is costly but recoverable: public APIs, data handling, "
           "authentication, or payments code",
           ["change auth middleware", "edit billing calculations", "change a public API response"]),
    _level("Operations on production systems, real user data, or secrets, or anything irreversible",
           ["deploy to production", "migrate or delete production data", "rotate leaked credentials"]),
]

QUESTIONS: dict[str, dict[str, Any]] = {
    "effort": {
        "type": "score",
        "instructions": (
            "How much thinking does a strong AI coding assistant need to complete the request "
            "in `request.prompt` correctly on the first attempt?"
        ),
        "criteria": EFFORT_LEVELS,
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
    "effort": len(EFFORT_LEVELS),
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


# Jev is served by TypeSafe and by two gateways. Each takes the same request
# body; only the address and model name differ. The key's prefix says which
# service issued it.
PROVIDERS: dict[str, dict[str, str]] = {
    "openrouter": {"endpoint": "https://openrouter.ai/api/v1/systemone", "model": "~typesafe/jev-latest"},
    "opencode": {"endpoint": "https://opencode.ai/inference/systemone/v1/systemone", "model": "jev-1.13-free"},
    "typesafe": {"endpoint": "https://api.typesafe.ai/v1/systemone", "model": "jev-latest"},
}
_KEY_PREFIXES = {"sk-or-": "openrouter", "oc_sk_": "opencode"}

# Checked in this order after jev.api_key_env, so a key works under the name
# its own service uses.
KEY_ENV_FALLBACKS = ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY", "OPENCODE_API_KEY")


def provider_for_key(api_key: str) -> str:
    for prefix, provider in _KEY_PREFIXES.items():
        if api_key.startswith(prefix):
            return provider
    return "typesafe"


def find_api_key(jev_config: dict[str, Any]) -> str:
    names = [jev_config.get("api_key_env") or "JEV_API_KEY", *KEY_ENV_FALLBACKS]
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def resolve_jev(jev_config: dict[str, Any]) -> tuple[str, str, str, str]:
    """The key, provider, endpoint, and model to use. Explicit config wins over the key prefix."""
    api_key = find_api_key(jev_config)
    provider = jev_config.get("provider") or provider_for_key(api_key)
    defaults = PROVIDERS.get(provider, PROVIDERS["typesafe"])
    endpoint = jev_config.get("endpoint") or defaults["endpoint"]
    model = jev_config.get("model") or defaults["model"]
    return api_key, provider, endpoint, model


def ask_jev(prompt: str, cwd: str | None, jev_config: dict[str, Any]) -> Judgments:
    api_key, provider, endpoint, model = resolve_jev(jev_config)
    if not api_key:
        raise JevUnavailable("no API key")

    state: dict[str, Any] = {"request": {"prompt": prompt}}
    if cwd:
        state["request"]["project_directory"] = os.path.basename(cwd.rstrip("/"))
    body = json.dumps({"model": model, "state": state, "questions": QUESTIONS})
    request = urllib.request.Request(
        endpoint,
        data=body.encode("utf-8"),
        # Some gateways (e.g. OpenCode behind Cloudflare) reject urllib's default User-Agent.
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "jev-router",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=float(jev_config["timeout_seconds"])) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise JevUnavailable(f"{provider}: {exc}") from exc

    judgments = parse_jev_response(payload)
    judgments.detail["provider"] = provider
    return judgments


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
    effort = [0, 0, 1, 2, 3][complexity]
    if reasoning_hits >= 3:
        effort = max(effort, 3)
    elif reasoning_hits:
        effort = max(effort, 2)

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
            "effort": _peaked(effort, SCORE_LEVELS["effort"]),
            "risk": _peaked(risk, SCORE_LEVELS["risk"]),
        },
        context_dependent=context,
        detail={"words": words, "files": files, "levels": [complexity, effort, risk]},
    )
