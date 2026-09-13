# Spark API, Muse Code, Cursor Headless — tero research

Checked **13 Sep 2026**. Spark AR is a different, discontinued product and is out of scope.

**Verdict:** do not add raw Meta Model API as an `AgentProvider`. Add Muse Code (`muse exec --json`) and Cursor CLI (`agent -p --output-format stream-json`) the same way Codex and Gemini already work. Spark is the model. Muse Code is the harness that uses it.

```text
tero Orchestrator (AgentProvider.run)
 ├── muse exec --json     → Meta Model API (api.meta.ai/v1) → Muse Spark 1.3
 ├── agent -p stream-json → Cursor (Composer / subscription models)
 └── opencode (already)   → can point at api.meta.ai without a new adapter
```

## 1. Meta Spark / Model API

Host: `https://api.meta.ai/v1`. Env for direct calls: **`MODEL_API_KEY`** (not the Muse Code key).

Three wire formats:

| Surface | Style | State |
|---|---|---|
| Responses | OpenAI Responses | `previous_response_id` |
| Chat Completions | OpenAI-compatible | client-managed |
| Messages | Anthropic-compatible | client-managed |

Claude Code on Spark uses Messages + `ANTHROPIC_AUTH_TOKEN` (bearer), not `ANTHROPIC_API_KEY`. Pin every alias (`ANTHROPIC_MODEL`, opus/sonnet/haiku, subagent model) to a Spark id or it falls back to a Claude model Meta does not serve.

Current Spark ids: `muse-spark-1.3` (recommended), `muse-spark-1.2`, `muse-spark-1.1`. Context **1,048,576**. Reasoning tokens bill as output.

| Tier | Ids | Price / 1M tokens | Data |
|---|---|---|---|
| Standard | `muse-spark-1.3`, `1.2`, `1.1` | $0.15 cached / $1.25 in / $4.25 out | not used for training |
| Contributor | `muse-spark-1.3-contributor`, `1.2-contributor` | $0.002 / $0.10 / $0.20 | Meta may train on prompts |

Do not default tero workspaces to Contributor. Muse Glimmer (30B, Apache 2.0) is self-hosted and is **not** on Model API.

This API has no cwd, no shell, no approvals. It is not a player. Prefer Muse Code, or an OpenCode preset pointed at `api.meta.ai`.

- https://dev.meta.ai/docs/getting-started/overview
- https://ai.developer.meta.com/docs/models
- https://dev.meta.ai/docs/pricing-rate-limits
- https://dev.meta.ai/docs/coding-agents
- https://developer.meta.com/ai/resources/blog/build-with-muse-spark

## 2. Muse Code (official Spark harness)

Binary: `muse`. Interactive TUI: `muse`. Headless: `muse exec`. Default model in Muse Code docs was still `muse-spark-1.2` at check time; API examples default to `muse-spark-1.3`.

Auth order: **`META_API_KEY`** > stored key > browser login. CI: env only, never argv.

`--yolo` is required for unattended runs. It expands to `--disable-approval --disable-sandbox --trust-workspace`. Default approval is `on-request`; in `exec` there is no UI, so the process hangs.

```bash
muse exec --json --yolo --trust-workspace \
  --workspace "$WD" \
  --model muse-spark-1.3 \
  --max-model-steps "$MAX_TURNS" \
  --prompt-file /tmp/tero-prompt.txt
```

| tero field | Muse flag |
|---|---|
| prompt | positional or `--prompt-file` |
| system_prompt | prepend; trusted workspace also loads skills/rules |
| working_dir | `--workspace` |
| max_turns | `--max-model-steps` (exit 1 at cap) |
| stream | `--json` JSONL on stdout |
| ephemeral | `--no-session-log` (kills resume/export) |
| resume | `muse exec --session-id <uuid> "Continue"` |

Exit codes: `0` turn finished (not “the patch is correct”), `1` fail/cancel/step cap, `2` usage error, `130/143` signal.

Replay log: `~/.local/share/muse/sessions/YYYY/MM/DD/…/session.jsonl`.

Linux sandbox needs bubblewrap and a non-musl build. In CI without it, every sandboxed shell dies as an environment failure — another reason to `--yolo` / `--disable-sandbox` inside an already isolated tero worktree.

Muse can fan subagents into **its own** worktrees. Pin `--workspace` to the tero worktree and do not enable a second isolation layer in v1.

- https://dev.meta.ai/docs/muse-code
- https://dev.meta.ai/docs/muse-code/auth
- https://dev.meta.ai/docs/muse-code/configuration
- https://dev.meta.ai/docs/muse-code/extending
- https://developer.meta.com/ai/resources/blog/build-with-muse-code

