"""Judge step: snapshot, the judge call with retries, applying the verdict."""

from __future__ import annotations

from typing import Any, Callable

from src.loop import snapshot as snapshot_mod
from src.loop.compact_step import CompactStep
from src.loop.findings import findings_key, in_continuation, not_shrunk, stalled
from src.loop.phase_ctx import (
    _JUDGE_MAX_FAILURES,
    _NEEDS_USER_MAX_ATTEMPTS,
    K_MAX,
    OpenFinding,
    PhaseContext,
    PhaseOutcome,
    _dump,
    _Next,
)
from src.loop.judge_prompt import (
    _judge_failed,
    _print_verdict,
    build_prompt,
    validate_ctx,
    verdict_state,
)
from src.loop.prompts import needs_user_reask_block
from src.loop.schemas import ValidateCtx
from src.loop.state import RunState, Status
from src.loop.transition import phase_transition


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
        ctx = validate_ctx(self.ctx.st)
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
        return self._accept_verdict(data)

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

    def _accept_verdict(self, data: dict[str, Any]) -> _Next:
        self.ctx.emit(
            "verdict",
            verdict=data.get("verdict"),
            round=self.ctx.st.round,
            role="judge",
        )
        self.ctx.update_state(
            role="judge",
            provider=self.ctx.cfg.judge_provider,
            model=self.ctx.cfg.judge_model,
            verdict=verdict_state(data),
        )
        self.ctx.ledger.event(
            "judge",
            verdict=data.get("verdict"),
            round=self.ctx.st.round,
        )
        self.ctx.ledger.artifact(self.ctx.round_file("judge.json"), _dump(data))
        _print_verdict(data)
        return self._apply_verdict(data)

    def _stop_judge_pending(self, reason: str = "judge_failed") -> PhaseOutcome:
        state = RunState.read(self.ctx.run_dir / "state.json") or {}
        ref = state.get("snapshot_ref") or ""
        if reason in ("judge_auth", "judge_quota"):
            provider = self.ctx.cfg.judge_provider or "?"
            text = self.ctx.provider_text("judge", provider) + " `tero resume` после входа"
        elif reason == "needs_user":
            question = self.ctx.st.user_question or ""
            text = f"нужен человек: `{question}`; `tero review`, `tero resume`"
        else:
            text = f"код готов, не принят: `{ref}`; `tero review`, `tero resume`"
        try:
            decisions = bool(self.ctx.cfg.decisions)
        except Exception:
            decisions = False
        if decisions:
            from src.loop.decision_step import DecisionStep

            step = DecisionStep(self.ctx, self.rollback_with_snapshot)
            return step.jp_open(reason, text, ref)
        phase_transition(
            self.ctx,
            status=Status.JUDGE_PENDING.value,
            reason=reason,
            text=text,
            ref=ref,
        )
        return PhaseOutcome(
            status="judge_pending",
            reason=reason,
            rounds=self.ctx.st.round,
            escalated=self.ctx.st.escalated,
            stop_text=text,
        )

    def _apply_verdict(self, data: dict[str, Any]) -> _Next:
        verdict = data.get("verdict")
        if verdict == "SHIP":
            return _Next.SHIP
        if verdict == "NEEDS_USER":
            self.ctx.st.user_question = data.get("user_question", "")
            return _Next.USER

        findings = data.get("findings") or []
        self._discard_tests(findings)
        previous = {row["id"]: row["state"] for row in data.get("previous") or []}
        if in_continuation(self.ctx.st):
            return self._apply_verdict_continuation(findings, previous)
        if stalled(self.ctx.st, previous) and self.no_progress():
            return _Next.STOP

        still_open = [
            item
            for item in self.ctx.st.open_findings
            if previous.get(item.id) != "resolved"
        ]
        new_blocking = self._new_blocking(findings)
        self.ctx.st.open_findings = still_open + new_blocking
        if any(item.route == "tester" for item in new_blocking) and self.ctx.tester_enabled:
            return _Next.TESTER
        return _Next.NEXT

    def _apply_verdict_continuation(
        self, findings: list[dict[str, Any]], previous: dict[str, str]
    ) -> _Next:
        # В раунде-продолжении _stalled/_no_progress не проверяются:
        # решает счётчик K_max.
        key = findings_key(findings)
        if not_shrunk(self.ctx.st, key):
            self.ctx.st.kmax_flat += 1
        else:
            self.ctx.st.kmax_flat = 0
        self.ctx.st.kmax_prev = key
        if self.ctx.st.kmax_flat >= K_MAX:
            self._clean_retry()
        still_open = [
            item
            for item in self.ctx.st.open_findings
            if previous.get(item.id) != "resolved"
        ]
        new_blocking = self._new_blocking(findings)
        self.ctx.st.open_findings = still_open + new_blocking
        if any(item.route == "tester" for item in new_blocking) and self.ctx.tester_enabled:
            return _Next.TESTER
        return _Next.NEXT

    def _new_blocking(self, findings: list[dict[str, Any]]) -> list[OpenFinding]:
        new_blocking: list[OpenFinding] = []
        for item in findings:
            if item.get("severity") != "blocking":
                continue
            if item.get("route") == "discard_test":
                continue
            route = item.get("route") or "player"
            if route == "tester" and not self.ctx.tester_enabled:
                route = "player"
            found = OpenFinding(
                id=f"F{self.ctx.st.next_id}",
                severity="blocking",
                route=route,
                file=item.get("file", ""),
                line=item.get("line"),
                problem=item.get("problem", ""),
                expected=item.get("expected", ""),
            )
            self.ctx.st.next_id += 1
            new_blocking.append(found)
        return new_blocking

    def _clean_retry(self) -> None:
        ref = self.rollback_with_snapshot("clean_retry")
        self.ctx.ledger.event(
            "clean_retry",
            reason="clean_retry",
            ref=ref,
            round=self.ctx.st.round,
        )
        # Чистая попытка: блок продолжения убирается, старый ref остаётся.
        self.ctx.st.resume_active = False
        self.ctx.st.had_crash = False
        self.ctx.st.kmax_flat = 0
        self.ctx.st.kmax_prev = None

    def _discard_tests(self, findings: list[dict[str, Any]]) -> None:
        self.ctx.st.discarded = []
        for item in findings:
            if item.get("route") != "discard_test":
                continue
            fid = item.get("id") or ""
            paths = self.ctx.st.protected_by_id.pop(fid, None)
            if not paths:
                continue
            note = "тест снят, чини код"
            for path in paths:
                target = self.ctx.repo / path
                target.unlink(missing_ok=True)
                self.ctx.st.protected.pop(path, None)
                self.ctx.st.discarded.append(f"{fid}: {note}")
            for found in self.ctx.st.open_findings:
                if found.id == fid:
                    found.test_paths = []
                    found.note = note
