# Debugger Integration Plan

Spec: `docs/superpowers/specs/2026-04-09-debugger-integration-design.md`
Source prompts: `~/Desktop/Projects/Active/auto/debugger-research/debugger/`

---

## Phase 1: Provider Cleanup

- [ ] Remove Provider Black: delete `src/providers/ccg.py`, remove `"black"` and `"turbo"` from `PROVIDER_CHOICES` in `src/cli_entry.py:17`, remove the CCG import and factory block from `src/providers/__init__.py` (lines 4, 17, 44-47), remove `CcgEnv` from `__all__`, remove `ccg_env` parameter from `create_provider()` signature
- [ ] Delete `CcgEnv` class from `src/config.py` (the entire dataclass ~lines 56-280 and `_DEFAULT_MODELS` dict at ~line 12), change `player_provider` and `coach_provider` defaults from `"black"` to `"zai"` (~lines 299-300), remove any `BLACKBOX` entries from `_ENV_MAP`
- [ ] Clean menu presets in `src/menu.py`: delete `CCG_MODEL_PRESETS` (lines 16-24), replace `CODEX_MODEL_PRESETS` with only `{"Medium (default)": "", "High": "gpt-5.4"}`, replace `OPENCODE_MODEL_PRESETS` with only `{"MiniMax M2.5 (free)": "opencode/minimax-m2.5-free"}`, remove BLACK and TURBO from `PROVIDER_PRESETS` (lines 54-55), update `_model_presets_for_provider` to return `{}` instead of `CCG_MODEL_PRESETS` as fallback
- [ ] Fix all remaining references to black/ccg/turbo/CcgEnv/CcgProvider/BLACKBOX across src/ and tests/ — check `src/providers/chain.py`, `src/providers/registry.py`, `src/providers/claude_native.py` (_BLACKBOX_VARS), `src/coach_player.py`, `src/orchestrator.py`. Run `grep -rn "ccg\|CcgEnv\|CcgProvider\|blackbox\|BLACKBOX\|run_agent" src/ tests/ --include="*.py"` and fix each hit
- [ ] Run `python -m pytest tests/ -x -q --tb=short` to verify nothing broke after provider cleanup, fix any failures

## Phase 2: Config

- [ ] Add debugger config fields to `Config` dataclass in `src/config.py` after existing fields: `debug_player_provider: str = "zai"`, `debug_tester_provider: str = "claude"`, `debug_fixer_provider: str = "codex"`, `debug_player_model: str = ""`, `debug_tester_model: str = ""`, `debug_fixer_model: str = ""`, `debug_intensity: str = "medium"`, `debug_limit_mode: str = "infinite"`, `debug_limit_value: int = 10`, `debug_victory_threshold: int = 3`
- [ ] Add env mappings to `_ENV_MAP` in `src/config.py`: `G3_DEBUG_PLAYER_PROVIDER`, `G3_DEBUG_TESTER_PROVIDER`, `G3_DEBUG_FIXER_PROVIDER`, `G3_DEBUG_INTENSITY`, `G3_DEBUG_LIMIT_MODE`, `G3_DEBUG_LIMIT_VALUE` (int), `G3_DEBUG_VICTORY_THRESHOLD` (int)

## Phase 3: Debugger Prompts

- [ ] Create `src/debugger_prompts.py` with: `PLAYER_PROMPT_MAIN` (copy verbatim from `~/Desktop/Projects/Active/auto/debugger-research/debugger/prompt.md`), `PLAYER_PROMPT_ANCHOR` (copy verbatim from `anchor_prompt.md`), `PLAYER_PROMPT_RED_TEAM` (copy from `run_experiment.py` `FOCUSED_PROMPTS["red_team"]`), `PLAYER_PROMPT_BOUNDARY` (from `FOCUSED_PROMPTS["boundary"]`), `PLAYER_PROMPT_COMPLETENESS` (from `FOCUSED_PROMPTS["completeness"]`)
- [ ] Add `INTENSITY_PROMPTS` dict to `src/debugger_prompts.py`: `{"low": [PLAYER_PROMPT_MAIN], "medium": [PLAYER_PROMPT_MAIN, PLAYER_PROMPT_ANCHOR], "high": [PLAYER_PROMPT_MAIN, PLAYER_PROMPT_ANCHOR, PLAYER_PROMPT_RED_TEAM, PLAYER_PROMPT_BOUNDARY, PLAYER_PROMPT_COMPLETENESS]}`
- [ ] Add `TESTER_PROMPT` to `src/debugger_prompts.py`: instructs test engineer to write pytest per bug, SELF-CHECK (imports actual function? checks specific behavior? would pass if fixed? no mocking?), run tests, output JSON `[{"bug_id": N, "status": "confirmed|false_positive|invalid_test", "test_file": "path"}]`
- [ ] Add `FIXER_PROMPT` to `src/debugger_prompts.py`: instructs senior engineer to READ failing test, READ buggy code, PLAN minimal fix, IMPLEMENT, RUN test to verify pass, rules: fix only confirmed bugs, minimal changes, run full suite after all fixes, if fix breaks other tests adjust fix not tests

