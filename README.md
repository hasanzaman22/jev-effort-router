# jev-effort-router

A Claude Code plugin that picks the effort level for every prompt you send. It asks
[TypeSafe Jev](https://docs.typesafe.ai) how much thinking the prompt needs and how risky it
is, then runs the turn at the lowest effort that should still get it right. Your model stays the
one you chose.

![Claude Code answering a quick question at low effort and a design question at xhigh](docs/demo.gif)

<sub>Recorded in Claude Code 2.1.306 with the plugin loaded. Each prompt gets a
`jev-effort-router: effort …` line, and the status line shows the effort in use.</sub>

Easy questions and small edits run at `low`, so they come back faster and spend fewer thinking
tokens. Debugging, risky changes, and design work get `high` or `xhigh`. When Jev is unsure, the
plugin leans toward more effort.

## Quick start

1. Get a Jev API key. The steps for OpenRouter are [below](#get-a-jev-key-from-openrouter).
2. Add it to your shell profile (`~/.zshrc` or `~/.bashrc`) and open a new terminal:

   ```sh
   export JEV_API_KEY="sk-or-v1-..."
   ```

3. In Claude Code, install the plugin:

   ```
   /plugin marketplace add hasanzaman22/jev-effort-router
   /plugin install jev-effort-router@jev-effort-router
   ```

4. Restart Claude Code and send a prompt.

You need Claude Code 2.1.306 or later and `python3` 3.9 or later on your `PATH`. The plugin has
no Python dependencies. It switches the effort through Claude Code mods, which are in early access
and may change between Claude Code releases.

## Get a Jev key from OpenRouter

1. Sign in at [openrouter.ai](https://openrouter.ai) with Google, GitHub, or email.
2. Open [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys), click
   **Create Key**, give it a name, and copy the key. It starts with `sk-or-`.
3. Add a little credit at [openrouter.ai/settings/credits](https://openrouter.ai/settings/credits).
   The key itself costs nothing, but OpenRouter bills each Jev request. Jev is
   $0.042 per million input tokens and output is free. One prompt sends about 830 tokens, which
   comes to roughly $0.000035, so $1 covers around 29,000 prompts.
4. Export the key as in the quick start.

If you would rather not pay anything, OpenCode Zen currently offers `jev-1.13-free` at no cost
for a limited time. A key from TypeSafe works too. The plugin reads the key's prefix and calls
the service that issued it, with that service's Jev model:

| Service | Where to get a key | Key starts with | Jev model used |
| --- | --- | --- | --- |
| OpenRouter | [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys) | `sk-or-` | `~typesafe/jev-latest` |
| OpenCode Zen | [opencode.ai](https://opencode.ai) console | `oc_sk_` | `jev-1.13-free` |
| TypeSafe | [console.typesafe.ai](https://console.typesafe.ai) | anything else | `jev-latest` |

`JEV_API_KEY` is checked first, then `TYPESAFE_API_KEY`, `OPENROUTER_API_KEY`, and
`OPENCODE_API_KEY`, so a key you already export under its service's usual name also works. When
OpenCode's free period ends, set `jev.model` to `jev-1.13` (see [Configure](#configure)).

Without any key the plugin still runs. It falls back to a keyword heuristic, which is less
accurate and picks one level higher to make up for it.

## Usage in Claude Code

Use Claude Code as usual. On every prompt the plugin adds a line like this to the conversation:

```
● jev-effort-router: effort low (difficulty 0.00, jev)
```

The status line at the bottom shows the effort in use, for example
`jev-effort-router: effort xhigh`. Claude Code's header and `/effort` keep showing your own
setting: the plugin sets the effort on each request of the turn and leaves that setting alone.
Subagents keep their own effort.

A follow-up that only makes sense with the earlier conversation, such as "yes, do that" or
"continue", keeps the current effort, so the work it continues gets the same care.

### Commands

| Command | What it does |
| --- | --- |
| `/jev-effort-router:status` | Shows whether routing is on and which Jev service it uses |
| `/jev-effort-router:disable` | Stops routing until you run `/jev-effort-router:enable` |
| `/jev-effort-router:enable` | Turns routing back on |

The on/off switch covers all your sessions and projects and survives restarts. It lives in
`~/.claude/jev-router/state.json` and wins over `enabled` in the config files. Routing is on
until you turn it off.

### Overriding one prompt

Put one of these anywhere in a prompt:

| Tag | Effect |
| --- | --- |
| `[jev:off]` | No routing for this prompt; your own `/effort` setting applies |
| `[jev:low]`, `[jev:medium]`, `[jev:high]`, `[jev:xhigh]` | Use that effort for this prompt |

### Checking past decisions

Every decision is appended to `~/.claude/jev-router/decisions.jsonl` with the effort (`tier`),
difficulty, source, reasons, and a hash of the prompt. The prompt text is only stored if you set
`log_prompt_text`.

```sh
tail -n 5 ~/.claude/jev-router/decisions.jsonl
```

### Installing for a team

Add this to the project's `.claude/settings.json`, and everyone who opens the project gets the
plugin:

```json
{
  "extraKnownMarketplaces": {
    "jev-effort-router": { "source": { "source": "github", "repo": "hasanzaman22/jev-effort-router" } }
  },
  "enabledPlugins": { "jev-effort-router@jev-effort-router": true }
}
```

Each person still needs their own Jev key.

## How it works

The plugin ships a Claude Code mod, `hooks/register.ts`, with two hooks. When you submit a
prompt, it runs the Python router in `jev_router/`, which sends Jev three questions about the
prompt in one request. When Claude Code then sends a request to the model, the mod sets the
chosen effort on it. A turn with tool calls makes several requests, and each of them gets the
same effort.

Jev answers these questions:

| Question | Type | Levels |
| --- | --- | --- |
| `effort`: how much thinking a strong assistant needs to get it right the first time | Score | minimal, moderate, careful, extended deep thinking |
| `risk`: what a subtle mistake would cost | Score | read-only; reversible dev change, including deleting temp files; costly (APIs, auth, payments); production, real user data, secrets, or irreversible |
| `context_dependent`: whether the prompt only makes sense with the earlier conversation | Noul | probability of yes |

Each Score level has a short description and a few generic examples, which keeps Jev from
splitting its answer between neighbouring levels.

The four effort levels map one to one onto `low`, `medium`, `high`, and `xhigh`. The router takes
the probability-weighted mean of Jev's effort answer, scales it to a difficulty between 0 and 1,
and picks the first level whose `max_difficulty` is at or above it:

| Level | Max difficulty |
| --- | --- |
| `low` | 0.133 |
| `medium` | 0.40 |
| `high` | 0.80 |
| `xhigh` | 1.00 |

These thresholds move up a level early. A prompt reaches `medium` once the mean passes 0.4 of a
level, for example.

The risk answer is read at its 85th percentile, so an unsure answer counts as the riskier level.
If that lands on the top level (production, security, irreversible), the effort is never below
`high`.

If Jev does not answer within 4 seconds, or there is no key, the keyword heuristic decides
instead and its pick moves up one level.

## Results

I labeled prompts by hand with the effort a careful engineer would choose: `low` for trivial
questions and small edits, `medium` for self-contained features, `high` for work across several
components, debugging, and risky changes, and `xhigh` for architecture and subtle concurrency.
The thresholds were tuned on the first 143 prompts. The other 54 were written afterwards and
scored once, so they give the fairer number. Prompts that depend on the earlier conversation keep
the current effort, so they only count toward the conversation flag. All runs used Jev 1.14 on
OpenRouter.

| | Tuning set (143) | Held-out set (54) |
| --- | --- | --- |
| Expected effort | 131 (92%) | 47 (87%) |
| One level higher | 11 | 6 |
| One level lower | 1 | 1 |
| Two or more levels off | 0 | 0 |
| Conversation flag correct | 155 / 160 | 60 / 60 |

With the earlier thresholds the held-out set scored 42 of 54 (78%), with 12 picks one level
higher.

Some held-out prompts and what they got:

| Prompt | Effort |
| --- | --- |
| what does `git stash pop` do? | `low` |
| fix the typo 'lenght' in array_utils.py | `low` |
| add an endpoint that returns a single product by id | `medium` |
| implement magic-link login by email | `high` |
| truncate the audit_log table in production | `high` |
| we have a heisenbug where two threads corrupt the shared cache under load | `xhigh` |

The misses: "write a function that checks whether a string is a palindrome" got `low` where I
expected `medium`. "run the tests" and "git pull and rebuild" got `medium`, and four small,
self-contained tasks got `high`: a `--dry-run` flag, a request timeout, an index migration, and a
rename script.

One person wrote the labels and 54 prompts is a small sample, so read these numbers as a sanity
check. Each Jev request took about a second.

## Cost and quality

Each prompt costs one Jev request of about 830 input tokens, and Jev charges nothing for output.
The savings come from fewer thinking tokens on easy prompts.

On the Claude 5.5 models, changing the effort keeps the prompt cache. On older models the first
request after a change reads the conversation without it.

In testing, a pick one level too high was about six times as common as one level too low. The
early thresholds, the risk floor, and the heuristic's extra level all push the same way.

The thresholds are a starting point. If the effort it picks doesn't match what you would choose,
look at `decisions.jsonl` and adjust `tiers[].max_difficulty`.

## Configure

Every setting has a default. To change one, put a JSON file in any of these places. Later files
override earlier ones, key by key:

1. `~/.claude/jev-router.json` for all your projects
2. `<project>/.claude/jev-router.json` for one project
3. the file named by `$JEV_ROUTER_CONFIG`

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Turn routing on or off |
| `jev.api_key_env` | `"JEV_API_KEY"` | Environment variable checked first for the key |
| `jev.provider` | from the key prefix | `"typesafe"`, `"openrouter"`, or `"opencode"` |
| `jev.endpoint` | the provider's | Jev request URL, for a proxy or another service |
| `jev.model` | the provider's | Jev model name, such as `"typesafe/jev-1.14"` to pin a version on OpenRouter |
| `jev.timeout_seconds` | `4.0` | How long to wait for Jev before using the heuristic |
| `quantile` | `0.85` | How strongly an unsure risk answer counts as the riskier level |
| `tiers[].max_difficulty` | see the table above | Effort thresholds |
| `risk_floor` | `{"min_risk": 0.75, "tier": "high"}` | The lowest effort allowed for risky work |
| `heuristic_tier_bump` | `1` | Extra levels added when the heuristic decides |
| `show_decision` | `true` | Show the `jev-effort-router: effort …` line |
| `log` / `log_prompt_text` | `true` / `false` | Write decision logs, and whether to include the prompt text |

[`examples/jev-router.json`](examples/jev-router.json) is a more cautious setup.

## Development

```sh
python3 -m unittest discover -s tests -t .
claude plugin validate .
echo '{"prompt":"rename foo to bar in utils.py","cwd":"."}' | python3 scripts/route_switch.py
claude --plugin-dir .    # a Claude Code session with the plugin loaded from this folder
vhs docs/demo.tape       # re-record the demo GIF
```

The repository also has a small `jev-claude` launcher (`bin/jev-claude`) that routes one prompt
and starts `claude --effort <level>` with it. `jev-claude --dry-run "<prompt>"` prints the
decision without starting anything, which is handy for testing thresholds.

```
.claude-plugin/   plugin.json and marketplace.json
hooks/            the mod: hooks.json names register.ts, which sets the effort
commands/         /jev-effort-router:enable, :disable, and :status
jev_router/       router code (Python standard library only)
scripts/          entry points the mod and the commands run
bin/jev-claude    terminal launcher
tests/            unit tests (Jev is mocked)
```

## License

MIT
