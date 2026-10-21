# jev-effort-router

Automatic effort level for [Claude Code](https://code.claude.com), powered by
[TypeSafe Jev](https://docs.typesafe.ai).

![jev-effort-router picking an effort level for four prompts](docs/demo.gif)

<sub>Real Jev answers, shown with the `jev-claude --dry-run` launcher. In Claude Code the plugin
does the same for every prompt and switches the session's effort.</sub>

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
3. **When the turn ends**, it runs `/effort <level>`, so the session's own effort setting, what
   `/effort` and the prompt bar show, matches the routed level. A command cannot run while a
   prompt is being submitted, so this happens after the turn; step 2 already gave that turn the
   routed effort. The transcript shows a short `/effort` line each time the level changes.

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

- **The effort answer picks the level.** Its four levels match the effort levels one to one.
  The probability-weighted mean of Jev's answer, scaled to 0–1, is the difficulty, and the prompt
  gets the first level whose `max_difficulty` is at or above it. The thresholds lean upward: a
  prompt goes to `medium` once the mean passes 0.4 of a level, not halfway.

  | Level | Max difficulty |
  | --- | --- |
  | `low` | 0.133 |
  | `medium` | 0.40 |
  | `high` | 0.80 |
  | `xhigh` | 1.00 |

- **Risk floor.** The risk answer is read at the 85th percentile of its distribution, so an
  unsure answer counts as the riskier level. If that is the top level (production, security,
  irreversible), the effort is never below `high`.
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

Turning routing off leaves the session at the last effort it set; run `/effort` to pick another.
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
| `quantile` | `0.85` | how strongly an unsure risk answer counts as the riskier level |
| `tiers[].max_difficulty` | see table above | effort thresholds |
| `risk_floor` | `{"min_risk": 0.75, "tier": "high"}` | the lowest effort allowed for risky work |
| `heuristic_tier_bump` | `1` | extra effort levels added when the heuristic decides |
| `show_decision` | `true` | show the `jev-router: …` line |
| `log` / `log_prompt_text` | `true` / `false` | write decision logs, and whether to include the prompt text |

[`examples/jev-router.json`](examples/jev-router.json) is a more conservative setup.

## Results

Prompts were labeled by hand with the effort a careful engineer would choose: trivial questions
and small edits `low`, self-contained features `medium`, multi-component work, debugging, and
risky changes `high`, architecture and subtle concurrency `xhigh`. The thresholds were tuned on
the first 143 prompts. The other 54 were written afterwards and scored once, so they are the
honest number. Prompts that depend on the earlier conversation ("yes, do that") are counted
only for that flag, because they keep the current effort. All runs used Jev 1.14 on
OpenRouter.

| | Tuning set (143) | Held-out set (54) |
| --- | --- | --- |
| Expected effort | 131 (92%) | **47 (87%)** |
| One level higher | 11 | 6 |
| One level lower | 1 | 1 |
| Two or more levels off | 0 | 0 |
| "Depends on the conversation" flagged correctly | 155 / 160 | 60 / 60 |

Before the thresholds were tuned, the same held-out set scored 42 of 54 (78%), with 12 one level
higher.

Examples from the held-out set:

| Prompt | Picked |
| --- | --- |
| what does `git stash pop` do? | `low` |
| fix the typo 'lenght' in array_utils.py | `low` |
| add an endpoint that returns a single product by id | `medium` |
| implement magic-link login by email | `high` |
| truncate the audit_log table in production | `high` |
| we have a heisenbug where two threads corrupt the shared cache under load | `xhigh` |

The misses: "write a function that checks whether a string is a palindrome" got `low` instead of
`medium`. "run the tests" and "git pull and rebuild" got `medium`, and four small,
self-contained tasks got `high` (a `--dry-run` flag, a request timeout, an index migration, and
a rename script).

The labels are one person's judgment, and 54 prompts is a small sample, so treat these numbers
as a sanity check. Each request to Jev took about 1 second.

## Quality and cost

- The rules lean toward quality: thresholds that move up a level early, the risk floor, and
  the heuristic bump. In testing, a pick one level too high was about six times as common as
  one level too low.
- Each prompt costs one small Jev request: a few hundred input tokens (output tokens are free).
  The savings come from fewer thinking tokens on easy prompts.
- Changing the effort keeps the prompt cache on the Claude 5.5 models, so switching costs
  nothing extra there. On older models, the first request after a change reads the conversation
  without the cache.
- **Measured accuracy.** 87% of held-out prompts got the expected effort; see [Results](#results).
- **Check it on your own work.** The thresholds are a starting point. Look at
  `decisions.jsonl` and adjust `tiers[].max_difficulty` if the routing does not
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
