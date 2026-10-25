"""Точка решения вместо _halt: вопрос, ожидание, answer/skip/stop."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from src.loop import snapshot as snapshot_mod
from src.loop.decision_actions import (
    LIMIT_REASONS, LIMIT_WAITS_S, apply_action, full_actions, limit_chain, question_actions,
    wait_limit,
)
from src.loop.halt_text import _halt_text_fallback, _legacy_halt_text  # noqa: F401
from src.loop.inbox import Inbox
from src.loop.phase_ctx import PhaseContext, PhaseOutcome
from src.loop.state import RunState, Status, owner_lock
from src.loop.transition import phase_transition
from src.relay.iface import NoopRelay, RelayClient

DECISION_POLL_S = 10


def implemented_actions(reason: str) -> list[str]:
    return full_actions(reason)


class DecisionStep:
    implemented_actions = staticmethod(implemented_actions)

    def __init__(
        self,
        ctx: PhaseContext,
        rollback_with_snapshot: Callable[[str], str | None],
        halt_text: Callable[[str, str | None], str] | None = None,
        write_stopped: Callable[[str, str], None] | None = None,
        relay: RelayClient | None = None,
        poll_s: float | None = None,
        sleep: Callable[[float], None] | None = None,
        round_limit: Callable[[], int] | None = None,
    ) -> None:
        self.ctx = ctx
        self.rollback = rollback_with_snapshot
        self._halt_text_fn = halt_text
        self._write_stopped_fn = write_stopped
        self.relay = relay or NoopRelay()
        self.poll_s = DECISION_POLL_S if poll_s is None else poll_s
        self._sleep = sleep or time.sleep
        self._round_limit = round_limit
        self._reason = ""
        self._qid = ""
        self._auto_wait: int | None = None
        self.secret_paths: list[str] = []

    def halt_text(self, reason: str, ref: str | None = None) -> str:
        if not getattr(self.ctx.cfg, "decisions", False):
            limit = self._round_limit() if self._round_limit else self.ctx.cfg.max_rounds
            return _legacy_halt_text(self.ctx, reason, limit, ref)
        if self._halt_text_fn is not None:
            try:
                return self._halt_text_fn(reason, ref)
            except Exception:
                pass
        if reason == "fuse" and self._round_limit is not None:
            try:
                limit = self._round_limit()
            except Exception:
                limit = None
            if limit is not None:
                try:
                    n = self.ctx.phase.index + 1
                except Exception:
                    n = 1
                tail = f", работа в `{ref}`" if ref else ""
                return f"фаза {n}: {limit} раундов без SHIP{tail}"
        return _halt_text_fallback(self.ctx, reason, ref)

    def write_stopped(self, reason: str, text: str) -> None:
        if self._write_stopped_fn is not None:
            try:
                self._write_stopped_fn(reason, text)
                return
            except Exception:
                pass
        phase_transition(
            self.ctx, status=Status.STOPPED.value, reason=reason, text=text,
            role=self.ctx.role or "judge", event="stop", kind="stop", doing="стоп",
            question=None,
        )

    def _old_halt(self, reason: str) -> PhaseOutcome:
        try:
            ref = self.locked_rollback(reason)
        except Exception:
            ref = None
        try:
            self.ctx.ledger.event("rollback", reason=reason, round=self.ctx.st.round)
        except Exception:
            pass
        if ref is None:
            text = "снимок не сделан, дерево не тронуто"
        else:
            text = self.halt_text(reason, ref)
        self.write_stopped(reason, text)
        return PhaseOutcome(
            status="stop", reason=reason, user_question=self.ctx.st.user_question,
            rounds=self.ctx.st.round, escalated=self.ctx.st.escalated, stop_text=text,
        )

    def _lock(self) -> Any:
        return owner_lock(Path(self.ctx.run_dir) / "state.json")

    def locked_rollback(self, reason: str) -> str | None:
        with self._lock():
            return self.rollback(reason)

    def tree_sha(self, reason: str) -> str | None:
        repo = Path(self.ctx.repo)
        base = self.ctx.checkpoint
        with self._lock():
            if reason == "secrets":
                try:
                    snapshot_mod._git_plain(repo, "reset")
                except Exception:
                    pass
            try:
                return snapshot_mod.tree_of(repo, base)
            except Exception:
                return None

    def _next_seq(self, n: int) -> tuple[str, Any]:
        try:
            cur = RunState.read(self.ctx.run_dir / "state.json") or {}
            raw = cur.get("decision_seq")
        except Exception:
            raw = None
        if isinstance(raw, dict):
            seq = int(raw.get(str(n), 0) or 0) + 1
            seq_map = dict(raw)
        elif isinstance(raw, int) and n == 1:
            seq = raw + 1
            seq_map = {"1": seq}
        else:
            if isinstance(raw, int):
                seq_map = {"1": raw}
            else:
                seq_map = {}
            seq = int(seq_map.get(str(n), 0) or 0) + 1
        seq_map[str(n)] = seq
        return f"{n}-{seq}", seq_map

    def _saved(self) -> dict:
        try:
            return RunState.read(Path(self.ctx.run_dir) / "state.json") or {}
        except Exception:
            return {}

    def blocked_secrets(self) -> list[str]:
        """Секреты в индексе, кроме разрешённых `allow` до конца прогона."""
        from src.loop.git import secret_paths

        allowed = set(self._saved().get("allowed_secrets") or [])
        return [p for p in secret_paths(self.ctx.repo) if p not in allowed]

    def stop_no_snapshot(self, reason: str) -> PhaseOutcome:
        text = "снимок не сделан, дерево не тронуто"
        self.write_stopped(reason, text)
        return PhaseOutcome(
            status="stop", reason=reason, user_question=self.ctx.st.user_question,
            rounds=self.ctx.st.round, escalated=self.ctx.st.escalated, stop_text=text,
        )

    def open_question(self, reason: str, text: str) -> str | None:
        full = implemented_actions(reason)
        if not full:
            return None
        n = self.ctx.phase.index + 1
        actions = question_actions(reason, self._saved(), n)
        if reason == "secrets":
            try:
                self.secret_paths = self.blocked_secrets()
            except Exception:
                self.secret_paths = []
        sha = self.tree_sha(reason)
        if not sha:
            return None
        qid, seq_map = self._next_seq(n)
        question = {"id": qid, "reason": reason, "text": text, "actions": actions,
                    "all_actions": full}
        phase_transition(
            self.ctx, status=Status.WAITING_HUMAN.value,
            reason=reason, text=text, ref="", role="judge",
            event="question", kind="question", doing="ожидание",
            question=question, tree_sha=sha, decision_seq=seq_map,
            question_id=qid, extra={"limit_wait": None},
        )
        return qid

    def wait_answer(self, qid: str):
        inbox = Inbox(self.ctx.run_dir)
        while True:
            try:
                ans = inbox.take(qid, self.relay)
            except Exception:
                ans = None
            try:
                self.ctx.progress()
            except Exception:
                pass
            if ans is not None:
                return ans
            try:
                self._sleep(self.poll_s)
            except Exception:
                pass

    def halt(self, reason: str) -> PhaseOutcome:
        """Открыть точку решения (без ожидания) или прежний стоп.

        При decisions=True возвращает waiting_human сразу: ждёт и применяет
        ответ session.run_phases через resolve(). Без флага или без действий
        по причине — прежний стоп с откатом.
        """
        try:
            decisions = bool(self.ctx.cfg.decisions)
        except Exception:
            decisions = False
        if not decisions or not implemented_actions(reason):
            return self._old_halt(reason)
        waits = limit_chain(self._saved(), self.ctx.phase.index + 1)
        if reason in LIMIT_REASONS and waits is not None and waits < len(LIMIT_WAITS_S):
            # Цепочка wait: повторная неудача — следующая пауза без вопроса.
            self._reason, self._auto_wait = reason, waits
            self._qid = str(self._saved().get("question_id") or "")
            return PhaseOutcome(
                status="waiting_limit", reason=reason, rounds=self.ctx.st.round,
                escalated=self.ctx.st.escalated, decider=self,
            )
        text = self.halt_text(reason, None)
        qid = self.open_question(reason, text)
        if qid is None:
            return self._old_halt(reason)
        self._reason = reason
        self._qid = qid
        return PhaseOutcome(
            status="waiting_human", reason=reason,
            user_question=self.ctx.st.user_question,
            rounds=self.ctx.st.round, escalated=self.ctx.st.escalated,
            stop_text=text, decider=self,
        )

    def resolve(self) -> PhaseOutcome:
        """Дождаться ответа на открытый halt() вопрос и применить его."""
        reason, qid = self._reason, self._qid
        if self._auto_wait is not None:
            return wait_limit(self, reason, qid, self._auto_wait)
        ans = self.wait_answer(qid)
        action = (ans.action or "").strip()
        who = (ans.who or "agent").strip() or "agent"
        if action == "stop":
            try:
                ref = self.locked_rollback(reason)
            except Exception:
                ref = None
            try:
                self.ctx.ledger.event("rollback", reason=reason, round=self.ctx.st.round)
            except Exception:
                pass
            if ref is None:
                t = "снимок не сделан, дерево не тронуто"
            else:
                t = self.halt_text(reason, ref)
            self.write_stopped(reason, t)
            return PhaseOutcome(
                status="stop", reason=reason, user_question=self.ctx.st.user_question,
                rounds=self.ctx.st.round, escalated=self.ctx.st.escalated, stop_text=t,
            )
        if action == "skip":
            try:
                ref = self.locked_rollback(reason)
            except Exception:
                ref = None
            if ref is None:
                return self.stop_no_snapshot(reason)
            try:
                self.ctx.ledger.event("rollback", reason=reason, round=self.ctx.st.round)
            except Exception:
                pass
            findings = []
            try:
                findings = [f.as_prompt() for f in (self.ctx.st.open_findings or [])[-5:]]
            except Exception:
                findings = []
            entry = {
                "phase": self.ctx.phase.index + 1,
                "reason": reason,
                "findings": findings,
                "who": who,
                "ref": ref or "",
            }
            try:
                cur = RunState.read(self.ctx.run_dir / "state.json") or {}
                debt = list(cur.get("debt") or [])
                debt.append(entry)
            except Exception:
                debt = [entry]
            try:
                self.ctx.st.answer = ""
            except Exception:
                pass
            phase_transition(
                self.ctx, status=Status.RUNNING.value, reason=reason,
                text=f"фаза {self.ctx.phase.index + 1} в долг",
                role="judge", event="skip", kind="skip", doing="фаза в долг",
                question=None, answer="", debt=debt, question_id=qid,
            )
            return PhaseOutcome(
                status="skip", reason=reason, rounds=self.ctx.st.round,
                escalated=self.ctx.st.escalated, stop_text="",
            )
        if action == "answer":
            txt = (ans.text or "").strip()
            try:
                self.ctx.st.answer = txt
            except Exception:
                pass
            phase_transition(
                self.ctx, status=Status.RUNNING.value, reason=reason, text=txt,
                role="judge", event="answer", kind="answer", doing="новый раунд",
                question=None, answer=txt, question_id=qid,
            )
            return PhaseOutcome(
                status="answer", reason=reason, rounds=self.ctx.st.round,
                escalated=self.ctx.st.escalated, stop_text="", answer=txt,
            )
        out = apply_action(self, action, reason, qid, ans)
        return out if out is not None else self._old_halt(reason)
