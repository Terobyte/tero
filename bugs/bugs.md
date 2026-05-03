# Cross-Function Contract Audit — Bugs Found

| # | File | Status | Resolution |
|---|------|--------|------------|
| 1 | `src/coach_player.py:869` | fixed (pre-existing) | Return value already captured (`self._last_turn_result = fix_result`); `TimeoutError` already handled at line 876 |
| 2 | `tests/test_batch_executor.py:1161,1312` | fixed (pre-existing) | Both tests already import `BATCH_REVIEW_MAX_TURNS` from `src.constants` |
| 3 | `src/turn_runner.py:37,55` | **fixed** | `provider` parameter now used as fallback: `provider_override or provider or router.provider_for(role)` |
| 4 | `src/batch_executor.py:395` | **fixed** | Added public `get_provider_by_name()` to `RoleRouter`; batch_executor uses it instead of private `_get_or_create_provider` |
