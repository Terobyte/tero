"""Judge step: snapshot, the judge call with retries, applying the verdict."""

from __future__ import annotations

from typing import Any, Callable

from src.loop import snapshot as snapshot_mod
from src.loop.compact_step import CompactStep
from src.loop.phase_ctx import (
    _JUDGE_MAX_FAILURES,
    _NEEDS_USER_MAX_ATTEMPTS,
    PhaseContext,
    PhaseOutcome,
    _Next,
)
from src.loop.judge_prompt import (
    _judge_failed,
    build_prompt,
    validate_ctx,
)
from src.loop import verdict_step as _verdict
from src.loop.prompts import needs_user_reask_block
from src.loop.schemas import ValidateCtx
from src.loop.pending import stop_judge_pending  # noqa: F401
from src.loop.situation_step import record_situation  # noqa: F401 — точка подмены тестов


class JudgeStep:
    def __init__(
        self,
        ctx: PhaseContext,
        compact: CompactStep,
        rollback_with_snapshot: Callable[[str], str | None],
        guard_plan: Callable[[str], None],
        no_progress: Callable[[], bool],
    ) -> None:
        self.ctx = ctx
        self.compact = compact
        self.rollback_with_snapshot = rollback_with_snapshot
        self.guard_plan = guard_plan
        self.no_progress = no_progress

    def _snapshot_before_judge(self) -> str | None:
        run_id = self.ctx.ledger.run_dir.name
        ref = snapshot_mod.ref_name(run_id, self.ctx.phase.index, self.ctx.st.round)
        try:
            snapshot_mod.take_snapshot(self.ctx.repo, self.ctx.checkpoint, ref)
            if not snapshot_mod.verify(self.ctx.repo, ref):
                raise RuntimeError("snapshot verify failed")
        except Exception as exc:
            self.ctx.update_state(snapshot_ref="")
            self.ctx.emit(
                "snapshot_failed",
                ref=ref,
                base=self.ctx.checkpoint,
                role="judge",
                error=str(exc) or type(exc).__name__,
                text=f"снимок работы не сделан (`{ref}`)",
            )
            self.ctx.ledger.event(
                "snapshot_failed",
                ref=ref,
                base=self.ctx.checkpoint,
                error=str(exc) or type(exc).__name__,
                round=self.ctx.st.round,
            )
            return None
        return ref

    async def _judge(self, ref: str | None = None) -> _Next:
        # ref — повторный вход судьи без снимка (F6); нет снимка — судить нельзя.
        if ref is None:
            ref = self._snapshot_before_judge()
            if ref is None:
                return _Next.NEXT
        else:
            try:  # ref невосстановим: fail closed, без нового снимка и без игрока (F7).
                if not snapshot_mod.verify(self.ctx.repo, ref):
                    raise RuntimeError(f"snapshot not found: {ref}")
                snapshot_mod.restore_snapshot(self.ctx.repo, ref)
            except Exception:
                self.ctx.st.pending_reason = "judge_failed"
                return _Next.JUDGE_PENDING
        self.compact._clear_compact_requested("judge")
        return await self._judge_on_ref(ref)

    async def _judge_on_ref(self, ref: str) -> _Next:
        from datetime import datetime, timezone

        self.ctx.update_state(
            role="judge",
            provider=self.ctx.cfg.judge_provider,
            model=self.ctx.cfg.judge_model,
            step_started=datetime.now(timezone.utc).isoformat(),
            snapshot_ref=ref,
            base=self.ctx.checkpoint,
        )
        if snapshot_mod.tree_of(self.ctx.repo, self.ctx.checkpoint) != snapshot_mod.ref_tree(
            self.ctx.repo, ref
        ):
            snapshot_mod.restore_snapshot(self.ctx.repo, ref)
        ctx = validate_ctx(self.ctx.st, self.ctx.ledger, self.ctx.phase.title)
        reask_question = ""
        shippable = ("SHIP", "NO_SHIP")
        while True:
            data = await self._call_judge(ref, ctx, reask_question)
            if data is not None and data.get("__compacted__"):
                data = self._judge_compacted(data, ref)
                if data is None:
                    continue
            bad = (data is None or data.get("__invalid__") or
                   (data.get("verdict") == "NEEDS_USER" and not self.ctx.cfg.escalation_provider))
            if not bad:
                if data.get("verdict") in shippable:
                    self.ctx.st.needs_user_attempts = 0
                break
            if data is not None and data.get("verdict") == "NEEDS_USER":
                asked = self._judge_needs_user(data)
                if asked is _Next.JUDGE_PENDING:
                    return asked
                reask_question = asked
                continue
            if self._judge_failure(data):
                return _Next.JUDGE_PENDING
        self.ctx.st.judge_failures = 0
        return await self._accept_verdict(data)

    async def _call_judge(
        self, ref: str, ctx: ValidateCtx, reask_question: str
    ) -> dict[str, Any] | None:
        """One judge call on the round ref; the plan and the tree are restored after it."""
        prompt = build_prompt(self.ctx, reask_question)
        self.compact._clear_compact_requested("judge")
        data = await self.ctx.agents.run_role(
            role="judge",
            provider=self.ctx.cfg.judge_provider,
            model=self.ctx.cfg.judge_model,
            effort=self.ctx.cfg.judge_effort,
            prompt=prompt,
            timeout=self.ctx.cfg.judge_timeout_s,
            ctx=ctx,
            emit=self.ctx.emit,
            compact_check=self.compact._judge_compact_check(),
            compact_at_tokens=(
                self.ctx.cfg.compact_judge_at_tokens
                if self.ctx.cfg.judge_provider == "claude"
                else 0
            ),
        )
        # Провайдер без read_only может писать в дерево: план проверяем
        # сразу после вызова, до сравнения деревьев.
        self.guard_plan("judge")
        if snapshot_mod.tree_of(
            self.ctx.repo, self.ctx.checkpoint
        ) != snapshot_mod.ref_tree(self.ctx.repo, ref):
            snapshot_mod.restore_snapshot(self.ctx.repo, ref)
            self.ctx.ledger.event("judge_tamper", round=self.ctx.st.round, ref=ref)
            print("  judge изменил рабочее дерево — откат", flush=True)

        return self.ctx.check_reply("judge", data, self.ctx.cfg.judge_provider)

    def _judge_compacted(self, data: dict[str, Any], ref: str) -> dict[str, Any] | None:
        """None — repeat the call; otherwise the reply to judge (invalid on a second compact)."""
        # Компакт судьи: повтор на уже проверенном ref раунда, без
        # дополнительного обязательного снимка (F12), не считается
        # сбоем судьи; второй компакт за раунд — как сбой (F7).
        if not self.ctx.st.judge_compact_used:
            tokens = data.get("tokens", 0)
            text = f"компакт judge ({tokens} токенов) — новый вызов, снимок `{ref}`"
            self.ctx.emit("compact", role="judge", tokens=tokens, ref=ref or "", text=text)
            self.ctx.ledger.event("compact", role="judge", tokens=tokens, ref=ref,
                                  round=self.ctx.st.round)
            print(f"  компакт judge ({tokens} токенов) — новый вызов", flush=True)
            self.ctx.st.judge_compact_used = True
            return None
        return {
            "__invalid__": True,
            "errors": ["judge compacted twice in one round"],
            "raw": "",
        }

    def _judge_needs_user(self, data: dict[str, Any]) -> _Next | str:
        """JUDGE_PENDING after the last attempt, else the question to re-ask ("" after the first)."""
        question = str(data.get("user_question") or "")
        self.ctx.st.needs_user_attempts += 1
        attempt = self.ctx.st.needs_user_attempts
        self.ctx.st.user_question = question
        text = needs_user_reask_block(question)
        self.ctx.ledger.event(
            "needs_user",
            attempt=attempt,
            question=question,
            round=self.ctx.st.round,
        )
        self.ctx.emit(
            "needs_user",
            attempt=attempt,
            question=question,
            round=self.ctx.st.round,
            role="judge",
            text=text,
        )
        print(f"  судья: нужен человек — {question}", flush=True)
        if attempt >= _NEEDS_USER_MAX_ATTEMPTS:
            self.ctx.st.pending_reason = "needs_user"
            self.ctx.st.user_question = question
            return _Next.JUDGE_PENDING
        return question if attempt == 1 else ""

    def _judge_failure(self, data: dict[str, Any] | None) -> bool:
        """Count a failed judge call; True — stop as judge_pending."""
        self.ctx.st.judge_failures += 1
        attempt = self.ctx.st.judge_failures
        if data is None:
            kind = self.ctx.st.last_provider_kind
            if kind == "auth":
                self.ctx.st.pending_reason = "judge_auth"
                return True
            if kind == "quota":
                self.ctx.st.pending_reason = "judge_quota"
                return True
            error = self.ctx.st.last_provider_error
            _judge_failed(self.ctx, attempt, error)
        else:
            errors = [str(item) for item in data.get("errors") or []]
            _judge_failed(self.ctx, attempt, "; ".join(errors[:3]))
        if attempt >= _JUDGE_MAX_FAILURES:
            self.ctx.st.pending_reason = "judge_failed"
            return True
        return False

    async def _accept_verdict(self, data: dict[str, Any]) -> _Next:
        return await _verdict.accept_verdict(self, data)

    # Совместимость: старые точки подмены (`JudgeStep._apply_verdict` и т.д.)
    # делегируют в verdict_step.
    _apply_verdict = _verdict.apply_verdict
    _apply_verdict_continuation = _verdict.apply_verdict_continuation
    _new_blocking = _verdict.new_blocking_findings
    _clean_retry = _verdict.clean_retry
    _discard_tests = _verdict.discard_tests

    def _stop_judge_pending(self, reason: str = "judge_failed") -> PhaseOutcome:
        from src.loop.pending import stop_judge_pending

        return stop_judge_pending(self, reason)
