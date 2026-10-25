"""Точка решения вместо _halt: вопрос, ожидание, answer/skip/stop."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from src.loop import snapshot as snapshot_mod
from src.loop.decision import actions_for
from src.loop.inbox import Inbox
from src.loop.phase_ctx import PhaseContext, PhaseOutcome
from src.loop.state import RunState, Status, owner_lock
from src.loop.transition import phase_transition
from src.relay.iface import NoopRelay, RelayClient

DECISION_POLL_S = 10
IMPLEMENTED = ("answer", "skip", "stop")


def implemented_actions(reason: str) -> list[str]:
    order = {"answer": 0, "skip": 1, "stop": 2}
    got = [a for a in actions_for(reason) if a in IMPLEMENTED]
    got = list(got)
    if reason == "secrets" and "answer" in got:
        # Фаза 2: только skip|stop (allow добавит фаза 3).
        got.remove("answer")
    return sorted(got, key=lambda a: order.get(a, 99))


def _legacy_halt_text(ctx: PhaseContext, reason: str, limit: Any, ref: str | None) -> str:
    """Тексты стопа при decisions=False — дословно как до точек решения."""
    st = ctx.st
    if reason == "user":
        return st.user_question or reason
    if reason in ("provider_auth", "provider_quota"):
        return ctx.provider_text("", st.last_provider or "?")
    if reason == "provider_unavailable" and st.last_provider_kind in (
        "timeout",
        "stall",
        "auth",
        "quota",
    ):
        return st.last_provider_error or reason
    tail = f", работа в `{ref}`" if ref else ""
    texts = {
        "stuck": f"нет прогресса → откат фазы{tail}",
        "fuse": f"лимит раундов ({limit}) исчерпан → откат фазы{tail}",
        "provider_unavailable": f"провайдер недоступен → откат фазы{tail}",
        "secrets": f"в дифе файлы-секреты → откат фазы{tail}",
    }
    return texts.get(reason, reason)


def _halt_text_fallback(ctx: PhaseContext, reason: str, ref: str | None = None) -> str:
    try:
        n = ctx.phase.index + 1
    except Exception:
        n = 1
    if reason == "user":
        return ctx.st.user_question or reason
    if reason in ("provider_auth", "provider_quota"):
        return ctx.provider_text("", ctx.st.last_provider or "?")
    if reason == "provider_unavailable":
        kind = getattr(ctx.st, "last_provider_kind", "") or ""
        if kind in ("timeout", "stall", "auth", "quota"):
            return ctx.st.last_provider_error or reason
        prov = getattr(ctx.st, "last_provider", "") or "?"
        err = getattr(ctx.st, "last_provider_error", "") or ""
        base = f"провайдер {prov} не отвечает"
        if err:
            base += f" ({err})"
        return base
    if reason == "stuck":
        try:
            finds = [f.as_prompt() for f in (ctx.st.open_findings or [])[-5:]]
        except Exception:
            finds = []
        tail = f": {'; '.join(finds)}" if finds else ""
        text = f"фаза {n} застряла{tail}"
        if ref:
            text += f", работа в `{ref}`"
        return text
    if reason == "fuse":
        try:
            lim = ctx.cfg.max_rounds
        except Exception:
            lim = "?"
        text = f"фаза {n}: {lim} раундов без SHIP"
        if ref:
            text += f", работа в `{ref}`"
        return text
    if reason == "secrets":
        try:
            from src.loop.git import secret_paths as _sp

            paths = _sp(ctx.repo)
        except Exception:
            paths = []
        tail = f": {', '.join(paths)}" if paths else ""
        text = f"в дифе файлы-секреты{tail}"
        if ref:
            text += f", работа в `{ref}`"
        return text
    tail = f", работа в `{ref}`" if ref else ""
    return f"{reason}{tail}"


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

    def open_question(self, reason: str, text: str) -> str | None:
        actions = implemented_actions(reason)
        if not actions:
            return None
        sha = self.tree_sha(reason)
        if not sha:
            return None
        n = self.ctx.phase.index + 1
        qid, seq_map = self._next_seq(n)
        question = {"id": qid, "reason": reason, "text": text, "actions": actions}
        phase_transition(
            self.ctx, status=Status.WAITING_HUMAN.value,
            reason=reason, text=text, ref="", role="judge",
            event="question", kind="question", doing="ожидание",
            question=question, tree_sha=sha, decision_seq=seq_map,
            question_id=qid,
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
                text = "снимок не сделан, дерево не тронуто"
                self.write_stopped(reason, text)
                return PhaseOutcome(
                    status="stop", reason=reason,
                    user_question=self.ctx.st.user_question,
                    rounds=self.ctx.st.round, escalated=self.ctx.st.escalated,
                    stop_text=text,
                )
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
        return self._old_halt(reason)
