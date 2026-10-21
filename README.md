# tero

**One model writes the code. A different model judges it. Code decides who wins.**

tero walks a markdown plan phase by phase over the coding-agent CLIs you already have installed. A *player* model implements the phase, your repo's own test suite gates it, a *different* model judges the diff and must answer in schema-validated JSON, a *tester* writes failing tests for what the judge found, and if the player stalls the loop swaps in a stronger model once. No model grades its own homework, and no verdict is ever scraped out of prose.

The loop is plain Python: phases, rounds, test gates, git checkpoints, a JSON-schema'd verdict and a fuse on rounds. Agents are interchangeable workers behind one provider interface: `muse`, `claude`, `codex`, `gemini`, `opencode`, `cursor`.

## One round, where the gates sit

```
             ┌──────────────────────────────────────────────────────────┐
             ▼                                                          │
 player ──▶ protected tests ──▶ test gate ──▶ judge (read-only) ──▶ verdict
 (edits)    untouched?          (exit code)    sees diff + raw          │
             │ no → restore      │ red → back    test output            ├─ SHIP       → tick plan boxes, commit phase
             └──▶ next round     └──▶ player                            ├─ NO_SHIP    → findings to player,
                                                                        │               or to tester → red test
                                                                        └─ NEEDS_USER → stop, ask you
 no progress (same red tests, nothing resolved, bad JSON):
   1st time → escalate player to the escalation model
   2nd time → roll the phase back to its checkpoint, stop the run
```

Details that are enforced in code, not asked for in a prompt:

- **Checkpoint per phase.** Each phase starts from `HEAD`; a stopped phase is reset to it.
- **The judge is read-only.** If its run changes the working tree anyway, tero restores the tree and logs `judge_tamper`.
- **The tester only writes tests.** Anything it touches outside test paths is reverted. Each test must fail (red) on the current code. If it passes, it is deleted. Red tests become *protected*: if the player edits one, tero restores it. If a tracked test file is deleted, tero puts it back.
- **The judge must close every open finding by id** (`F1`, `F2`, …) as `resolved` or `still_open`, and can drop a wrong red test with `route: discard_test`.
- **A bad reply gets one repair attempt.** After that it counts as an invalid answer. Provider errors get one retry.
- **The fuse:** a phase that hits `max_rounds` (default 15) stops with reason `fuse`.
- **Secrets guard:** a phase whose staged diff includes `.env`, `*.pem`, `id_rsa`, `*secret*` and similar files is rolled back, not committed.

## A run

A plan is markdown. Each `##` section that has checkboxes is one phase. Sections without checkboxes are skipped. Text above the first `##` is a preamble that every prompt gets. A plan with no `##` headings treats each checkbox as its own phase.

```markdown
# Parser

Python 3.11, no new dependencies.

## First phase

Do the parser.

- [ ] write parse()
- [ ] empty string returns []

## Second phase

- [x] already done
- [ ] still open
```

Phases whose boxes are all `[x]` are skipped, so re-running a half-done plan resumes it.

Before the first round tero checks that every role's CLI is ready, that the worktree is clean, and that your test suite is **green at the start**. A red start stops the run. Then it checks out `tero/<plan-slug>` (or reuses it) and works phase by phase. On `SHIP` it ticks that phase's boxes in the plan and commits with the phase title in lowercase as the message. Your main branch is never touched.

The console prints one line per loop event. Each agent call also gets one line per tool call (and a "still working" line after 30 s of silence). `-v` adds snippets of the agent's text:

```
  [раунд 1] player muse(muse-spark-1.3) …
    · Read src/parse.py
    · Edit src/parse.py
  player: done, изменений 2
  тесты …
  тесты: зелёные (код 0, 3.4 с)
  [раунд 1] judge codex(gpt-5.6-terra) …
  judge: NO_SHIP, blocking 1, advisory 0
    - src/parse.py:42: parse('') raises IndexError
  [раунд 1] tester gemini(gemini-3.8-flash) …
  tester: тестов написано 1, red-подтверждено 1
  [раунд 2] player muse(muse-spark-1.3) …
```

Every run leaves a ledger in `.tero/runs/<timestamp>-<slug>/`: `ledger.jsonl` (append-only events) plus per-round `player.json`, `judge.json`, `tester.json` and `tests.log` under `phase-NN/round-NN/`. tero adds `.tero/` to `.git/info/exclude`, so none of this gets committed.

## Install

Requires Python ≥ 3.11. Dependencies: `pyyaml`, `psutil`, `questionary`, `jsonschema`.

```bash
git clone https://github.com/Terobyte/tero.git
cd tero
pip install -e .
```

This installs the `tero` command.

## Quickstart

```bash
cd your-repo                       # a git repo with a clean worktree and green tests
tero go -p plan.md                 # interactive menu, then the loop
tero go -p plan.md --no-menu       # straight to the loop
tero history                       # recent runs: slug, stop reason, phases shipped/total, plan
```

