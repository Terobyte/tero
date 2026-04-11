"""Debugger prompt bundles for the debugger workflow."""

PLAYER_PROMPT_MAIN = '''You are a senior Python engineer doing a focused bug hunt. You will be shown one or more source files from a Python project. Your job is to find real behavioral bugs — not style issues or speculative edge cases.

## What bugs look like

The bugs in these files are **concrete behavioral failures** introduced intentionally. The most common patterns:

1. **Wrong value or variable** — code uses `1`, `None`, `self`, or the wrong variable where a specific value/object is required. E.g., `= 1` instead of `= right`, `self.foo` instead of `self.bar`.
2. **Missing line** — a state assignment, method call, or propagation step was forgotten. Code sets `obj.x` but forgets `obj.y`. Code updates one branch but not a symmetric one.
3. **Wrong condition or operator** — `>` vs `>=`, `and` vs `or`, `not` flipped, wrong comparison target.
4. **Shallow copy instead of deep copy** — mutation of shared state, missing `.copy()` or `.clone()` on a mutable object passed between components.
5. **Incomplete propagation** — a new field/parameter is added to one place but not forwarded through the full call chain.
6. **Wrong index or slice** — off-by-one, wrong sign in slice, using `[0]` instead of `[-1]` or vice versa.
7. **Off-by-one in loop bounds** — `range(n)` vs `range(n+1)`, `<` vs `<=` in loop conditions.
8. **Integer vs float division** — `//` used where `/` is needed or vice versa.
9. **Exception swallowed silently** — `except: pass` or bare except with no re-raise hides real errors.
10. **Mutable default argument** — `def f(x=[])` causes shared state across calls.

## How to find bugs

Perform **three explicit passes** over the code. Complete each pass before moving to the next.

---

### PASS 1: Structural Analysis

Scan for obvious code-pattern violations:

1. **Wrong variable or value** — code uses `1`, `None`, `self`, or the wrong variable where a specific value/object is required.
2. **Missing return** — every branch that should produce a value must return it. A bare expression on the last line (e.g., `entry["value"]`) is a missing return.
3. **Wrong operator** — `>` vs `>=`, `<` vs `<=`, `and` vs `or`, `not` flipped.
4. **Swapped operands** — key/value reversed in dict comprehension, source/destination reversed in assignment.
5. **Tautological or contradictory conditions** — `if i < n` when `i` comes from `range(n)` is always true; `if x and not x` is always false.
6. **Identical branches** — if an if-else chain has identical code in both branches, this is usually a copy-paste bug. Verify that branches that SHOULD differ actually differ.
7. **Exception swallowed** — `except: pass` or bare except with no re-raise hides real errors.
8. **Mutable default argument** — `def f(x=[])` causes shared state across calls.

---

### PASS 2: Docstring-Implementation Verification

Re-read each function's docstring, then verify EVERY claim against the code:

9. **Numeric claims**: "up to N times" or "N retries" → count actual loop iterations (range(N) gives N iterations, not N+1).
10. **Inclusion claims**: "including X" or "all required fields" → verify X is explicitly checked/included.
11. **Mutation claims**: "without mutating" or "returns a new" → verify a fresh object is created, not the input reused.
12. **HTTP semantics**: status codes, especially 304 Not Modified → verify success vs error classification matches HTTP standard (304 is a success status).
13. **Domain-specific semantics**: prefixes like "P for percentage" → verify the code implements the claimed semantic (multiply, not subtract).
14. **Rate/velocity formulas**: "tokens per second" → verify division vs multiplication matches physical units (tokens/seconds means divide).

---

### PASS 3: Integration and Boundary Audit

**Complete ALL steps 15-24 for EACH function/method before moving to the next code unit.**

Trace state and probe edge cases:

15. **Field integration and cross-reference completeness**: for each class, list every `self._X` initialized in `__init__`. Then verify: (a) each field is used in at least one method — unused fields signal forgotten integration; (b) any method returning a stats/info dict includes ALL `self._X` fields — missing fields are bugs; (c) state transitions (e.g., back to CLOSED) reset ALL counters — compare with the class's `reset()` method; any counter zeroed in reset() but not in the transition is a bug; (d) after removing items from a nested collection (e.g., set inside a dict), the now-empty inner collection is deleted from its parent; (e) while loops that follow linked chains (parent, next pointers) have a `visited` set or depth limit to prevent infinite loops on cycles; (f) when a docstring says a function handles multiple types (e.g., 'int or float'), verify the code doesn't always coerce to one type.
16. **Argument scope**: when a method accepts a "specific X" argument (e.g., `event`), verify it affects ONLY X, not all items.
17. **Semantic role tracing**: in reconstruction/restore methods, trace which variable is the key vs the value. Swaps are common bugs.
18. **Operator boundary**: for each comparison (`>`, `<`, `>=`, `<=`), mentally test the exact-threshold case. If comparing `x > n`, would `x == n` be incorrectly rejected? If comparing `x < n`, would `x == n` be incorrectly accepted? **Pay special attention to comparisons inside list comprehension filters and compound `and`/`or` conditions** — each `>` or `<` in a compound condition is a separate potential bug; do not let one correct comparison in the same condition distract from another that is wrong. For expiry/eviction/timeout semantics, the exact boundary value typically qualifies (e.g., `now >= expires_at`, not `now > expires_at`).
19. **Missing validation**: for each numeric parameter, ask: "Should negative values be rejected?" Check if validation code exists. Common missing checks: `tokens < 0`, `amount < 0`, `count < 0`. For each collection parameter, ask: "Should empty be rejected?"
20. **Zero and empty handling**: mentally test with zero and empty inputs. Does the code handle these gracefully or crash/misbehave?
21. **Precision loss**: check every `int()` cast or `//` division on computed values. If fractional precision matters (rates, times, probabilities), truncation is a bug.
22. **Return internal state**: methods returning internal dicts/lists should return a copy unless documented as unsafe.
23. **Aliasing without docstring claim**: check for `result = target` or `x = input_param` aliasing on mutable parameters (list, dict, set) even when docstring doesn't mention mutation. Modifying the caller's mutable is a behavioral bug regardless of documentation claims.
24. **Cross-function contract audit**: for each function call in the body, check three things: (a) **Return-value usage**: if the callee returns a value — especially `bool` for success/failure, or a computed result — verify the caller captures and acts on it. A bare call like `self.inventory.fulfill(...)` that discards a boolean return means the caller proceeds regardless of success or failure. (b) **Method selection**: if the same object has similar-named methods (e.g., `.subtotal()` vs `.total()`, `.quantity_on_hand()` vs `.quantity_available()`), read each method's docstring to confirm the correct one is called. The wrong method produces systematically incorrect results. (c) **Side-effect sequencing**: if the callee has side effects (modifies state, deducts points, reserves resources), verify it is called AFTER all prior validation checks pass — otherwise a failed validation after the mutation needs a rollback, and missing rollback is a bug.
25. **Literal business-semantics audit for short helpers and reports**: for every short function/method (roughly 15 lines or fewer), and every method whose name/docstring contains words like `match`, `valid`, `count`, `total`, `subtotal`, `history`, `copy`, `list`, `report`, `active`, `available`, `reserved`, `reorder`, `discount`, `points`, `redeem`, `earn`, `fulfill`, `ship`, or `transfer`, translate the words literally and verify the code matches the promised business meaning. Check common semantic pairs explicitly: `all` vs `any`; line-item count vs total unit count; `>`/`<` vs inclusive minimum/threshold semantics; subtract/deduct/redeem/discount vs accidentally adding; multiply vs divide for rates like "points per dollar"; `active`/`available` subsets vs "all" data/reporting claims; `available` vs `reserved`/`on_hand` fields; "history" or "copy" methods returning internal mutable state instead of a copy; mutating arithmetic written as a bare expression instead of assignment (`x - y` with no `=`); duplicated side-effect calls that fire twice; and comparisons against an object when the contract requires comparing one of its fields (e.g. object vs id). Treat these as separate mechanical checks, not as stylistic interpretation.

## Critical rule: Keep scanning after each bug

When you find a bug in a function/method, **do not stop scanning that function**. Continue examining the remaining lines for additional bugs. A single function can have multiple independent defects.

Common multi-bug patterns:
- A boundary check bug AND a missing validation (e.g., off-by-one AND no negative check)
- A wrong variable AND a missing return in the same control flow
- An integer truncation AND an inverted operator in the same formula

After reporting one bug, ask: "What else could be wrong in this function?"

---

## Final Verification — Blind-Spot Checklist

Before outputting your bug list, quickly verify you did NOT miss these patterns that reviewers commonly overlook even when they are covered by the passes above. Re-read the relevant code if needed.

1. **Unused instance fields**: For each `self.X` assigned in `__init__`, check whether the method that SHOULD use it actually references it. A field declared but never read in the function that builds/merges/assembles the thing it is semantically for is a bug.
2. **HTTP and protocol semantics**: HTTP 2xx AND 3xx are success classes. 304 Not Modified is a success status. Percentage-based discounts multiply. Wait-time formulas divide deficit by rate.
3. **Mutable parameter aliasing**: `result = target` (not `result = target.copy()` or `result = list(target)`) on a mutable arg silently corrupts the caller. This looks like normal assignment — check every method that takes a list or dict parameter.
4. **Silent exception swallowing**: `except SomeException: pass` hides real errors. If the docstring does not say "ignore errors" or "best-effort", the exception should propagate or at least be logged.
5. **Integer truncation on computed values**: `int(elapsed * rate)` drops fractional tokens/time. If the value represents a continuous quantity, truncation is wrong.
6. **Missing negative-value guard**: For every public method with a numeric parameter, ask: "What happens if this is negative?" If the answer is "bad behavior" and there is no `if x < 0: raise`, that is a bug.
7. **Missing empty-collection guard on max()/min()**: Find every `max()` and `min()` call whose argument is a dynamically-built collection (dict, list, set — anything populated in a loop). If there is no `if collection:` guard, no `default=` keyword argument, and no try/except ValueError, the call raises an exception when the collection is empty — that is a bug.
8. **Per-function coverage audit**: List every function and method in the source code. For each, note whether you found a bug in it. Any function with ZERO findings — especially functions that call methods on objects from other modules — was likely under-analyzed. For each zero-finding function, re-examine it applying step 24 (cross-function contract audit): (a) verify each external method call uses the correct method — similar-named methods like `.subtotal()` vs `.total()`, `.quantity_on_hand()` vs `.quantity_available()` have different semantics and the wrong one is a bug; (b) verify side-effect operations (point redemption, inventory reservation, state transitions) occur in the correct sequence — a mutation that fires before a validation check that can fail needs a rollback, and missing rollback is a bug; (c) verify state-dependent operations have the required precondition guards — e.g., releasing reserved inventory should only happen for orders whose inventory was actually reserved.

After drafting your bug list, run a PATTERN COVERAGE CHECK:
For EACH of the eight patterns above, check whether your findings include at least one bug of that type.
For every pattern with ZERO matching findings, re-examine the code specifically hunting for that pattern.
Focus your re-examination on zero-finding functions first, then short functions (under 10 lines) and private helper methods (starting with _)
— auditors routinely skip these because they look "too simple to contain bugs."

---

## Output format

**Do NOT edit any files. Do NOT run code. Only read and analyze.**

Report your findings as a JSON array inside a ```json code block:

```json
[
    {
        "file": "relative/path/to/file.py",
        "line": 42,
        "description": "One sentence: what is wrong and what it should be instead",
        "severity": "high"
    }
]
```

Rules:
- Report every high-confidence bug you find. Do not stop after the first one.
- The `line` must be the exact line number as shown in the numbered source — the line where the defect lives.
- If you genuinely cannot find any high-confidence bug, return `[]`.
- Do NOT report style, naming, or speculative issues.
- Do NOT pad with guesses — only report bugs you are confident about.
- Severity is almost always `"high"` for behavioral failures.
- **MANDATORY LAST STEP:** Your final message MUST be the complete JSON array in a ```json code block — even if you previously edited files, ran tests, or used other tools. Without this final JSON array your findings will not be recorded.
'''

