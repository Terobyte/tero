# tero

The latest commit in this repo reads:

```
7ec906f  ldb fix: 4 bug(s) via block-level runtime debugger
```

Those four bugs were in tero's own `src/config.py` and `src/menu.py`, and
they were found by `tero ldb` — the newest subsystem here — which decomposed
the functions into basic blocks, ran them with synthesized inputs, watched
variable values at block boundaries, and pointed at the first block where
the values went wrong. That is the bet this project explores: **coding
agents you don't babysit, kept honest by other agents and by actually
running the code — not by reading it and vibing.**

tero is a personal research bench for multi-agent coding loops. It treats
locally installed agent CLIs — Claude Code, Codex, OpenCode, Kilo, Gemini,
and GLM (via Z.AI's Anthropic-compatible endpoint) — as interchangeable
workers behind one provider interface, then wires them into adversarial
pipelines: one model implements, a different model reviews, a third judges.
124 Python files, ~37,000 lines (16.8k source / 20.1k tests), heavily
dogfooded: the commit log carries the tool's own auto-commits
(`fix(debugger): iteration 3 — 29 bug(s) fixed`), and the plan file in the
repo root is the plan tero executed to build `ldb` into itself.

## Two loops

| Command | What it does |
|---|---|
| `tero go` | Coach–player plan execution. Parses a markdown checklist plan (default: `requirements.md`), batches steps into phases, has a **player** agent implement each phase and a **coach/judge** agent review it, with retry feedback, provider fallback chains, context compaction, and an optional code-review pass. Run history lands in `.g3/knowledge` (`tero history`). |
| `tero ldb` | Runtime bug hunt — the interesting one. See below. |

Roles are freely mixable across providers, and the point is that they
differ: the default wiring has GLM-5.1 (via Z.AI) implementing and Codex
(pinned to `gpt-5.4`) judging the batches, so no model grades its own
homework. OpenCode and Kilo slots exist specifically for free models
(MIMO, Kimi) when the work is cheap.

## ldb: verify by execution, not by reading

Static LLM code review — the kind this repo used to ship as a `tero debug`
loop, now removed — has a blind spot: bugs that are invisible in the text
and only exist at runtime — off-by-ones in non-obvious
formulas, swallowed exceptions hiding real values. `tero ldb` is a
production-code adaptation of the LDB debugger from the ACL'24 paper
([FloridSleeves/LLMDebugger](https://github.com/FloridSleeves/LLMDebugger)):

```
 target function
      │
      ▼
 1. decompose into basic blocks        (vendored staticfg CFG builder)
      │
      ▼
 2. synthesize inputs with an LLM      (or take yours via --test "assert f(1,2)==3")
      │
      ▼
 3. execute in a subprocess,           (python -m trace + instrumented prints;
    capture variable values             logs in .ldb-trace/)
    at every block boundary
      │
      ▼
 4. Player LLM reads *values*, flags   ──▶  Tester writes a failing test
    the first incorrect block               ──▶  Fixer repairs + auto-commits (mode 3)
```

The LLM never has to guess what the code does — it sees what the code *did*.
Mode 2 stops after find+test (read-only); mode 3 adds fix and auto-commit.
Scope is either one function (`--file f.py --entry func`) or every public
function in the project (`--all`, nested classes included). Found bugs are
appended to `bugs.md` as feedback memory, so later runs know what earlier
runs already caught. On the paper's HumanEval benchmark this technique hit
98.2% with GPT-4o; this repo makes **no** such claim for production code —
the in-repo integration plan (`requirements.md`) explicitly expects less.

## Install

Requires Python 3.11+ and, for anything beyond `--help`, at least one agent
CLI installed and authenticated on your machine (`claude`, `codex`,
`opencode`, `kilo`, or `gemini`; the `zai` provider additionally needs
`claude-agent-sdk` and a `ZAI_API_KEY`).

```bash
git clone https://github.com/Terobyte/tero.git
cd tero
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

`pip install -e .` pulls everything `tero ldb` needs, including the `astor`
and `graphviz` that the vendored `staticfg` CFG builder imports and the
`questionary` behind the interactive menus.

```bash
# runtime-debug one function, with your own assert as the input
tero ldb --no-menu --file src/thing.py --entry compute --test "assert compute(2) == 4"

# runtime-debug every public function in a project
tero ldb --no-menu --all -w /path/to/project

# execute a markdown plan against a working dir
tero go --no-menu -p plan.md -w /path/to/project
```

Omit `--no-menu` to get an interactive settings menu instead.

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

Result at HEAD: **995 passed, 6 failed, 5 skipped in ~24 s** — offline,
no API keys needed. The failures are the repo's known-bug backlog, not your
environment: this workflow writes "bug-proof" tests that *demonstrate* each
confirmed-but-unfixed bug (the backlog lives in `bugs.md`), so the suite
stays red until a bug is actually fixed and a bug can't silently be
declared dead.

## Configuration

Resolution order: dataclass defaults → `.g3/config.yaml` in the working dir →
environment variables → CLI flags. Every knob is a field on `Config` in
`src/config.py` — provider/model per role, fallback chains, context limits
and compaction thresholds, debug intensity, ldb mode/timeouts. The design
docs, specs, and plans that drove each subsystem are in `docs/superpowers/`.

## Honest limitations

- **Research prototype, sample size: one.** This is a personal bench, built
  and validated by running it on itself and on its author's projects. No CI,
  no releases, no stability promises. The architecture is mid-simplification —
  `docs/superpowers/specs/2026-05-01-tero-simplification-design.md` strips it
  down to the player + coach + batch core — so expect churn.
- **The UI speaks Russian.** Menus, prompts to the user, and CLI status
  messages are partly in Russian ("Прервано.", "История пуста."). The agent
  prompts themselves are English.
- **`claude-agent-sdk` is an optional dependency**, undeclared on purpose —
  it is needed only for the `zai` provider.
- **Naming drift.** The package is `g3-coach`, the CLI is `tero`, state lives
  in `.g3/` — "g3" is the project's earlier codename and never got renamed.
- **Stowaway files from a different project.** `.env.example`,
  `config/settings.json`, and `src/ai/resume_tailor.py` belong to a job-application
  bot that was once developed in the same tree. They do **not** describe this
  project's configuration — ignore them.
- **Single-machine assumptions.** Config resolution will fall back to
  grepping `~/.zshrc` for exported keys; provider defaults assume specific
  CLIs and accounts installed the way the author has them.
- **ldb is Python-only** and needs code it can actually execute; functions
  whose inputs can't be synthesized or that touch heavy external state won't
  trace.
- **MIT licensed**; the vendored CFG builder in `src/ldb/staticfg/` carries
  its own Apache-2.0 license.
