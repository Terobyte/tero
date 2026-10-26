"""Точка решения вместо _halt: вопрос, ожидание, answer/skip/stop."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from src.loop import snapshot as snapshot_mod
from src.loop.decision_actions import (
    LIMIT_REASONS, LIMIT_WAITS_S, full_actions, limit_chain, question_actions,
)
from src.loop.decision_resolve import _skip_to_debt, resolve_step
from src.loop.halt_text import _halt_text_fallback, _legacy_halt_text  # noqa: F401
from src.loop.phase_ctx import PhaseContext, PhaseOutcome
from src.loop.state import RunState, Status, owner_lock
from src.loop.transition import phase_transition
from src.loop.watchdog import stop_requested
from src.relay.iface import NoopRelay, RelayClient

__all__ = ["DecisionStep", "implemented_actions"]

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
        now: Callable[[], float] | None = None,
        popen: Any = None,
    ) -> None:
        self.ctx = ctx
        self.rollback = rollback_with_snapshot
        self._halt_text_fn = halt_text
        self._write_stopped_fn = write_stopped
        self.relay = relay or NoopRelay()
        self.poll_s = DECISION_POLL_S if poll_s is None else poll_s
        self._sleep = sleep or time.sleep
        self._now = now or time.time
        self._popen = popen
        self._round_limit = round_limit
        self._reason = ""
        self._qid = ""
        self._auto_wait: int | None = None
        self.secret_paths: list[str] = []
        self._opened_at: float | None = None

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

    def _repair_after_rollback(self) -> None:
        """`git clean -fd` сносит run_dir: пересоздать каркас прогона."""
        try:
            self.ctx.run_dir.mkdir(parents=True, exist_ok=True)
            (self.ctx.run_dir / "inbox").mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _restore_journal(self) -> None:
        """Вернуть decisions/applied/budget из снапшота до отката."""
        try:
            snap = self._snap
        except AttributeError:
            return
        try:
            if not (RunState.read(self.ctx.run_dir / "state.json") or {}).get("decisions"):
                RunState(self.ctx.run_dir / "state.json").update(
                    decisions=snap.get("decisions") or [],
                    applied_uuids=snap.get("applied_uuids") or [],
                    **({"budget": snap["budget"]} if snap.get("budget") is not None else {}))
        except Exception:
            pass

    def _rollback_keep_journal(self, reason: str) -> str | None:
        """Откат дерева с сохранением журнала решений (F11)."""
        try:
            self._snap = RunState.read(self.ctx.run_dir / "state.json") or {}
        except Exception:
            self._snap = {}
        try:
            ref = self.locked_rollback(reason)
        except Exception:
            ref = None
        self._repair_after_rollback()
        self._restore_journal()
        try:
            self.ctx.ledger.event("rollback", reason=reason, round=self.ctx.st.round)
        except Exception:
            pass
        return ref

    def tree_sha(self, reason: str) -> str | None:
        repo = Path(self.ctx.repo)
        base = self.ctx.checkpoint
        with self._lock():
            if reason == "secrets":
                try:
                    snapshot_mod._git_plain(repo, "reset")
                except Exception:
                    pass
            if reason == "judge_pending":
                try:
                    ref = (RunState.read(self.ctx.run_dir / "state.json") or {}).get(
                        "snapshot_ref") or ""
                    if ref:
                        return snapshot_mod.ref_tree(repo, ref)
                except Exception:
                    return None
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

        from src.loop.human_key import trusted_list

        saved = self._saved()
        allowed = set(trusted_list(Path(self.ctx.run_dir).name, saved.get("allowed_secrets"),
                                   saved.get("allowed_secrets_sig")))
        return [p for p in secret_paths(self.ctx.repo) if p not in allowed]

    def stop_no_snapshot(self, reason: str) -> PhaseOutcome:
        text = "снимок не сделан, дерево не тронуто"
        self.write_stopped(reason, text)
        return PhaseOutcome(
            status="stop", reason=reason, user_question=self.ctx.st.user_question,
            rounds=self.ctx.st.round, escalated=self.ctx.st.escalated, stop_text=text,
        )

    def open_question(self, reason: str, text: str, status: str | None = None) -> str | None:
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
            self.ctx, status=status or Status.WAITING_HUMAN.value,
            reason=reason, text=text, ref="", role="judge",
            event="question", kind="question", doing="ожидание",
            question=question, tree_sha=sha, decision_seq=seq_map,
            question_id=qid, extra={"limit_wait": None},
        )
        return qid

    def jp_open(self, reason: str, text: str, ref: str) -> PhaseOutcome:
        """judge_pending при decisions=True: лок держим, дерево не откатываем."""
        t = f"{text}, работа в `{ref}`" if ref else text
        qid = self.open_question("judge_pending", t, Status.JUDGE_PENDING.value)
        if qid is None:
            return self._old_halt(reason)
        self._reason, self._qid = "judge_pending", qid
        try:
            self._opened_at = self._now()
        except Exception:
            self._opened_at = None
        return PhaseOutcome(
            status="waiting_human", reason="judge_pending",
            rounds=self.ctx.st.round, escalated=self.ctx.st.escalated,
            stop_text=text, decider=self,
        )

    def wait_answer(self, qid: str, opened_at: float | None = None):
        from src.loop import autopilot as auto
        return auto.wait_answer(self, qid, opened_at)

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
        try:
            if stop_requested(self.ctx.run_dir):
                reason = "user_stop"
        except Exception:
            pass
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
        try:
            self._opened_at = self._now()
        except Exception:
            self._opened_at = None
        return PhaseOutcome(
            status="waiting_human", reason=reason,
            user_question=self.ctx.st.user_question,
            rounds=self.ctx.st.round, escalated=self.ctx.st.escalated,
            stop_text=text, decider=self,
        )

    def resolve(self) -> PhaseOutcome:
        """Дождаться ответа на открытый halt() вопрос и применить его."""
        return resolve_step(self)

    def _skip_to_debt(self, reason: str, qid: str, who: str) -> PhaseOutcome:
        return _skip_to_debt(self, reason, qid, who)