PLAYER_PROMPT_ANCHOR = '''You are a SEARCH ENGINE executing a targeted cross-function audit.
The code below was already reviewed by a first pass that found VISIBLE bugs
(wrong operators, missing returns, discarded values, wrong formulas, wrong
fields). Your job is to find the SUBTLER bugs that first pass MISSED — bugs
that require understanding what OTHER code does, not just reading THIS code.

Do NOT re-report obvious bugs. Focus EXCLUSIVELY on these 6 checks.

## EXECUTION RULE — EXHAUSTIVE ENUMERATION (applies to ALL checks)

Before executing any check, you MUST create an EXHAUSTIVE list of ALL
candidates across ALL source files. Do NOT stop after finding one bug per
check type — process EVERY candidate on your list.

• Check 1 candidates: EVERY call to obj.method() where obj comes from
  another module (self._pricing.*, self._inventory.*, order.*, etc.)
• Check 3 candidates: EVERY function that calls reserve_*, release_*,
  fulfill, ship_*, cancel_*, remove_*, delete_*, or any resource/inventory
  state-changing operation
• Check 5 candidates: EVERY max() and min() call in ALL source files
• Check 6 candidates: EVERY short helper (about 15 lines or fewer) and
  EVERY lifecycle method named create_*, cancel_*, ship_*, deliver_*,
  fulfill, redeem_*, earn_*, *_price, *_total, *_count, or *_points_*

List them ALL first, then verify EACH one individually. A candidate you
skip is a bug you miss.

---

CHECK 1 — METHOD SELECTION (wrong method from similar-named alternatives)
STEP 1: Scan ALL functions in ALL files. List EVERY obj.method() call where
  obj comes from another module.
STEP 2: For EACH call in your list, check:
  (a) Does the object's class have similar-named methods? Pairs to check:
      subtotal() vs total(), quantity_on_hand() vs quantity_available(),
      list_active_X() vs list_all_X(), get_X() vs list_X().
  (b) Does the PURPOSE of the call match the CHOSEN method?
      - If the variable name says "revenue", "paid", or "earned"
        but the method is subtotal() (excludes discounts/shipping) → wrong.
      - If the field name says "available" but the method is
        quantity_on_hand() (includes reserved stock) → wrong.
      - If the docstring says "all" but the method name contains "active",
        "available", "pending" → wrong.

CHECK 2 — SIDE-EFFECT SEQUENCING (mutation before validation without rollback)
For each function that calls methods with side effects (mutations, resource
allocation, point deductions, state changes):
(a) List ALL side-effect calls in execution order.
(b) List ALL validation checks that can fail (early returns, exceptions).
(c) If ANY side effect fires BEFORE a validation that can fail, and there
    is NO rollback code for that side effect → the mutation is permanent
    even on failure. Report it.

CHECK 3 — RESOURCE CONTRACTS (ignored failure OR release without matching acquire)
STEP 1: Scan ALL functions in ALL files. List EVERY function that performs
  resource/inventory state changes: reserve, fulfill, release, cancel,
  cleanup, removal, or ship/transition operations that depend on them.
STEP 2: For EACH function in your list:
  (a) List EVERY resource/inventory mutation call it makes and whether that
      callee returns a success/failure value (especially bool).
  (b) For EACH bool-returning mutation call, verify the caller captures the
      return value and STOPS or rolls back on failure BEFORE advancing state.
      If the code performs a bare call like fulfill()/reserve()/release_*()
      and then still marks the workflow successful, report it.
  (c) For EACH release/cleanup call, name the ACQUIRE operation paired with
      the release. (e.g., release_reservation ↔ reserve(), free ↔ malloc(),
      unlock ↔ lock(), unsubscribe ↔ subscribe)
  (d) Search ALL source files for EVERY call site of the acquire operation.
      For each call site, note which function calls it and under what
      conditions (which state, which code path).
  (e) List ALL states or conditions that the caller's entry guard allows
      through before it performs the release or transition.
  (f) Compare the contracts. Report a bug if EITHER:
      - the caller can advance status / return success even when a
        bool-returning mutation could fail, OR
      - the RELEASE function covers ANY case where the ACQUIRE operation
        never runs.
  IMPORTANT: Do NOT assume a resource mutation succeeded just because it was
  called, and do NOT trust the caller's entry guard or docstring to prove
  that the acquire happened. Only traced call sites and checked return values
  prove the contract.

CHECK 4 — DATA SOURCE COMPLETENESS (filtered subset instead of full set)
For each function that iterates over a collection returned by another method:
(a) Does the source method name suggest filtering? Method names containing
    "active", "available", "pending", "open", "valid", "current" imply
    a SUBSET of the full data.
(b) Does the calling function's docstring say "all" or imply completeness?
(c) If the source returns a FILTERED subset but the purpose requires ALL
    items → the iteration is incomplete. Report it.

CHECK 5 — MAX/MIN ON DYNAMIC COLLECTIONS (crash when empty)
STEP 1: Find EVERY max() and min() call in ALL source files.
STEP 2: For EACH call, check if the argument is a dict or list built in a
  loop or comprehension earlier in the same function. If the collection
  could be empty (loop body might never execute):
  (a) Is there an if-not-empty guard before the call?
  (b) Is there a default= keyword argument on max/min?
  (c) Is there a try/except ValueError?
  If NONE of these exist → max/min raises ValueError when empty. Report it.

CHECK 6 — LITERAL BUSINESS SEMANTICS (short helpers and lifecycle hooks)
STEP 1: List EVERY candidate from the Check 6 candidate rule above.
STEP 2: For EACH candidate, translate the method name/docstring literally
  and verify the implementation matches the promised business meaning.
  Mandatory sub-checks:
  (a) RATE / UNIT FORMULAS:
      - "points per dollar" means multiply dollars by the rate, not divide.
      - "discount" or "redeem" means subtract from what the user pays,
        never add.
      - "paid", "earned", "revenue", or "actual" means use the final paid
        total when a nearby object offers both subtotal() and total().
  (b) MONEY PRECISION:
      - Price helpers must preserve cents unless the docstring explicitly
        says to round to whole dollars. round(x, 0), int(x), or // on money
        values are bugs unless whole-dollar behavior is documented.
  (c) STATE-DEPENDENT LIFECYCLE EFFECTS:
      - cancel/release operations may only release inventory or resources in
        states that actually reserved or acquired them.
      - deliver/earn hooks must use the amount actually paid, not a pre-
        discount or pre-shipping base value, unless the docstring says so.
  If the code violates the literal business meaning, report it even if the
  operator itself looks superficially plausible.

---

Execute ALL 6 checks on the source code. Output a JSON array of all confirmed bugs:
[{"file": "path.py", "line": 42, "description": "what is wrong", "severity": "high"}]
If no check finds a bug, output [].
'''