## 3. Cursor Headless

Two surfaces. v1 for tero: **CLI**, same shape as Gemini. Later: Python `cursor_sdk` (`Agent.create` / `send` / `wait`) for resume and cloud.

```bash
export CURSOR_API_KEY=...
agent -p --force --trust \
  --output-format stream-json \
  --workspace "$WD" \
  --model composer-2.5 \
  "$PROMPT"
```

`--force` = `--yolo`. `--trust` skips the workspace prompt. Do **not** pass `--worktree` — tero already isolates via `WorktreeManager`.

`stream-json` events: `system.init` → `assistant` → `tool_call` started/completed → terminal `result`. `thinking` is suppressed in print mode. On failure: non-zero exit, stream may end without `result`, error on stderr.

Known 2026 issues: `-p` sometimes never exits after a finished answer; MCP tools listed by `mcp list-tools` are not injected in print mode. Use tero’s existing timeout. Do not promise MCP in v1.

SDK notes: local agents auto-approve tools; model is required for local; keep `setting_sources` empty so user/team rules do not leak into a service run.

Cursor AUP (11 Aug 2026) restricts some non-human access. CLI/SDK are the official automation surfaces. Do not proxy Cursor as a public backend.

- https://cursor.com/docs/cli/headless
- https://cursor.com/docs/cli/reference/parameters
- https://cursor.com/docs/cli/reference/output-format
- https://cursor.com/docs/cli/reference/authentication
- https://cursor.com/docs/sdk/typescript
- https://cursor.com/docs/sdk/python

## 4. How to add them to tero

Current contract: `AgentProvider.run(prompt, system_prompt, working_dir, max_turns, model)` → JSONL → `AdaptedMessage`, via `run_subprocess_jsonl`.

1. `src/providers/muse.py` — Codex-shaped. `--yolo` default. `--prompt-file` for large prompts. Auth: `META_API_KEY`.
2. `src/providers/cursor.py` — Gemini-shaped. `agent -p --force --trust --output-format stream-json`. Auth: `CURSOR_API_KEY`.
3. Registry + `PROVIDER_PRESETS` in `src/menu.py`.
4. Optional, no new adapter: OpenCode preset `meta` / `muse-spark-1.3` at `https://api.meta.ai/v1`.

Role split: player = Muse or Cursor; keep Codex as coach/judge. Do not let Spark grade Spark.

## 5. Current model catalog (same check date)

Pinned in menu / runtime picker / defaults. Official ids, not marketing names.

### Anthropic (Claude Code)

| Label | Id | Window | Notes |
|---|---|---|---|
| Fable 5.1 | `claude-fable-5-1` | 1M | top Mythos-class; adaptive thinking always on |
| Opus 5 | `claude-opus-5` | 1M | Anthropic’s default “start here” |
| Sonnet 5 | `claude-sonnet-5` | 1M | current balanced |
| Haiku 4.5 | `claude-haiku-4-5` | 200K | current fast tier; no Haiku 5 yet |

Aliases `sonnet` / `opus` / `haiku` / `fable` map to those ids in `claude_native.py`. Escalation constants: Sonnet 5, Opus 5.

https://docs.anthropic.com/en/docs/about-claude/models/overview

### OpenAI (Codex CLI)

| Label | Id | Window | Notes |
|---|---|---|---|
| GPT-6 Astra | `gpt-6-astra` | 1.05M | newest flagship on the API page |
| GPT-5.6 Sol | `gpt-5.6-sol` | 1.05M | `gpt-5.6` alias routes here; default tero judge |
| GPT-5.6 Terra | `gpt-5.6-terra` | 1.05M | everyday / old-5.5 slot |
| GPT-5.6 Luna | `gpt-5.6-luna` | 1.05M | cheap high-volume |

https://developers.openai.com/api/docs/models

### Gemini (Gemini CLI)

| Label | Id | Window | Notes |
|---|---|---|---|
| Gemini 3.1 Pro | `gemini-3.1-pro-preview` | 1M | still the Pro flagship |
| Gemini 3.8 Flash | `gemini-3.8-flash` | 1M | current Flash (Sep 2026) |
| Gemini 3.5 Flash-Lite | `gemini-3.5-flash-lite` | 1M | replaces shut-down `gemini-3.1-flash-lite-preview` |

https://ai.google.dev/gemini-api/docs/pricing
https://ai.google.dev/gemini-api/docs/changelog
