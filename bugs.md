# Bugs Found in tero/src/

> Test proofs in `tests/test_bugs_found.py` — run with `pytest tests/test_bugs_found.py -v`
>
> **19 RED** (bugs proved) | **12 GREEN** (false positive / OK / baseline)

## CRITICAL

### A. `psutil` imported unconditionally but not a listed dependency
**File:** `src/coach_player.py:461`, `pyproject.toml:10-12`

`_kill_new_processes` does `import psutil` unconditionally but `psutil` is NOT in `pyproject.toml` dependencies. On any machine without psutil, the method crashes with `ModuleNotFoundError` on every Player turn.

```python
# src/coach_player.py — inside _kill_new_processes
import psutil  # crashes if not installed
```

```toml
# pyproject.toml — only pyyaml is listed
dependencies = ["pyyaml>=6.0"]
```

**Impact:** Every Player turn crashes with `ModuleNotFoundError: No module named 'psutil'` unless psutil happens to be installed. The import is not try/except guarded.

**Proof:** `TestBugA_PsutilImportCrash::test_psutil_either_in_deps_or_import_guarded` (RED)
**Proof:** `TestBugA_PsutilImportCrash::test_kill_new_processes_crashes_without_psutil` (RED)

---

## HIGH

### 1. `_SENTENCE_CONTINUATION_RE` rejects valid PHASE_COMPLETE lines
**File:** `src/batch_executor.py:98,128`

Regex `\.\s+\S` rejects any PHASE_COMPLETE line that contains a period followed by a space and word — e.g. `PHASE_COMPLETE: Setup phase. All code changes verified.` — treating it as "embedded in discussion" when it's actually a valid completion marker.

```python
# Line 98: the regex
_SENTENCE_CONTINUATION_RE = re.compile(r"\.\s+\S")

# Line 128: the check that rejects valid completions
match = _PHASE_COMPLETE_RE.search(text)
if match and not _SENTENCE_CONTINUATION_RE.search(match.group(0)):
    return [s.text for s in phase.steps]
```

**Impact:** Player correctly reports phase completion but BatchExecutor ignores it, causing unnecessary retries and wasted API calls.

**Proof:** `TestSentenceContinuationFalsePositive::test_valid_phase_complete_with_period_is_recognized` (RED)

---

### 2. pytest "failed," (with comma) not matched in test output parser
**File:** `src/bug_detector.py:203`

The parser checks `p == "failed"` but pytest outputs `"failed,"` (with trailing comma) when there are both failures and passes:

```
3 failed, 5 passed in 0.50s
```

Split: `["3", "failed,", "5", "passed", ...]` — `"failed,"` != `"failed"`.

```python
for i, p in enumerate(parts):
    if p == "failed" and i > 0:  # "failed," won't match
        try:
            return int(parts[i - 1])
        except ValueError:
            pass
```

**Impact:** BugReport always reports `1` failure instead of the actual count when tests have mixed pass/fail results.

**Proof:** `TestPytestFailedCommaNotMatched::test_mixed_failures_and_passes_counted_correctly` (RED)
**Proof:** `TestPytestFailedCommaNotMatched::test_two_failures_with_passes_counted_correctly` (RED)
**Proof:** `TestBugC_PytestFailedCountComma::test_failed_with_comma_is_parsed` (RED)
**Proof:** `TestBugC_PytestFailedCountComma::test_full_pytest_output_mixed` (RED)

---

### 3. `resume()` / `run()` returns `rounds_used=0` on error
**File:** `src/orchestrator.py:247,414`

The error handlers in both `run()` and `resume()` hardcode `rounds_used=0`, discarding how many rounds were actually completed before the failure.

```python
return OrchestratorResult(
    success=False,
    winner=None,
    bug_score=0,
    rounds_used=0,          # <-- should be round_num
    total_duration_s=duration,
    run_id=None,
    error=str(e),
)
```

**Impact:** Learning module receives incorrect `rounds_used` data, degrading quality of task classification and config recommendations.

**Proof:** `TestOrchestratorRoundsUsedOnFailure::test_run_error_handler_preserves_round_count` (RED)
**Proof:** `TestOrchestratorRoundsUsedOnFailure::test_resume_error_handler_preserves_round_count` (RED)

---

### B. `completed_steps` replaced instead of accumulated in `_run_phase`
**File:** `src/batch_executor.py:567`

On each retry attempt, `completed_steps` is reassigned from `parse_completed_steps()` instead of being accumulated. Steps completed in earlier attempts are silently forgotten.

```python
# Line 567 — REPLACES, doesn't accumulate
completed_steps = parse_completed_steps(result, phase)
```

After attempt 1 completes steps 1-3 and attempt 2 completes steps 4-5 (without re-listing 1-3), `completed_steps` only contains steps 4-5. Steps 1-3 are lost.

**Impact:** Multi-attempt phases silently lose progress. The retry prompt tells the Player that steps 1-3 are NOT done, causing unnecessary re-work and confusing the Player.

**Proof:** `TestBugB_CompletedStepsLostOnRetry::test_replacement_loses_previously_completed_steps` (RED — `2 != 5`)

---