```
tero go [--plan PLAN] [--working-dir DIR] [--no-menu] [--verbose]
        [--player P[:MODEL]] [--judge P[:MODEL]] [--tester P[:MODEL]]
        [--escalation P[:MODEL]] [--no-escalation]
        [--test-command CMD] [--test-command-scoped CMD]
        [--max-rounds N]
tero history [--limit N] [--working-dir DIR]
```

Short forms: `-p`, `-w`, `-v`, `-l`. The plan path is relative to `--working-dir` (default `.`). The default plan file is `requirements.md`. `tero history` shows 10 runs by default.

Role flags take `provider` or `provider:model`, for example `--judge claude:opus` or `--player cursor`. When no test command is set, tero auto-detects one: pytest (`pyproject.toml`, `pytest.ini`, `conftest.py` or `tests/*.py`), then npm (a `test` script), then `cargo test`, then `go test ./...`. The tester needs a *scoped* command, one that can run single files. pytest and npm have one, and you can set your own with `--test-command-scoped` (use `{files}` as the placeholder). Without one, findings meant for the tester go to the player.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | every phase shipped, or nothing left to do |
| `1` | startup error: unknown provider, CLI not ready, not a git repo, dirty worktree, plan not found, red tests at start |
| `2` | stopped mid-run: `stuck`, `fuse`, `user` (judge asked a question), `provider_unavailable`, `secrets` |
| `130` | Ctrl+C. The tree is left as is so you can inspect it. |

A dirty `tero/*` branch blocks the next start. tero prints the reset command for you to run. It does not run it.

## Configuration

Resolution order, later wins:

1. `Config` dataclass defaults
2. `<tero install>/.tero/config.yaml`
3. `~/.tero/config.yaml`
4. `<working-dir>/.tero/config.yaml`
5. `TERO_*` environment variables
6. CLI flags, then the interactive menu

Default roles:

| Role | Provider | Model |
|---|---|---|
| player | `muse` | `muse-spark-1.3` |
| judge | `codex` | `gpt-5.6-terra` |
| tester | `gemini` | `gemini-3.8-flash` |
| escalation | `claude` | `opus` (an empty provider turns escalation off) |

Timeouts: player 600 s, judge 600 s, tester 300 s, test gate 900 s. `max_rounds` is 15.

YAML files use a `defaults:` section with `Config` field names (including `player_effort`, `judge_effort`, … which are only settable here or in the menu). A `providers:` section overrides per-provider settings such as `command`, `default_model` or `type` (for running several accounts of one CLI):

```yaml
defaults:
  judge_provider: claude
  judge_model: opus
  test_command: "python -m pytest -q"
providers:
  codex:
    command: /opt/homebrew/bin/codex
```

Environment variables: `TERO_PLAYER_PROVIDER`, `TERO_PLAYER_MODEL`, `TERO_JUDGE_PROVIDER`, `TERO_JUDGE_MODEL`, `TERO_TESTER_PROVIDER`, `TERO_TESTER_MODEL`, `TERO_ESCALATION_PROVIDER`, `TERO_ESCALATION_MODEL`, `TERO_TEST_COMMAND`, `TERO_TEST_COMMAND_SCOPED`.

The interactive menu (`tero go` without `--no-menu`) lets you pick provider and model for each role, the plan file and the test command. **«Запустить» writes the whole current config to `~/.tero/config.yaml`**, so your last choices become the global defaults for the next run.

## Requirements

tero runs agent CLIs as subprocesses. Each one must be installed, on `PATH` and logged in. tero checks this before the first round.

| Provider | CLI it runs |
|---|---|
| `claude` | `claude` (Claude Code, `claude auth status` must pass) |
| `muse` | `claude`, pointed at the Muse API. The key comes from `MODEL_API_KEY` or that line in `~/.tero/secrets.env` |
| `codex` | `codex` |
| `gemini` | `gemini` |
| `opencode` | `opencode` |
| `cursor` | `agent` (Cursor CLI); aliases `cursor-cli`, `cursor-headless`, `headless` |

You only need the CLIs for the roles you actually assign.

## Tests

```bash
python3 -m pytest -q -p no:warnings tests
```

353 passed at the time of writing. The suite needs no agent CLIs: the loop runs against scripted fake agents and throwaway git repos.

## Limitations

- **This is a personal research bench, not a product.** The defaults reflect one person's set of accounts and models.
- **You bring the agents.** tero orchestrates CLIs you have already installed and authenticated. It has no API clients of its own.
- **The UI is in Russian:** menu, status lines and stop messages. Flags, exit codes and the ledger's event names are English.
- **The status lines are flat.** They are tagged `[раунд N]`, without the spec's `[фаза i/n · раунд r]` prefix, and there is no per-phase "SHIP in N rounds → commit" summary line yet. Use `ledger.jsonl` for that.
- **"Different model" is a default, not a rule.** Nothing stops you from setting player and judge to the same model.

## License

MIT, see [LICENSE](LICENSE).