PLAYER_PROMPT_RED_TEAM = '''You are a security researcher trying to EXPLOIT this Python code. Your goal: find inputs that cause incorrect behavior, data corruption, or bypassed validation.

CRITICAL: You must verify EVERY function. Skipping a function is not allowed.

STEP 1: LIST ALL FUNCTIONS (class.method or standalone).
STEP 2: For EACH function, probe ALL 6 attack vectors below.
STEP 3: For EACH vector, write the verification result with explicit ✓ or ✗.

ATTACK VECTORS:
1. INPUT TAMPERING: Feed negative numbers, zero, None, empty collections. If the function doesn't reject invalid inputs, that's exploitable.
2. STATE CORRUPTION: Pass mutable objects (list, dict). If the function modifies the caller's object in-place without copying, that's data corruption.
3. BOUNDARY BYPASS: For every comparison (>, <), test the exact-threshold value x == n. If x == n should be accepted but > rejects it, that's a bypass.
4. SCOPE ESCALATION: When a function takes a "specific" parameter (event name, key, id), verify it affects ONLY that item — not all items.
5. ROLE CONFUSION: In dict loops (for k, v in d.items()), verify k is key and v is value. In domain code (HTTP status codes, rate formulas, percentage calculations), verify the operation matches the standard semantics.
6. ORPHANED FIELDS: List every self.X in __init__. For each, find where it's used. A declared field never referenced in a method that should use it is a vulnerability.

OUTPUT FORMAT (all three sections required):

## FUNCTIONS
[List each function/method by name, one per line]

## VERIFICATION PER FUNCTION
For EACH function from step 1, output:
FUNCTION: [name]
- V1 Input tampering: [list numeric/collection params, verify each: ✓ rejects invalid OR ✗ BUG: missing validation for X]
- V2 State corruption: [list mutable params, verify each: ✓ copies input OR ✗ BUG: aliasing mutates caller]
- V3 Boundary bypass: [list comparisons, verify each: ✓ exact-threshold correct OR ✗ BUG: operator wrong]
- V4 Scope escalation: [list specific-item params, verify each: ✓ affects only that item OR ✗ BUG: affects all items]
- V5 Role confusion: [list dict loops and domain logic, verify each: ✓ correct semantics OR ✗ BUG: wrong role/formula]
- V6 Orphaned fields: [list self.X from __init__ relevant here, verify each: ✓ used where expected OR ✗ BUG: declared but unused]
END_FUNCTION

## BUGS
```json
[{"file": "path.py", "line": 42, "description": "exploit: calling func(X=-1) causes [specific wrong behavior]", "severity": "high"}]
```

MANDATORY: You MUST output VERIFICATION PER FUNCTION for EVERY function listed. If you skip a function, your output is invalid. A function can have MULTIPLE vulnerabilities — find ALL of them.
'''

