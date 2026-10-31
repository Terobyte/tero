"""ResumeStep: judge-first entry on the ref tree (moved from resume/step)."""

from __future__ import annotations


class ResumeStep:
    """Stopped-resume entry: protect+gate on the ref tree, then judge."""

    def __init__(self, ctx, judge, gate, protect, after_judge, stop_check=None) -> None:
        self.ctx = ctx
        self.judge = judge
        self.gate = gate
        self.protect = protect
        self.after_judge = after_judge
        self.stop_check = stop_check

    def _gate_entry(self, resume_from) -> bool:
        if not resume_from.gate_first:
            return True
        ctx, gate_out = self.ctx, None
        ctx.st.resume_ref = resume_from.ref
        ctx.st.resume_base = resume_from.base
        ctx.st.resume_active = True
        self.protect()
        gate = self.gate()
        ctx.progress()
        if gate is not None and not gate.ok:
            gate_out = gate.output
        else:
            gate_out = gate.output if gate else "тесты не обнаружены"
        ctx.st.gate_output = gate_out
        return gate is None or gate.ok

    async def enter(self, resume_from, start_round):
        ctx = self.ctx
        ctx.st.round = start_round
        ctx.st.judge_failures = 0
        if resume_from.answer:
            ctx.st.answer = resume_from.answer
            # телефонный флаг не сбрасывается: его ставит `--answer-from-inbox` в state.json
            try:
                from pathlib import Path

                from src.loop.state import RunState

                saved = RunState.read(Path(ctx.run_dir) / "state.json") or {}
                ctx.st.answer_from_phone = bool(
                    saved.get("answer_from_phone") and saved.get("answer") == resume_from.answer)
            except Exception:
                pass
        if not self._gate_entry(resume_from):
            return None
        if self.stop_check is not None:
            hit = self.stop_check()
            if hit is not None:
                return hit
        ctx.ledger.event("resume_judge", round=ctx.st.round)
        print(f"  {ctx.tag()} judge {ctx.cfg.judge_provider}({ctx.cfg.judge_model}) …",
              flush=True)
        if getattr(resume_from, "judge_reuse_ref", False) and resume_from.ref:
            verdict = await self.judge._judge(resume_from.ref)
        else:
            verdict = await self.judge._judge()
        ctx.progress()
        outcome = await self.after_judge(verdict)
        if outcome is not None and getattr(outcome, "status", "") == "ship":
            ctx.progress()
        elif outcome is None:
            ctx.st.round = start_round
        return outcome
