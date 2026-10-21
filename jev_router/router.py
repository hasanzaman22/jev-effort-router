"""Turn judgments about a prompt into an effort level."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .judgments import SCORE_LEVELS, JevUnavailable, Judgments, ask_jev, heuristic

# Per-prompt override: "[jev:off]" skips routing, "[jev:high]" forces an effort level.
_OVERRIDE = re.compile(r"\[jev:([a-z0-9-]+)\]", re.I)


@dataclass
class Decision:
    tier: dict[str, Any]
    difficulty: float
    source: str  # "jev", "heuristic", or "override"
    context_dependent: float = 0.0
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

    # The effort question has one level per effort setting. Its mean, scaled to
    # 0-1, is the difficulty: on held-out prompts it matched the expected level
    # more often than a quantile did, with the thresholds below.
    effort = judgments.distributions["effort"]
    mean = sum(i * p for i, p in enumerate(effort)) / (sum(effort) or 1.0)
    difficulty = round(mean / (SCORE_LEVELS["effort"] - 1), 3)
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
            reasons.append(f"heuristic +{bump} level")

    return Decision(
        tier=tiers[index],
        difficulty=difficulty,
        source=judgments.source,
        context_dependent=judgments.context_dependent,
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
            return Decision(tier=tier, difficulty=-1.0, source="override",
                            reasons=[f"forced by [jev:{override}]"])

    try:
        judgments = ask_jev(prompt, cwd, config["jev"])
    except JevUnavailable as exc:
        judgments = heuristic(prompt)
        judgments.detail["jev_error"] = str(exc)
    return decide(judgments, config)
