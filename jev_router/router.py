"""Turn judgments about a prompt into a model and effort tier."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .judgments import SCORE_LEVELS, JevUnavailable, Judgments, ask_jev, heuristic

MODEL_RANK = {"haiku": 0, "sonnet": 1, "opus": 2}

# Per-prompt override: "[jev:off]" skips routing, "[jev:opus-high]" forces a tier.
_OVERRIDE = re.compile(r"\[jev:([a-z0-9-]+)\]", re.I)


@dataclass
class Decision:
    tier: dict[str, Any]
    difficulty: float
    source: str  # "jev", "heuristic", or "override"
    context_dependent: float = 0.0
    complexity_level: int = 0
    reasons: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.tier["name"]


def quantile_level(distribution: list[float], q: float) -> int:
    """Smallest level whose cumulative probability reaches q."""
    total = sum(distribution) or 1.0
    cumulative = 0.0
    for level, p in enumerate(distribution):
        cumulative += p / total
        if cumulative >= q - 1e-9:
            return level
    return len(distribution) - 1


def tier_index(tiers: list[dict[str, Any]], name: str) -> int:
    for i, tier in enumerate(tiers):
        if tier["name"] == name:
            return i
    raise KeyError(name)


def parse_override(prompt: str) -> str | None:
    match = _OVERRIDE.search(prompt)
    return match.group(1).lower() if match else None


def decide(judgments: Judgments, config: dict[str, Any]) -> Decision:
    tiers = config["tiers"]
    q = float(config["quantile"])

    levels: dict[str, int] = {}
    normalized: dict[str, float] = {}
    for qid, count in SCORE_LEVELS.items():
        levels[qid] = quantile_level(judgments.distributions[qid], q)
        normalized[qid] = levels[qid] / (count - 1)

    # The capability question names the least capable model that can do the
    # task, with one level per tier, so it is the difficulty directly.
    difficulty = round(normalized["capability"], 3)
    reasons = [f"{k} level {levels[k]}/{SCORE_LEVELS[k] - 1}" for k in SCORE_LEVELS]
    index = len(tiers) - 1
    for i, tier in enumerate(tiers):
        if difficulty <= float(tier["max_difficulty"]) + 1e-9:
            index = i
            break

    floor = config.get("risk_floor") or {}
    if floor and normalized["risk"] >= float(floor.get("min_risk", 1.1)):
        floor_index = tier_index(tiers, floor["tier"])
        if floor_index > index:
            index = floor_index
            reasons.append("raised to risk floor")

    if judgments.source == "heuristic":
        bump = int(config.get("heuristic_tier_bump", 0))
        if bump:
            index = min(len(tiers) - 1, index + bump)
            reasons.append(f"heuristic +{bump} tier")

    return Decision(
        tier=tiers[index],
        difficulty=difficulty,
        source=judgments.source,
        context_dependent=judgments.context_dependent,
        complexity_level=levels["complexity"],
        reasons=reasons,
        detail=judgments.detail,
    )


def route(prompt: str, config: dict[str, Any], cwd: str | None = None) -> Decision | None:
    """Route a prompt. Returns None when routing is off for this prompt."""
    override = parse_override(prompt)
    if override == "off":
        return None
    if override:
        try:
            tier = config["tiers"][tier_index(config["tiers"], override)]
        except KeyError:
            pass
        else:
            return Decision(tier=tier, difficulty=-1.0, source="override", complexity_level=4,
                            reasons=[f"forced by [jev:{override}]"])

    try:
        judgments = ask_jev(prompt, cwd, config["jev"])
    except JevUnavailable as exc:
        judgments = heuristic(prompt)
        judgments.detail["jev_error"] = str(exc)
    return decide(judgments, config)


def session_rank(tier_or_model: str | None, tiers: list[dict[str, Any]]) -> int | None:
    """Rank of the session's tier, from a tier name or a model id like claude-opus-5-5."""
    if not tier_or_model:
        return None
    value = tier_or_model.lower()
    try:
        return tier_index(tiers, value)
    except KeyError:
        pass
    # Only the model is known: assume the strongest tier that uses it.
    for model in MODEL_RANK:
        if model in value:
            matches = [i for i, t in enumerate(tiers) if t["model"] == model]
            return max(matches) if matches else None
    return None


def should_delegate(decision: Decision, session: int | None, config: dict[str, Any]) -> tuple[bool, str]:
    """Whether the main session should hand this task to the routed subagent."""
    delegate = config.get("delegate") or {}
    if not delegate.get("enabled", True):
        return False, "delegation disabled"
    if decision.context_dependent >= 0.5:
        return False, "request depends on the conversation"

    routed = tier_index(config["tiers"], decision.name)
    if session is not None and routed == session:
        return False, "session already runs this tier"
    if session is not None and routed > session:
        return True, "task needs a stronger tier than the session"

    if decision.complexity_level < int(delegate.get("down_min_complexity_level", 1)):
        return False, "too small to be worth a subagent"
    return True, "a cheaper tier can do this task"
