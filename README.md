# jev-effort-router

Automatic effort level for [Claude Code](https://code.claude.com), powered by
[TypeSafe Jev](https://docs.typesafe.ai).

jev-router reads each prompt and estimates how much thinking it needs and how risky it is. It
then sets the Claude Code session's effort level to the lowest one that can still do the task
well. It never changes the model: you keep the model you chose. When it is unsure, it picks more
effort, because saving tokens must not cost quality.

Example (the numbers are illustrative):

```
you> fix the typo in README.md
jev-router: effort low (difficulty 0.00, jev)

you> the checkout page double-charges some users in production, find out why
jev-router: effort high (difficulty 0.67, jev)
```

## How it works

The plugin includes a Claude Code mod (`hooks/register.ts`) with two hooks:

1. **When you submit a prompt**, it runs the Python router, which asks Jev three questions about
   the prompt in a single request and maps the answers to an effort level.
2. **When Claude Code sends a request to the model**, it sets that effort on the request. This
   covers every request of the turn, tool calls included. Subagents keep their own settings, and
   a model without effort levels is left alone.

The session keeps that effort until a prompt routes elsewhere. A prompt that only makes sense
with the earlier conversation ("yes, do that", "continue") keeps the current effort, so the work
it continues gets the same care. The status line shows the current effort, such as
`jev effort: high`.

The `jev-claude` launcher is optional: it routes a first prompt from the terminal and starts
`claude --effort <effort>` with it.

### The judgments

Jev answers these questions about each prompt. All three go in one request and run in parallel.

| Question | Type | Levels |
| --- | --- | --- |
| `effort`: how much thinking a strong assistant needs to get it right the first time | Score | minimal → moderate → careful → extended deep thinking |
| `risk`: what a subtle mistake would cost | Score | read-only → reversible dev change (including deleting temp files) → costly (APIs, auth, payments) → production, real user data, secrets, or irreversible |
| `context_dependent`: whether it only makes sense with the earlier conversation | Noul | probability of yes |

### From judgments to an effort level

Each Score level is a short description plus a few generic examples, which keeps Jev from
splitting its answer between neighbouring levels.

- **Unsure answers count as the harder level.** Each Score is read at the 85th percentile of its
  probability distribution, not at its mean. If Jev puts 70% on "moderate thinking" and 30% on
  "careful thinking", the router picks `high`.
- **The effort answer picks the level.** Its four levels match the effort levels one to one. The
  answer is scaled to a difficulty of 0, 0.33, 0.67, or 1, and the prompt gets the first level
  whose `max_difficulty` is at or above it:

  | Level | Max difficulty |
  | --- | --- |
  | `low` | 0.00 |
  | `medium` | 0.34 |
  | `high` | 0.67 |
  | `xhigh` | 1.00 |

- **Risk floor.** If the risk is at the top level (production, security, irreversible), the
  effort is never below `high`.
- **Offline fallback.** Without a Jev API key, or if Jev does not answer within 4 seconds,
  a built-in keyword heuristic gives the same judgments. Because the heuristic is less
  reliable, its pick moves up one level.

## Install

### 1. Get a Jev API key (recommended)

Jev is available from three services. A key from any of them works:

| Service | Where to get a key | Key starts with | Model used |
| --- | --- | --- | --- |
| TypeSafe | [console.typesafe.ai](https://console.typesafe.ai) | anything else | `jev-latest` |
| OpenRouter | [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys) | `sk-or-` | `~typesafe/jev-latest` |
| OpenCode Zen | [opencode.ai](https://opencode.ai) console | `oc_sk_` | `jev-1.13-free` |

Export the key in your shell profile, so Claude Code and its hooks can read it:

```sh
export JEV_API_KEY="sk-or-v1-..."
```

jev-router looks at the key's prefix and sends requests to the service that issued it, with that
service's Jev model. No other setup is needed. If you already export the key under its service's
usual name (`TYPESAFE_API_KEY`, `OPENROUTER_API_KEY`, or `OPENCODE_API_KEY`), that works too;
`JEV_API_KEY` is checked first.

OpenCode's `jev-1.13-free` is free for a limited time. With a paid OpenCode balance, set
`jev.model` to `jev-1.13` (see [Configure](#configure)).

Without a key, jev-router still works, using the offline heuristic.

### 2. Install the plugin

Requirements: Claude Code 2.1.306 or later, and `python3` 3.9 or later on `PATH`. The plugin
has no Python dependencies. The effort switching uses Claude Code mods, which are in early access
and may change between Claude Code releases.

In Claude Code:

```
/plugin marketplace add hasanzaman22/jev-effort-router
/plugin install jev-effort-router@jev-effort-router
```

Or from a terminal:

```sh
claude plugin marketplace add hasanzaman22/jev-effort-router
claude plugin install jev-effort-router@jev-effort-router
```

Restart Claude Code. Your next prompt shows a `jev-router: …` line, and the status line shows the
session's current effort.

To install it for a whole team, add this to the project's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "jev-effort-router": { "source": { "source": "github", "repo": "hasanzaman22/jev-effort-router" } }
  },
  "enabledPlugins": { "jev-effort-router@jev-effort-router": true }
}
```

### 3. Install the launcher (optional)

```sh
pipx install git+https://github.com/hasanzaman22/jev-effort-router
# or: pip install --user git+https://github.com/hasanzaman22/jev-effort-router
```

## Usage

The plugin needs no action: type prompts as usual.

**Turn routing on or off:**

| Command | Effect |
| --- | --- |
| `/jev-effort-router:disable` | stop routing every prompt, until you run `/jev-effort-router:enable` |
| `/jev-effort-router:enable` | route every prompt again, until you run `/jev-effort-router:disable` |
| `/jev-effort-router:status` | show whether routing is on and which Jev service it uses |

The switch applies to all your sessions and projects, and it lasts across restarts. It is saved in
`~/.claude/jev-router/state.json` and overrides `enabled` in the config files. Routing is on
until you first run one of these commands.

**Start a session at the right effort:**

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
| `[jev:xhigh]` (`low`, `medium`, `high`, or `xhigh`) | force that effort |

**See past decisions:** each decision is appended to `~/.claude/jev-router/decisions.jsonl`. A
log record has the effort (`tier`), difficulty, source, reasons, and a hash of the prompt. It holds the
prompt text only if you set `log_prompt_text`.

```sh
tail -n 5 ~/.claude/jev-router/decisions.jsonl
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
| `jev.api_key_env` | `"JEV_API_KEY"` | environment variable checked first for the key (then `TYPESAFE_API_KEY`, `OPENROUTER_API_KEY`, `OPENCODE_API_KEY`) |
| `jev.provider` | from the key prefix | `"typesafe"`, `"openrouter"`, or `"opencode"` |
| `jev.endpoint` | the provider's | Jev request URL, for a proxy or a new service |
| `jev.model` | the provider's | Jev model name, such as `"typesafe/jev-1.14"` to pin a version on OpenRouter |
| `jev.timeout_seconds` | `4.0` | after this, use the heuristic |
| `quantile` | `0.85` | how strongly uncertainty pushes toward more effort (higher = safer, costlier) |
| `tiers[].max_difficulty` | see table above | effort thresholds |
| `risk_floor` | `{"min_risk": 0.75, "tier": "high"}` | the lowest effort allowed for risky work |
| `heuristic_tier_bump` | `1` | extra effort levels added when the heuristic decides |
| `show_decision` | `true` | show the `jev-router: …` line |
| `log` / `log_prompt_text` | `true` / `false` | write decision logs, and whether to include the prompt text |

[`examples/jev-router.json`](examples/jev-router.json) is a more conservative setup.

## Quality and cost

- Every rule that chooses between cost and quality chooses quality: the percentile reading, the
  risk floor, and the heuristic bump.
- Each prompt costs one small Jev request: a few hundred input tokens (output tokens are free).
  The savings come from fewer thinking tokens on easy prompts.
- Changing the effort keeps the prompt cache on the Claude 5.5 models, so switching costs
  nothing extra there. On older models, the first request after a change reads the conversation
  without the cache.
- **Measured accuracy.** On 54 held-out coding prompts labeled by hand (Jev 1.14), the router
  picked the expected effort for 43, one level higher for 10, and one level lower for 1. It
  flagged all 60 prompts correctly as depending on the earlier conversation or not. The labels
  are one person's judgment, so treat this as a sanity check.
- **Check it on your own work.** The thresholds are a starting point. Look at
  `decisions.jsonl` and adjust `tiers[].max_difficulty` or `quantile` if the routing does not
  match what you would choose.

## Development

```sh
python3 -m unittest discover -s tests -t .
claude plugin validate .
echo '{"prompt":"rename foo to bar in utils.py","cwd":"."}' | python3 scripts/route_switch.py
claude --plugin-dir .    # a session with the plugin loaded from this folder
```

Layout:

```
.claude-plugin/   plugin.json and marketplace.json
hooks/            the mod: hooks.json names register.ts, which switches the effort
commands/         /jev-effort-router:enable, :disable, and :status
jev_router/       router code (Python standard library only)
bin/jev-claude    launcher
tests/            unit tests (Jev is mocked)
```

## License

MIT