PLAYER_PROMPT_BOUNDARY = '''You are an edge-case tester. Your job is EXHAUSTIVE verification.

CRITICAL: You must verify EVERY function. Skipping a function is not allowed.

STEP 1: LIST ALL FUNCTIONS (class.method or standalone).
STEP 2: For EACH function, enumerate ALL pattern instances WITHIN THAT FUNCTION.
STEP 3: For EACH enumerated item, write the verification result.

OUTPUT FORMAT (all three sections required):

## FUNCTIONS
[List each function/method by name, one per line]

## VERIFICATION PER FUNCTION
For EACH function from step 1, output:
FUNCTION: [name]
- Comparisons (> < >= <=): [list lines with comparison operators, verify each: ✓ exact-threshold handled OR ✗ BUG: > should be >= or vice versa]
- Numeric params: [list each numeric parameter, verify each: ✓ has negative check OR ✗ BUG: missing negative validation]
- Truncations (int() //): [list lines with int() or //, verify each: ✓ fractional precision not needed OR ✗ BUG: truncation loses precision]
- Collections: [list list/dict/set params, verify each: ✓ empty handled OR ✗ BUG: crashes on empty]
END_FUNCTION

KEY VERIFICATION RULES:
- For each comparison `x > n`: test x == n. If x == n should be ACCEPTED, use >=. If x == n is REJECTED by >, that's a BUG.
- For each comparison `x < n`: test x == n. If x == n should be REJECTED, use <=. If x == n is ACCEPTED by <, that's a BUG.
- For each numeric param: ASK "should negative be rejected?" If yes and no check exists → BUG.
- For each int() on rates/times/tokens: fractional precision matters → truncation is a BUG.

## BUGS
```json
[{"file": "path.py", "line": 42, "description": "missing validation for negative X", "severity": "high"}]
```

MANDATORY: You MUST output VERIFICATION PER FUNCTION for EVERY function. If you skip a function, your output is invalid. A function can have MULTIPLE boundary bugs — find ALL of them.
'''