## Phase 4: Context Builder

- [ ] Create `src/debugger_context.py` adapted from `~/Desktop/Projects/Active/auto/debugger-research/debugger/file_strategy.py`: change `build_context(task_dir)` to `build_context(working_dir: str)`, add `discover_py_files()` that walks working_dir skipping `.git/venv/.venv/node_modules/__pycache__/.mypy_cache/.pytest_cache/.tox/`, remove benchmark-specific code (`metadata.json`, `historical_bug_density`, `_clean_counterpart_path`, `_render_buggy_clean_diff`), keep all rendering functions (`_format_with_line_numbers`, `_build_symbol_index`, `_parse_python_symbols`, `_build_hotspot_sections`, `_render_large_python_section`, budget allocation)

## Phase 5: Bug Parser

- [ ] Create `src/debugger_bugs.py` with `BugEntry` dataclass (`id`, `file`, `line`, `description`, `severity`, `status`), `parse_bugs(raw_output, start_id)` adapted from research `debugger.py` (keep `_extract_json_from_text`, `_strip_trailing_commas`, `_extract_prose_fallback`, per-line dedup, remove `_history_anchor`/`_is_shadow`), `merge_bugs()` that deduplicates by `(file, line)`
- [ ] Add `write_bugs_md(bugs, path, iteration)` and `write_final_report(bugs, path, duration_s, victory)` to `src/debugger_bugs.py` — write to `{working_dir}/bugs.md` with format from spec section 10
- [ ] Write `tests/test_debugger_bugs.py`: test `parse_bugs` with JSON array input, empty output, prose fallback; test `merge_bugs` dedup by (file, line); test `write_bugs_md` output format

## Phase 6: Main Debugger Loop

- [ ] Create `src/debugger.py` with `DebuggerResult` dataclass and `Debugger` class: `__init__(config)` creates providers via `create_provider()`, `run_sync()` calls `asyncio.run(self.run())`, main `run()` loop: while not should_stop — run_player, if 0 bugs increment clean_passes (3 = victory, skip tester/fixer), else reset clean_passes, run_tester, run_fixer if confirmed, git commit, update bugs.md
- [ ] Implement `_run_player()` in `src/debugger.py`: call `build_context(working_dir)`, iterate `INTENSITY_PROMPTS[intensity]`, for each prompt call provider.run() with `max_turns=1`, collect text output, `parse_bugs()`, return `merge_bugs(all_bugs)`
- [ ] Implement `_run_tester(bugs)` in `src/debugger.py`: build user prompt with context + bug list, call tester provider with `TESTER_PROMPT`, parse JSON response to split into confirmed/false_positive/invalid_test lists, fallback if no JSON: treat all as confirmed
- [ ] Implement `_run_fixer(confirmed)` in `src/debugger.py`: build user prompt with context + confirmed bugs, call fixer provider with `FIXER_PROMPT`
- [ ] Implement helpers in `src/debugger.py`: `_should_stop(iteration, start_time)` checks limit mode, `_git_commit(iteration, count)` runs git add -A and git commit, `_display_status()` and `_display_final()` print colored counters (red/grey/green), `_parse_tester_results(raw, bugs)` extracts JSON from tester output

## Phase 7: Menu and CLI

- [ ] Add `run_debugger_menu(config)` to `src/menu.py`: questionary selects for Player/Tester/Fixer provider (reuse `PROVIDER_PRESETS`), model selector per provider, intensity (`DEBUG_INTENSITY_PRESETS`: Low=1/Medium=2/High=5), limit (`DEBUG_LIMIT_PRESETS`: 5/10/20 iterations, 10/30/60 min, infinite), returns updated config
- [ ] Add `debug` subparser to `src/cli_entry.py` `build_parser()` after history_parser (~line 252): args `--working-dir`, `--player-provider`, `--tester-provider`, `--fixer-provider` (choices=PROVIDER_CHOICES), `--intensity` (choices=low/medium/high), `--limit` (int), `--time` (int), `--infinite` (flag), `--no-menu` (flag)
- [ ] Add `run_debug(args)` function to `src/cli_entry.py`: load Config, apply CLI overrides for debug_* fields, show `run_debugger_menu` unless --no-menu, create `Debugger(config)`, call `run_sync()`, exit 0 if success else 1
- [ ] Add `elif args.command == "debug"` to `main()` in `src/cli_entry.py` (~line 329): wrap `asyncio.run(run_debug(args))` in try/except KeyboardInterrupt

## Phase 8: Integration Test

- [ ] Run full test suite: `python -m pytest tests/ -x -q --tb=short` — all tests must pass
- [ ] Smoke test CLI: run `tero debug --no-menu --intensity low --limit 1 --working-dir .` and verify it starts Player, builds context, attempts LLM call (may fail on auth — that's ok, verify the flow runs)
- [ ] Smoke test menu: run `tero debug --working-dir .` and verify debugger submenu appears with all options