## MEDIUM

### 4. Non-atomic file write in `write_checklist_back()`
**File:** `src/plan_tracker.py:520`

Writes the plan file directly without atomic rename. If the process is interrupted (SIGKILL, OOM, power loss), the file can be left partially written.

```python
path.write_text("\n".join(new_lines))
```

**Impact:** Progress data corruption on crash — plan file could become empty or truncated, losing all checkpoint state.

**Proof:** `TestWriteChecklistAtomicity::test_write_checklist_uses_atomic_rename` (RED)

---

### 5. `ProviderChain.run()` buffers all messages in memory before yielding
**File:** `src/providers/chain.py:75-82`

All provider output is collected into a list before being yielded. No buffer size limit exists.

```python
buffer: list = []
async for msg in provider.run(**kwargs):
    buffer.append(msg)  # unbounded
for msg in buffer:
    yield msg
```

**Impact:** Potential OOM if a verbose provider generates massive output before failing on rate limit.

**Proof:** `TestProviderChainUnboundedBuffer::test_chain_has_no_buffer_size_limit` (RED)
**Proof:** `TestProviderChainUnboundedBuffer::test_chain_does_not_limit_buffer` (RED)

---

### 6. `SessionManager.load()` reads JSON without error handling
**File:** `src/state.py:140-141`

If `session.json` is partially written or contains invalid JSON, this will raise an unhandled `json.JSONDecodeError`.

```python
def load(self) -> dict[str, Any]:
    if self._state_file.exists():
        self._state = json.loads(self._state_file.read_text())
    return self._state
```

**Impact:** Resume crashes with `json.JSONDecodeError` instead of gracefully starting fresh.

**Proof:** `TestSessionManagerLoadJsonError::test_load_handles_corrupted_json` (RED)
**Proof:** `TestSessionManagerLoadJsonError::test_load_handles_empty_file` (RED)

---

### 9. `ClaudeNativeProvider._clean_env()` leaks `ZAI_API_KEY`
**File:** `src/providers/claude_native.py:12-19,118-125`

`_BLACKBOX_VARS` strips Blackbox-specific env vars but doesn't strip `ZAI_API_KEY`. The Claude CLI may pick up wrong credentials.

```python
_BLACKBOX_VARS = [
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    ...
    # Missing: "ZAI_API_KEY"
]
```

**Impact:** Claude native CLI may fail authentication when `ZAI_API_KEY` is set.

**Proof:** `TestClaudeNativeZaiKeyLeak::test_zai_api_key_stripped_from_clean_env` (RED)
**Proof:** `TestClaudeNativeZaiKeyLeak::test_blackbox_vars_covers_all_provider_keys` (RED)

---

## LOW

### D. `_detect_test_command` has redundant substring check
**File:** `src/coach_player.py:1615-1618`

`'[tool.pytest'` already matches `'[tool.pytest.ini_options]'` as a substring, making the second OR condition dead code.

```python
if "[tool.pytest" in content or "[tool.pytest.ini_options]" in content:
    # second check is always True when first is True
```

**Impact:** Dead code, no functional impact. Confusing for maintainers.

**Proof:** `TestBugD_RedundantPytestCheck::test_source_has_redundant_check` (RED)

---

### E. `PhaseFailedError.phase` typed as `Phase` but `__str__` guards for `None`
**File:** `src/batch_executor.py:207,216`

The field annotation is `phase: Phase` (no `None` allowed) but `__str__` checks `if self.phase is None`. The type annotation should be `Phase | None`.

```python
@dataclass
class PhaseFailedError(Exception):
    phase: Phase  # annotation doesn't allow None

    def __str__(self) -> str:
        if self.phase is None:  # but code checks for None
            return ...
```

**Impact:** Static type checkers (mypy/pyright) won't flag potential None access. Misleading annotation.

**Proof:** `TestBugE_TypeAnnotationNoneGuard::test_type_annotation_allows_none` (RED)

---

## FALSE POSITIVES (GREEN — bugs don't exist)

| Test | Why GREEN |
|------|-----------|
| `test_valid_phase_complete_without_period_works` | Baseline works — bug is only with period |
| `test_embedded_phase_complete_in_discussion_is_rejected` | Correctly rejects embedded markers |
| `test_only_failures_counted_correctly` | `"failed"` without comma parses correctly |
| `test_id_mapping_survives_object_copy` | `replace()` keeps same identity within session |
| `test_id_mapping_breaks_on_re_parse` | Proves `id()` fragility is real but only across re-parses |
| `test_write_checklist_preserves_data_integrity` | Write works — only atomicity is the bug |
| `test_range_1_to_3_inclusive` | Phase range parsing is correct |
| `test_subprocess_run_handles_timeout` | subprocess.run kills on timeout |
| `test_batch_executor_runs_phases_sequentially` | No race condition — phases are sequential |
| `test_failed_without_comma_still_works` | Baseline `"N failed"` parsing works |
| `test_substring_proof` | Proves `[tool.pytest` is substring of `[tool.pytest.ini_options]` |
| `test_build_batch_prompt_does_not_redo_done_steps` | Prompt includes step text (from remaining list) |