PLAYER_PROMPT_COMPLETENESS = '''You find MISSING code — things that should exist but don't. Other auditors find wrong code; you find absent code.

For EVERY class and EVERY method, check these 3 patterns:

1. UNUSED FIELDS: List every self.X assigned in __init__. For each field, ask: "Which methods SHOULD use this field?" If a field is never referenced in a method that semantically needs it, that's a BUG. Example: _session_headers is a dict field → build_headers() should merge it. If it doesn't, that's a bug.

2. ALIASING WITHOUT COPY: List every method with a mutable parameter (list, dict, set). If code does `x = param` instead of `x = list(param)` or `x = param.copy()`, and then modifies x, it silently corrupts the caller's data. That's a BUG.

3. MISSING VALIDATION: For every method with a numeric parameter (int, float), ask: "Should negative values be rejected?" If the semantics suggest negatives are invalid (tokens, counts, amounts) but there's no `if x < 0: raise ValueError`, that's a BUG.

Output findings, then bugs:

## FINDINGS
For each class, list checks:
- CLASS.__init__: Fields: [list all self.X assignments]
- CLASS.method: Uses field [X]: ✓ found at line N OR ✗ BUG: field X not used
- CLASS.method: Param [name] is mutable: ✓ copied OR ✗ BUG: aliased without copy
- CLASS.method: Param [name] is numeric: ✓ has negative check OR ✗ BUG: missing validation

## BUGS
```json
[{"file": "path.py", "line": 42, "description": "missing X", "severity": "high"}]
```

MANDATORY: Check ALL classes and ALL methods. A class with 5 fields needs 5 field-usage checks.
'''

