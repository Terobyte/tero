"""ResumeStep: a stop requested during the resume gate wins over the judge (plan 07 debt F13)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace


def _ctx():
    events: list = []
    return SimpleNamespace(
        st=SimpleNamespace(round=0, judge_failures=0),
        ledger=SimpleNamespace(event=lambda *a, **k: events.append(a)),
        cfg=SimpleNamespace(judge_provider="codex", judge_model="m"),
        progress=lambda: None,
        tag=lambda: "[t]",
    ), events


def _resume_from():
    return SimpleNamespace(gate_first=True, ref="refs/tero/r/p1", base="b",
                           answer="", judge_reuse_ref=False)


class _Judge:
    def __init__(self):
        self.calls = 0

    async def _judge(self, ref=None):
        self.calls += 1
        return "verdict"


def _step(stop_hit):
    from src.loop.resume.entry import ResumeStep

    ctx, events = _ctx()
    judge = _Judge()
    order: list = []

    def gate():
        order.append("gate")
        return SimpleNamespace(ok=True, output="ok")

    def stop_check():
        order.append("stop_check")
        return stop_hit

    async def after_judge(verdict):
        return SimpleNamespace(status="ship")

    step = ResumeStep(ctx, judge, gate, lambda: None, after_judge, stop_check=stop_check)
    return step, judge, order, events


def test_stop_after_resume_gate_skips_judge():
    hit = SimpleNamespace(status="stop", reason="user_stop")
    step, judge, order, events = _step(hit)
    out = asyncio.run(step.enter(_resume_from(), 3))
    assert out is hit
    assert order == ["gate", "stop_check"]
    assert judge.calls == 0
    assert ("resume_judge",) not in events


def test_no_stop_runs_judge_after_gate():
    step, judge, order, _ = _step(None)
    out = asyncio.run(step.enter(_resume_from(), 3))
    assert out.status == "ship"
    assert order == ["gate", "stop_check"]
    assert judge.calls == 1
