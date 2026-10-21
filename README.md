# jev-router

Automatic model and effort routing for [Claude Code](https://code.claude.com), powered by
[TypeSafe Jev](https://docs.typesafe.ai).

jev-router reads each prompt and estimates how hard and how risky the task is. It then sends the
task to the cheapest Claude model and effort level that can still do it well. When it is unsure,
it picks the stronger option, because saving money must not cost quality.

Example (the numbers are illustrative):

```
you> fix the typo in README.md
jev-router: haiku-low (difficulty 0.20, jev; delegating)

you> the checkout page double-charges some users in production, find out why
jev-router: opus-high (difficulty 0.58, jev; delegating)
```

## How it works

Claude Code hooks cannot change the model or effort of a session that is already running. So
jev-router works in two places:

1. **Inside a session (plugin hook).** On every prompt, a `UserPromptSubmit` hook asks Jev four
   questions about it, in a single request. It maps the answers to a tier, then tells Claude to
   hand the task to a subagent pinned to that tier's model and effort. The plugin ships one
   subagent per tier (`jev-router:haiku-low` … `jev-router:opus-xhigh`).
2. **At session start (`jev-claude` launcher).** `jev-claude "<prompt>"` routes the first prompt
   and starts `claude --model <model> --effort <effort>`. The whole session then runs on that
   tier, with the full conversation context.

### The judgments

Jev answers these questions about each prompt. All four go in one request and run in parallel.

| Question | Type | Levels |
| --- | --- | --- |
| `complexity`: how much work the task is | Score | conversational reply → small edit → few-file task → large change → architecture or subtle bugs |
| `reasoning`: how much careful thinking it needs | Score | direct → few steps → careful analysis → deep reasoning |
| `risk`: what a subtle mistake would cost | Score | read-only → reversible dev change → costly (APIs, auth, payments) → production, security, or irreversible |
| `context_dependent`: whether it only makes sense with the earlier conversation | Noul | probability of yes |

### From judgments to a tier

- **Unsure answers count as the harder level.** Each Score is read at the 85th percentile of its
  probability distribution, not at its mean. If Jev puts 70% on "few-file task" and 30% on
  "large change", the router treats the task as a large change.
- **Weighted difficulty.** `0.5 × complexity + 0.3 × reasoning + 0.2 × risk`, each scaled to
  0–1. A single hard dimension still counts: the difficulty is never below
  `0.8 × max(complexity, reasoning)`.
- **Tiers.** The task goes to the first tier whose `max_difficulty` is at or above its difficulty:

  | Tier | Model | Effort | Max difficulty |
  | --- | --- | --- | --- |
  | `haiku-low` | haiku | low | 0.22 |
  | `sonnet-medium` | sonnet | medium | 0.45 |
  | `sonnet-high` | sonnet | high | 0.60 |
  | `opus-high` | opus | high | 0.82 |
  | `opus-xhigh` | opus | xhigh | 1.00 |

- **Risk floor.** If the risk is at the top level (production, security, irreversible), the task
  never goes below `opus-high`.
- **Offline fallback.** Without a TypeSafe API key, or if Jev does not answer within 4 seconds,
  a built-in keyword heuristic gives the same four judgments. Because the heuristic is less
  reliable, its pick moves up one tier.

### When it delegates

The main session hands the task to the routed subagent when one of these is true:

- the task needs a **stronger** tier than the session runs on, or
- a **cheaper** tier can do it, and it is real work (a small edit or more). Trivial questions are
  answered in the main session, because starting a subagent would cost more than answering.

It does **not** delegate when:

- the session already runs the routed tier, or
- the prompt depends on the earlier conversation ("yes, do that", "fix it", "continue"). A
  subagent does not see the conversation, so this protects quality.

The session's tier comes from the `jev-claude` launcher, or else from the model of the last
reply in the session transcript.

If a subagent replies with `ESCALATE: <reason>`, or its result fails a check, the main session
reruns the task on the next stronger tier.

## Install

### 1. Get a TypeSafe API key (recommended)

Create a key at [console.typesafe.ai](https://console.typesafe.ai) and export it in your shell
profile, so Claude Code and its hooks can read it:

```sh
export TYPESAFE_API_KEY="ts-..."
```

Without a key, jev-router still works, using the offline heuristic.

### 2. Install the plugin

Requirements: Claude Code 2.1.251 or later, and `python3` 3.9 or later on `PATH`. The plugin
has no Python dependencies.

In Claude Code:

```
/plugin marketplace add hasanzaman22/jev-model-effort-router
/plugin install jev-router@jev-router
```

Or from a terminal:

```sh
claude plugin marketplace add hasanzaman22/jev-model-effort-router
claude plugin install jev-router@jev-router
```

Restart Claude Code. Your next prompt shows a `jev-router: …` line.

To install it for a whole team, add this to the project's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "jev-router": { "source": { "source": "github", "repo": "hasanzaman22/jev-model-effort-router" } }
  },
  "enabledPlugins": { "jev-router@jev-router": true }
}
```

### 3. Install the launcher (optional)

```sh
pipx install git+https://github.com/hasanzaman22/jev-model-effort-router
# or: pip install --user git+https://github.com/hasanzaman22/jev-model-effort-router
```

## Usage

The plugin needs no action: type prompts as usual.

**Start a session on the right tier:**

```sh
jev-claude "add pagination to the /users endpoint"
jev-claude "add pagination to the /users endpoint" -- --permission-mode acceptEdits   # args after -- go to claude
jev-claude --dry-run "redesign the cache layer"          # show the decision only
jev-claude --dry-run --json "redesign the cache layer"   # as JSON
```

**Override a single prompt:**

| Put this in the prompt | Effect |
| --- | --- |
| `[jev:off]` | no routing for this prompt |
| `[jev:opus-xhigh]` (any tier name) | force that tier |

**See past decisions:** each decision is appended to `decisions.jsonl` in the plugin data
directory (`~/.claude/plugins/data/<plugin id>/`), or in `~/.claude/jev-router/` when the
launcher runs outside Claude Code. A log record has the tier, difficulty, source, reasons, and a
hash of the prompt. It holds the prompt text only if you set `log_prompt_text`.

```sh
tail -n 5 ~/.claude/plugins/data/*jev-router*/decisions.jsonl
```

## Configure

Every setting has a default. To change any of them, put a JSON file in one of these places.
Later files override earlier ones, key by key:

1. `~/.claude/jev-router.json`: for all your projects
2. `<project>/.claude/jev-router.json`: for one project
3. the file named by `$JEV_ROUTER_CONFIG`

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | turn routing on or off |
| `jev.model` | `"jev-latest"` | TypeSafe model name |
| `jev.api_key_env` | `"TYPESAFE_API_KEY"` | environment variable that holds the key |
| `jev.timeout_seconds` | `4.0` | after this, use the heuristic |
| `quantile` | `0.85` | how strongly uncertainty pushes toward stronger tiers (higher = safer, costlier) |
| `weights` | `0.5 / 0.3 / 0.2` | weights of complexity, reasoning, and risk |
| `tiers[].max_difficulty` | see table above | tier thresholds. Tier names are fixed, because each one has a subagent. |
| `risk_floor` | `{"min_risk": 0.75, "tier": "opus-high"}` | the lowest tier allowed for risky work |
| `heuristic_tier_bump` | `1` | extra tiers added when the heuristic decides |
| `delegate.enabled` | `true` | if `false`, only show the decision and never delegate |
| `delegate.down_min_complexity_level` | `1` | the smallest complexity level worth delegating to a cheaper tier |
| `show_decision` | `true` | show the `jev-router: …` line |
| `log` / `log_prompt_text` | `true` / `false` | write decision logs, and whether to include the prompt text |

[`examples/jev-router.json`](examples/jev-router.json) is a more conservative setup.

## Quality and cost

- Every rule that chooses between cost and quality chooses quality: the percentile reading, the
  risk floor, the heuristic bump, and escalation.
- Each prompt costs one small Jev request: a few hundred input tokens (output tokens are free).
  The savings come when tasks run on Haiku or Sonnet instead of Opus.
- Delegation has overhead: the main session writes a brief and reads the result. If your
  session already runs on a cheap model, start hard tasks with `jev-claude`, or let the hook
  move them up to a stronger tier.
- **Check it on your own work.** The thresholds are a starting point. Look at
  `decisions.jsonl` and adjust `tiers[].max_difficulty` or `quantile` if the routing does not
  match what you would choose.

## Development

```sh
python3 -m unittest discover -s tests -t .
claude plugin validate .
echo '{"prompt":"rename foo to bar in utils.py","cwd":"."}' | python3 scripts/route_hook.py
```

Layout:

```
.claude-plugin/   plugin.json and marketplace.json
hooks/hooks.json  UserPromptSubmit hook
agents/           one subagent per tier (model and effort in the frontmatter)
jev_router/       router code (Python standard library only)
bin/jev-claude    launcher
tests/            unit tests (Jev is mocked)
```

## License

MIT