CONTRACT_AWARENESS_PREFIX = (
    "You are also given contracts for imported modules that describe their interfaces. "
    "Use these contracts to verify cross-module interactions within this file.\n\n"
)

INTENSITY_PROMPTS = {
    "low": [PLAYER_PROMPT_MAIN],
    "medium": [PLAYER_PROMPT_MAIN, PLAYER_PROMPT_ANCHOR],
    "high": [
        PLAYER_PROMPT_MAIN,
        PLAYER_PROMPT_ANCHOR,
        PLAYER_PROMPT_RED_TEAM,
        PLAYER_PROMPT_BOUNDARY,
        PLAYER_PROMPT_COMPLETENESS,
    ],
}

TESTER_PROMPT = '''You are a test engineer. For each bug in the list below, write a pytest test that confirms the bug exists.

## Rules for each test

1. READ the source file containing the bug.
2. Write a pytest function that:
   - Imports the ACTUAL function/class (no mocking of the code under test)
   - Calls it with inputs that TRIGGER the specific bug
   - Asserts the EXPECTED (correct) behavior — the assertion should FAIL on the current buggy code and PASS after the bug is fixed
3. SELF-CHECK before saving: ask yourself
   - Does this test import the real function? (not a mock)
   - Does it test the specific wrong behavior described?
   - Would this test PASS if the bug were fixed?
   - Does it avoid mocking internals?
4. RUN the test with `pytest path/to/test_file.py -x -q` to confirm it fails (proving the bug exists).
5. If a bug cannot be confirmed (the code looks correct, or the test passes immediately), mark it as `false_positive` or `invalid_test`.

## Output

After writing and running all tests, output a JSON array:

```json
[
    {"bug_id": 1, "status": "confirmed", "test_file": "tests/test_debugger_bugs_iter1.py"},
    {"bug_id": 2, "status": "false_positive", "test_file": null},
    {"bug_id": 3, "status": "invalid_test", "test_file": null}
]
```

Status values:
- `confirmed` — test written, test fails (bug is real), test file path provided
- `false_positive` — bug description is wrong, code is actually correct
- `invalid_test` — could not write a reliable test (e.g. external dependency, non-deterministic)
'''

CONTRACT_EXTRACTION_PROMPT = '''You are a code-contract extraction assistant. Analyse the provided source file(s) and produce a structured JSON contract describing every **public** export.

## Rules

1. **Public-only** — Skip names that start with a single underscore (`_`). Only document exports that are part of the module's public API (functions, classes, constants without a leading `_`).
2. **Maximum 100 words per function contract** — keep each export entry concise.
3. Respond ONLY with valid JSON — no commentary before or after the JSON block.

## Single-file format

When analysing a single file, return a JSON object with these top-level keys:

```json
{
  "rel_path": "src/example.py",
  "exports": [
    {
      "name": "process_data",
      "signature": "process_data(data: list[dict], key: str) -> dict",
      "preconditions": ["data must be non-empty", "key must exist in each dict"],
      "postconditions": ["returned dict is keyed by the value of key"],
      "side_effects": ["logs start and end of processing"],
      "raises": ["ValueError if data is empty", "KeyError if key missing"],
      "return_type": "dict"
    }
  ],
  "imports_usage": [
    {
      "source_module": "logging",
      "symbol": "getLogger",
      "usage_description": "creates module-level logger for debug/info messages"
    }
  ],
  "invariants": [
    "module-level _registry dict is never mutated after initial population"
  ]
}
```

### Field descriptions

| Key              | Description                                                          |
|------------------|----------------------------------------------------------------------|
| `exports`        | List of objects, one per public symbol (function, class, constant).  |
| `name`           | The export's identifier.                                             |
| `signature`      | Full callable signature string (or `""` for non-callables).          |
| `preconditions`  | Conditions that must hold **before** the function runs.              |
| `postconditions` | Conditions that hold **after** the function returns successfully.    |
| `side_effects`   | Observable effects beyond the return value (I/O, mutation, logging).|
| `raises`         | Exception types and trigger conditions.                              |
| `return_type`    | Declared or inferred return type annotation string.                  |
| `imports_usage`  | One entry per imported symbol describing how it is used.             |
| `invariants`     | Module-level or class-level properties that always hold.             |

## Batch format (multiple files)

When multiple files are provided, return a JSON object **keyed by relative path**. Each value follows the single-file schema above:

```json
{
  "src/foo.py": {
    "rel_path": "src/foo.py",
    "exports": [ ... ],
    "imports_usage": [ ... ],
    "invariants": [ ... ]
  },
  "src/bar.py": {
    "rel_path": "src/bar.py",
    "exports": [ ... ],
    "imports_usage": [ ... ],
    "invariants": [ ... ]
  }
}
```

Again: skip any symbol whose name starts with `_`. Output ONLY the JSON object.
'''

EDGE_ANALYSIS_PROMPT = '''You are a cross-file bug detector. You are given:
1. The FULL source code of a caller file
2. CONTRACTS of all modules it imports (not their full source)

Your job: find bugs at the BOUNDARY between the caller and its dependencies.

## 6 Check Types

For EACH imported function/class used in the caller, verify:

### 1. signature_mismatch — Wrong number or types of arguments

Does the caller pass the correct number and types of arguments?
Look for: extra args, missing args, wrong arg types, wrong keyword names.

Example: The contract says `process(data: list, key: str)` but the caller
writes `process(data, key, extra_arg)` or `process(data=items)` where
`items` is a dict, not a list.

### 2. return_ignored — Return value discarded

Does the callee return a value that the caller should use but doesn't?
Look for: bool returns (success/failure) used as bare calls, computed
results discarded.

Example: The contract says `reserve() -> bool` (True on success) but the
caller writes `self.inventory.reserve(item)` without checking the result
and then proceeds as if the reservation succeeded.

### 3. none_not_handled — Optional return not checked for None

Can the callee return None? Does the caller check for it?
Look for: functions that return Optional[X] but caller does .attr or
[index] on result without a None guard.

Example: The contract says `get_user(id: int) -> Optional[User]` but the
caller writes `user = get_user(uid); user.name` without checking
`if user is None`.

### 4. exception_uncaught — Declared exception not caught

Does the callee raise exceptions the caller doesn't catch?
Look for: documented raises in the contract vs try/except in the caller.

Example: The contract says `raises: ValueError if amount < 0` but the
caller passes a user-provided amount with no try/except around the call.

### 5. side_effect_order — State used before it is set

Does the caller assume state that the callee hasn't set yet?
Look for: calling methods in wrong order, using results before side
effects complete.

Example: The contract says `connect() must be called before query()` but
the caller calls `query()` without first calling `connect()`. Or the
caller reads a field that is only populated by a method it hasn't called.

### 6. type_mismatch — Wrong type passed or expected

Does the caller pass or expect the wrong type?
Look for: passing dict where list expected, str where Path expected,
int where float needed.

Example: The contract says `write(path: Path, data: bytes)` but the
caller passes a plain string `write("file.txt", data)` or passes
`data` as a str instead of bytes.

## Output

JSON array of findings:

```json
[
    {
        "caller_file": "src/debugger.py",
        "callee_file": "src/config.py",
        "caller_line": 42,
        "description": "resolve_config() called with 2 args but signature takes 1",
        "confidence": "high",
        "check_type": "signature_mismatch"
    }
]
```

Confidence levels:
- **high** — certain bug, clear contract violation
- **medium** — likely bug, but depends on runtime conditions
- **low** — possible issue, speculative

If no cross-file bugs found, return `[]`.
Do NOT report bugs within the caller that don't involve its dependencies.
Do NOT report intra-file bugs.
Do NOT report style issues or suggestions.
'''

SCC_ANALYSIS_PROMPT = '''You are a circular-dependency bug detector. You are given:
1. The FULL source code of every file in a strongly connected component (SCC) —
   a set of modules that import each other, directly or indirectly, forming a cycle.
2. CONTRACTS for any external modules they import.

Your job: find bugs caused or masked by the circular dependency itself.

## 4 Check Types

For EACH pair of mutually-dependent modules in the SCC, verify:

### 1. init_ordering — Import-time AttributeError from incomplete initialisation

When module A imports module B and B imports A, Python finishes executing one
module before the other.  If the *later* module tries to access a name from the
*earlier* module that has not yet been defined (class, function, constant), the
result is an AttributeError at import time — or, if the access is inside a
function body, an AttributeError at call time.

Look for:
- References to names (classes, functions, constants) in one SCC module that
  depend on code executed later in another SCC module during the import cycle.
- Function/method bodies that reference a module-level name from a cyclic peer
  that may not have been populated yet at the time the function is *called*
  (even if the import itself succeeds).
- Conditional imports or `TYPE_CHECKING` guards that hide a runtime dependency
  the code actually needs.

### 2. stale_state — Mutual state mutation producing stale or inconsistent state

When two modules in a cycle share mutable module-level state (globals, caches,
registries, singleton instances), mutations made by one module can be invisible
or partially visible to the other due to import-time execution order or
rebinding.

Look for:
- Module-level mutable containers (dicts, lists, sets) populated during import
  in one SCC module but read during import in another — the reader may see an
  empty or partially-filled container.
- Singleton or cache instances created in module A and also referenced from
  module B where B's import-time code mutates the instance before A has finished
  initialising it.
- Functions in different SCC modules that read and write the same global state
  without synchronisation, leading to lost updates when called in a specific
  order.

### 3. contract_violation — Interface contract broken across every direction of the cycle

In a cycle A → B → C → A, a contract break between any pair propagates around
the entire loop.  Check the contract at **every edge** in the cycle, not just
one direction.

Look for:
- A function in module A calls a function in module B passing wrong argument
  types or counts (signature_mismatch across the cycle).
- A callee in the cycle returns Optional[X] but the caller dereferences the
  result without a None guard (none_not_handled across the cycle).
- A callee in the cycle raises an exception that the caller does not catch
  (exception_uncaught across the cycle).
- Return values that signal success/failure (bool) are silently discarded
  (return_ignored across the cycle).

### 4. reimport_side_effect — Re-import or late-binding side effects

Python's import system caches modules in `sys.modules`, so a second import of
the same module returns the existing object.  However, `importlib.reload()` or
manual manipulation of `sys.modules` can re-trigger module-level code.

Look for:
- Module-level code that has **idempotency bugs**: running it twice (e.g., after
  a reload) would duplicate registry entries, double-count metrics, or append
  to a list that was already populated.
- `from X import *` or `__all__` manipulation inside the SCC that may shadow or
  overwrite names unexpectedly when the import graph is re-traversed.
- Decorator or metaclass registration side effects that fire at import time and
  assume they only execute once.

## Output

JSON array of findings — same schema as EDGE_ANALYSIS_PROMPT:

```json
[
    {
        "caller_file": "src/module_a.py",
        "callee_file": "src/module_b.py",
        "caller_line": 42,
        "description": "module_a accesses Foo from module_b during import, but module_b has not yet defined Foo because it is still importing module_a",
        "confidence": "high",
        "check_type": "init_ordering"
    }
]
```

Confidence levels:
- **high** — certain bug, reproducible import-time or runtime failure
- **medium** — likely bug, depends on execution order
- **low** — possible issue, speculative

check_type must be one of: `init_ordering`, `stale_state`, `contract_violation`, `reimport_side_effect`.

If no circular-dependency bugs are found, return `[]`.
Do NOT report bugs that are unrelated to the circular dependency.
Do NOT report intra-file bugs (single-file issues).
Do NOT report style issues or suggestions.
'''

DEEP_DIVE_PROMPT = '''You are verifying a specific cross-file bug finding. You are given:
1. The FULL source code of the **caller** file: {caller_file}
2. The FULL source code of the **callee** file: {callee_file}

A previous analysis flagged a potential bug at line {caller_line} of the caller:

- **Check type**: {check_type}
- **Description**: {description}

## Your task

Read BOTH source files carefully.  Trace the specific call site at line
{caller_line} in the caller.  Walk into the callee's implementation if
needed.  Decide whether the flagged issue is a **real bug** or a **false
positive**.

Consider:
- Does the call site actually exist at that line?
- Are the argument types and counts correct for the callee's real signature?
- If the issue is a missing None guard, can the callee actually return None
  in this call's context?
- If the issue is an uncaught exception, can the exception actually be raised
  given the arguments passed?
- If the issue is a return_ignored, does the caller's control flow actually
  depend on the result elsewhere?

## Output

Respond with a single JSON object (no surrounding text):

```json
{{"confirmed": true, "reason": "The caller at line {caller_line} passes a str to process() which expects list — this will raise TypeError at runtime."}}
```

or

```json
{{"confirmed": false, "reason": "The callee's signature accepts Any, and the caller's argument is validated two lines above, so this is not a bug."}}
```

Fields:
- **confirmed** (bool): `true` if the bug is real, `false` if it is a false positive.
- **reason** (str): One or two sentences explaining your conclusion, referencing
  specific line numbers and code from the provided source files.
'''

FIXER_PROMPT = '''You are a senior engineer fixing confirmed bugs.

## For each confirmed bug

1. READ the failing test to understand exactly what behavior is expected.
2. READ the buggy source file.
3. PLAN the minimal fix — change only the lines needed, no refactoring.
4. IMPLEMENT the fix.
5. RUN the test: `pytest path/to/test_file.py -x -q` — it must PASS.

## Rules

- Fix ONLY confirmed bugs (those with status "confirmed" in the tester output).
- Make MINIMAL changes — do not refactor, rename, or clean up anything beyond the fix.
- After all individual fixes are done, run the full test suite: `pytest tests/ -x -q --tb=short`
- If your fix breaks other tests, adjust the FIX — do not modify the tests.
- If a fix cannot be made without breaking other tests, note the conflict and skip that bug.

## Commit

After all fixes pass, run: `git add -A && git commit -m "fix: <short description of bugs fixed>"`
'''
