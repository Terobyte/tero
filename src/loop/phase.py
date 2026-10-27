"""One plan phase: player → protect tests → gate → judge → tester."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Protocol

from src.config import Config
from src.loop.gates import GateResult, TestCommand, fingerprint, run_all, run_files
from src.loop import gate_scope
from src.loop.git import (  # noqa: F401 — rollback точка подмены
    GitError,
    commit,
    exclude_tero,
    has_staged_changes,
    head,
    rollback,
    stage_and_diff,
    write_tree,
)
from src.errors import scrub_secrets
from src.loop.ledger import Ledger
from src.loop.plan import Phase, mark_phase_done
from src.loop import snapshot as snapshot_mod  # noqa: F401 — snapshot_mod точка подмены
from src.loop.prompts import player_prompt
from src.loop.schemas import ValidateCtx
from src.loop.state import RunState
from src.loop.stream import Stream
from src.loop import suite_guard
from src.loop.compact_step import CompactStep
from src.loop.decision_step import DecisionStep
from src.loop.phase_ctx import (
    K_MAX,
    OpenFinding,
    PhaseContext,
    PhaseOutcome,
    PhaseState,
    ProtectedTest,
    _dump,
    _Next,
    _tail,
)
from src.loop.resume_step import ResumeFrom, baseline_suite
from src.loop.tester_step import TesterStep
from src.loop.findings import in_continuation
from src.loop.judge_step import JudgeStep
from src.loop.suite_guard import (
    AGENT_CONFIG_DIRS,
    AGENT_INSTRUCTION_NAMES,
    SUITE_CONFIG_NAMES,
    SUITE_HOOK_NAMES,
    is_test_path,
)

__all__ = [
    "K_MAX",
    "OpenFinding",
    "PhaseOutcome",
    "ProtectedTest",
    "_Next",
    "_State",
    "run_phase",
    "AGENT_CONFIG_DIRS",
    "AGENT_INSTRUCTION_NAMES",
    "SUITE_CONFIG_NAMES",
    "SUITE_HOOK_NAMES",
    "is_test_path",
]

_State = PhaseState

UNAVAILABLE_PLAYER = {
    "status": "blocked",
    "changes": [],
    "start_here": [],
    "blocked_reason": "отчёт недоступен",
}


class AgentRunner(Protocol):
    async def run_role(self, role: str, **kwargs: Any) -> dict[str, Any] | None: ...


async def run_phase(
    phase: Phase,
    *,
    repo: str | Path,
    plan_path: str | Path,
    preamble: str,
    cfg: Config,
    agents: AgentRunner,
    test_command: TestCommand | None,
    ledger: Ledger,
    total_phases: int = 1,
    start_with_judge: bool = False,
    start_checkpoint: str = "",
    start_round: int = 0,
    start_answer: str = "",
    resume_from: ResumeFrom | None = None,
    pulse: Any = None,
) -> PhaseOutcome:
    runner = _PhaseRunner(
        phase=phase,
        repo=Path(repo),
        plan_path=Path(plan_path),
        preamble=preamble,
        cfg=cfg,
        agents=agents,
        test_command=test_command,
        ledger=ledger,
        total_phases=total_phases,
        start_with_judge=start_with_judge,
        start_checkpoint=start_checkpoint,
        start_round=start_round,
        start_answer=start_answer,
        resume_from=resume_from,
        pulse=pulse,
    )
    return await runner.run()


class _PhaseRunner:
    def __init__(
        self,
        *,
        phase: Phase,
        repo: Path,
        plan_path: Path,
        preamble: str,
        cfg: Config,
        agents: AgentRunner,
        test_command: TestCommand | None,
        ledger: Ledger,
        total_phases: int = 1,
        start_with_judge: bool = False,
        start_checkpoint: str = "",
        start_round: int = 0,
        start_answer: str = "",
        resume_from: ResumeFrom | None = None,
        pulse: Any = None,
    ) -> None:
        self.start_with_judge = start_with_judge
        self.start_round = start_round
        self.start_answer = start_answer
        self.resume_from = resume_from
        self.phase = phase
        self.repo = repo
        self.plan_path = plan_path
        self.preamble = preamble
        self.cfg = cfg
        self.agents = agents
        self.test_command = test_command
        self.ledger = ledger
        exclude_tero(repo)
        self.checkpoint = start_checkpoint or head(repo)
        self.suite_hashes, self.guarded, self.link_targets = baseline_suite(repo, resume_from)
        self.state = _State()
        self.state.answer = start_answer or ""
        self.state.round = start_round or 0
        self.contract = phase.body or phase.title
        self._plan_bytes = self._remember_plan()
        self.run_dir = Path(ledger.run_dir)
        try:
            agents.run_dir = str(self.run_dir)
        except Exception:
            pass
        self.ctx = PhaseContext(
            phase=phase,
            total_phases=total_phases,
            repo=repo,
            plan_path=plan_path,
            preamble=preamble,
            cfg=cfg,
            agents=agents,
            test_command=test_command,
            tester_enabled=not cfg.no_tester
            and bool(test_command and test_command.run_files),
            ledger=ledger,
            run_dir=self.run_dir,
            state_io=RunState(self.run_dir / "state.json"),
            stream=Stream(self.run_dir / "stream.jsonl"),
            pulse=pulse,
            checkpoint=self.checkpoint,
            contract=self.contract,
            suite_hashes=self.suite_hashes,
            guarded=self.guarded,
            link_targets=self.link_targets,
            st=self.state,
        )
        self.compact = CompactStep(self.ctx)
        self.tester = TesterStep(self.ctx, self._guard_plan)
        self.judge = JudgeStep(
            self.ctx,
            self.compact,
            self._rollback_with_snapshot,
            self._guard_plan,
            self._no_progress,
        )

    def _remember_plan(self) -> bytes | None:
        from src.loop import plan_guard as _pg

        return _pg.remember_plan(self)

    def _guard_plan(self, role: str) -> None:
        from src.loop import plan_guard as _pg

        return _pg.guard_plan(self, role)


    async def run(self) -> PhaseOutcome:  # noqa: C901
        self.ledger.event("phase_start", phase=self.phase.title, index=self.phase.index)
        from datetime import datetime, timezone

        if self.start_with_judge:
            from src.loop.resume_step import ResumeStep

            announcer = ResumeStep(
                self.ctx, self.judge, self._gate, self._protect_tests, self._after_judge, self._stop_checks()[0],
            )
            entered = await announcer.enter(self.resume_from_entry(), self.start_round)
            if entered is not None:
                return entered
        self.ctx.update_state(
            phase=self.phase.title,
            phase_index=self.phase.index,
            phase_started=datetime.now(timezone.utc).isoformat(),
            phase_done=False,
            commit="",
            round=self.state.round,
        )
        while True:
            limit = self.round_limit()
            if self.state.round + 1 > limit:
                return self._halt("fuse")
            self.state.round += 1
            self.ctx.update_state(round=self.state.round)
            if (hit := self._stop_checks()[0]()) is not None:
                return hit
            self.state.compact_count = 0
            self.state.judge_compact_used = False
            self.ctx.emit(
                "start_step",
                phase=self.phase.title,
                round=self.state.round,
                role="player",
            )
            model, provider, effort = self.ctx.player_identity()
            extra = f" {effort}" if effort else ""
            print(
                f"  {self.ctx.tag()} player {provider}({model}{extra}) …",
                flush=True,
            )
            player_ok = await self._player()
            self.ctx.progress()
            hit = self._stop_checks()[1](player_ok)
            if hit is not None:
                return hit
            if player_ok is None:
                if self.state.stop_reason == "snapshot_failed":
                    return self._halt("snapshot_failed")
                return self._halt(self.state.stop_reason or "provider_unavailable")
            if not player_ok:
                # Падение исполнителя: дерево не откатывается, следующий
                # раунд — новый вызов исполнителя с diff снимка в промпте.
                # Два падения подряд — halt, как раньше по смыслу.
                if self.state.player_crashes >= 2:
                    return self._halt("provider_unavailable")
                continue
            if self._protect_tests():
                if not in_continuation(self.state) and self._no_progress():
                    return self._halt("stuck")
                continue
            if (hit := self._stop_checks()[0]()) is not None:
                return hit
            gate = self._gate()
            self.ctx.progress()
            if gate is not None and not gate.ok:
                hit = self._stop_checks()[2](gate)
                if hit is not None:
                    return hit
                if self._same_red(gate):
                    if not in_continuation(self.state) and self._no_progress():
                        return self._halt("stuck")
                self.state.gate_output = gate.output
                continue
            self.state.gate_output = gate.output if gate else "тесты не обнаружены"
            hit = self._stop_checks()[3]()
            if hit is not None:
                return hit
            print(
                f"  {self.ctx.tag()} judge "
                f"{self.cfg.judge_provider}({self.cfg.judge_model}) …",
                flush=True,
            )
            verdict = await self.judge._judge()
            self.ctx.progress()
            if (hit := self._stop_checks()[4]()) is not None:
                return hit
            outcome = await self._after_judge(verdict)
            if outcome is not None:
                if outcome.status == "ship":
                    self.ctx.progress()
                return outcome

    async def _after_judge(self, verdict: _Next) -> PhaseOutcome | None:
        """Apply a judge verdict. None means: continue the normal cycle."""
        match verdict:
            case _Next.JUDGE_PENDING:
                return self.judge._stop_judge_pending(self.state.pending_reason or "judge_failed")
            case _Next.USER:
                return self._halt("user")
            case _Next.STOP:
                return self._halt("stuck")
            case _Next.SHIP:
                if not self._full_gate_before_ship():
                    hit = self._stop_checks()[5]()
                    if hit is not None:
                        return hit
                    # Полный гейт красный: не шипать, следующий раунд — игрок.
                    return None
                hit = self._stop_checks()[5]()
                if hit is not None:
                    return hit
                return self._ship()
            case _Next.TESTER:
                print(
                    f"  {self.ctx.tag()} tester "
                    f"{self.cfg.tester_provider}({self.cfg.tester_model}) …",
                    flush=True,
                )
                hit = self._stop_checks()[3]()
                if hit is not None:
                    return hit
                tester_ok = await self.tester._tester()
                self.ctx.progress()
                if tester_ok is None:
                    return self._halt(self.state.stop_reason or "provider_unavailable")
                if not tester_ok:
                    return self._halt("provider_unavailable")
                return None
            case _Next.NEXT:
                return None
            case _:
                # invalid or unavailable judge replies are consumed inside
                # _judge (retry loop): reaching here would be a bug,
                # so fall through without escalating the player.
                return None

    async def _player(self) -> bool | None:
        """True/False — обычный исход; None — auth/quota/snapshot_failed, caller делает halt."""
        model, provider, effort = self.ctx.player_identity()
        while True:
            check = self.compact._player_compact_check()
            data = await self._raw_player_call(
                model, provider, effort, self._player_prompt(), check
            )
            if data is None:
                return False
            if data is False:
                if self.state.last_provider_kind == "auth":
                    self.state.stop_reason = "provider_auth"
                    return None
                if self.state.last_provider_kind == "quota":
                    self.state.stop_reason = "provider_quota"
                    return None
                return False
            if not data.get("__compacted__"):
                break
            if check is None:
                # Компакт без запрошенного порога (лимит исчерпан, дальше
                # вызов шёл без compact_check): проверенного снимка нет —
                # останавливаемся без гейта и судьи (F5, F9).
                return self.compact._player_compact_failed()
            self.state.compact_count += 1
            ref = self.compact._record_compact("player", data)
            if ref is None:
                # Снимок компакта не записался: без проверенного ref бриф
                # для нового вызова не построить — останавливаемся без
                # гейта и судьи (F5, F9).
                return self.compact._player_compact_failed()
        ref = self._snapshot_after_player()
        if data is None:
            if self.state.last_provider_kind == "auth":
                self.state.stop_reason = "provider_auth"
                return None
            if self.state.last_provider_kind == "quota":
                self.state.stop_reason = "provider_quota"
                return None
            self.state.player_crashes += 1
            self.state.resume_active = True
            self.state.had_crash = True
            if ref:
                self.state.resume_ref = ref
                self.state.resume_base = self.checkpoint
            return False
        if data.get("__invalid__"):
            # Уже зарегистрировано в _raw_player_call: без повтора события.
            # F14: любой некомпактный ответ (включая __invalid__) сбрасывает
            # счётчик падений — иначе сбой, невалид, сбой считались бы двумя
            # последовательными падениями.
            self.state.player_crashes = 0
            report = UNAVAILABLE_PLAYER
        else:
            self.state.player_crashes = 0
            if ref:
                # Успешный ответ не начинает чистую попытку: блок продолжения
                # остаётся, ref обновляется под актуальное дерево.
                self.state.resume_ref = ref
                self.state.resume_base = self.checkpoint
                self.state.resume_active = True
            report = data if data.get("status") in {"done", "blocked"} else UNAVAILABLE_PLAYER
        self.ledger.event("player", phase=self.phase.title, round=self.state.round, model=model)
        self.ledger.artifact(self.ctx.round_file("player.json"), _dump(report))
        line = f"  player: {report['status']}, изменений {len(report.get('changes') or [])}"
        if report.get("blocked_reason"):
            line += f" — {report['blocked_reason']}"
        print(line, flush=True)
        return True

    def round_limit(self) -> int:
        from src.loop.resume_step import resume_limit as _rl

        limit = _rl(self.resume_from, self.cfg.max_rounds)
        return max(limit, self.start_round + 1) if self.start_answer else limit

    def resume_from_entry(self) -> Any:
        from src.loop.resume_step import resume_entry as _re

        return _re(self.resume_from)

    def _player_prompt(self) -> str:
        from src.loop.resume_step import _continuation_block

        continuation = _continuation_block(
            self.repo, self.state.resume_base, self.state.resume_ref
        ) if self.state.resume_active else ""
        return player_prompt(
            preamble=self.preamble,
            contract=self.contract,
            findings=[item.as_prompt() for item in self.state.open_findings],
            suite_delta=self.state.suite_delta,
            protected=list(self.state.protected),
            gate_output=_tail(self.state.gate_output),
            discarded=self.state.discarded,
            continuation=self.state.resume_active,
            continuation_diff=continuation,
            answer=self.state.answer, phone=self.state.answer_from_phone,
            forbidden=getattr(self.resume_from, "forbidden", None),
        )

    async def _raw_player_call(
        self,
        model: str,
        provider: str,
        effort: str,
        prompt: str,
        compact_check: Any,
    ) -> dict[str, Any] | bool | None:
        """None — исключение/недоступность провайдера (счётчик падений +1).

        False — недоступный ответ провайдера (auth/quota handled via returns:
        returns True + Sets stop_reason). Dict — сырой ответ агента.
        """
        from datetime import datetime, timezone

        self.compact._clear_compact_requested("player")
        self.ctx.update_state(
            role="player",
            provider=provider,
            model=model,
            step_started=datetime.now(timezone.utc).isoformat(),
        )
        try:
            data = await self.agents.run_role(
                role="player",
                provider=provider,
                model=model,
                effort=effort,
                prompt=prompt,
                timeout=self.cfg.player_timeout_s,
                ctx=ValidateCtx(),
                emit=self.ctx.emit,
                compact_check=compact_check,
                compact_at_tokens=(
                    self.cfg.compact_player_at_tokens
                    if provider == "claude"
                    else 0
                ),
            )
        except Exception as exc:
            self._guard_plan("player")
            ref = self._snapshot_after_player()
            self.ctx.provider_error(
                "player",
                {"error": scrub_secrets(str(exc) or type(exc).__name__)},
                provider,
            )
            self.state.player_crashes += 1
            self.state.resume_active = True
            self.state.had_crash = True
            if ref:
                self.state.resume_ref = ref
                self.state.resume_base = self.checkpoint
            return None
        self._guard_plan("player")
        data = self.ctx.check_reply("player", data, provider)
        if data is None:
            if self.state.last_provider_kind in ("auth", "quota"):
                return False
            self.state.player_crashes += 1
            self.state.resume_active = True
            self.state.had_crash = True
            ref = self._snapshot_after_player()
            if ref:
                self.state.resume_ref = ref
                self.state.resume_base = self.checkpoint
            return None
        return data

    def _protect_tests(self) -> bool:
        return suite_guard.protect_tests(
            self.repo,
            self.state,
            ledger=self.ledger,
            checkpoint=self.checkpoint,
            suite_hashes=self.suite_hashes,
            guarded=self.guarded,
            link_targets=self.link_targets,
        )

    def _gate(self) -> GateResult | None:
        if self.test_command is None:
            gate_scope.report_skipped(self.ctx, self.ledger)
            return None
        from datetime import datetime, timezone

        print("  тесты …", flush=True)
        self.ctx.update_state(
            role="gate",
            provider="",
            model="",
            step_started=datetime.now(timezone.utc).isoformat(),
        )
        self.ctx.emit(
            "gate_start",
            round=self.state.round,
            role="gate",
            text="тесты идут …",
        )
        started = time.monotonic()
        files = gate_scope.scoped_files(
            self.test_command,
            repo=self.repo,
            enabled=self.cfg.gate_scope == "changed",
            checkpoint=self.checkpoint,
        )
        scope, run = gate_scope.describe(files)
        if run is None:
            result = run_all(
                self.test_command,
                repo=self.repo,
                timeout_s=self.cfg.test_timeout_s,
            )
        else:
            result = run_files(
                self.test_command,
                run,
                repo=self.repo,
                timeout_s=self.cfg.test_timeout_s,
            )
        gate_scope.finish_gate(
            self.ctx, self.ledger, self.state,
            command=self.test_command, result=result, run=run, scope=scope,
            repo=self.repo, marked=self.cfg.gate_scope == "changed",
            timeout_s=self.cfg.test_timeout_s, duration=time.monotonic() - started,
        )
        return result

    def _same_red(self, result: GateResult) -> bool:
        tree_now = write_tree(self.repo)
        fp = fingerprint(result)
        same = False
        if fp is not None and fp == self.state.last_red_fp:
            same = True
        elif fp is None and self.state.last_red_tree is not None and tree_now == self.state.last_red_tree:
            same = True
        if fp is not None:
            self.state.last_red_fp = fp
        self.state.last_red_tree = tree_now
        return same

    def _no_progress(self) -> bool:
        if self.cfg.escalate_at_round:
            return self.state.round >= self.cfg.escalate_at_round
        if not self.state.escalated and self.cfg.escalation_provider:
            self.state.escalated = True
            self.ledger.event(
                "escalate",
                round=self.state.round,
                provider=self.cfg.escalation_provider,
                model=self.cfg.escalation_model,
            )
            print(
                "  нет прогресса → эскалация: "
                f"{self.cfg.escalation_provider}({self.cfg.escalation_model})",
                flush=True,
            )
            return False
        if self.state.round <= getattr(self.resume_from, "grace_until", 0):
            return False  # действие more: эти раунды без stuck
        print("  нет прогресса → откат фазы", flush=True)
        return True

    def _snapshot_after_player(self, *a: Any, **k: Any) -> str | None:
        from src.loop import snapshot_step as _snap

        return _snap.take_after_player(self, *a, **k)

    def _rollback_with_snapshot(self, *a: Any, **k: Any) -> str | None:
        from src.loop import snapshot_step as _snap

        return _snap.rollback_with_snapshot(self, *a, **k)

    def _full_gate_before_ship(self) -> bool:
        """Полный прогон перед SHIP в scoped-режиме. True — можно шипать."""
        if self.test_command is None:
            return True
        outcome = gate_scope.full_gate(
            self.test_command, self.repo, self.ctx, self.ledger, self.state,
            timeout_s=self.cfg.test_timeout_s,
            enabled=self.cfg.gate_scope == "changed",
        )
        return outcome is None or outcome == "green"

    def _ship(self) -> PhaseOutcome:
        stage_and_diff(self.repo, self.checkpoint)
        secrets = self._decider().blocked_secrets()
        if secrets:
            if getattr(self.cfg, "decisions", False):
                return self._halt("secrets")
            ref = self._rollback_with_snapshot("secrets")
            snapshot_note = f", работа в `{ref}`" if ref else ""
            text = f"secrets: {', '.join(secrets)}{snapshot_note}"
            if ref is None:
                text = "снимок не сделан, дерево не тронуто"
            self._write_stopped("secrets", text)
            return PhaseOutcome(
                status="stop",
                reason="secrets",
                rounds=self.state.round,
                escalated=self.state.escalated,
                stop_text=text,
            )
        plan_bytes = self.plan_path.read_bytes()
        mark_phase_done(self.plan_path, self.phase)
        stage_and_diff(self.repo, self.checkpoint)
        msg, trl = self.phase.title.lower()[:60], f"Tero-Phase: {self.run_dir.name}/{self.phase.index + 1}" if getattr(self.cfg, "decisions", False) else ""
        try:
            sha = commit(self.repo, msg, trailer=trl, allow_empty=bool(trl)) if trl or has_staged_changes(self.repo) else head(self.repo)
        except GitError:
            self.plan_path.write_bytes(plan_bytes)
            raise
        self.ledger.event("ship", commit=sha, rounds=self.state.round)
        self.ctx.emit("ship", commit=sha, round=self.state.round, role="judge")
        self.ctx.update_state(commit=sha, phase_done=True)
        return PhaseOutcome(
            status="ship",
            reason="ship",
            rounds=self.state.round,
            commit_sha=sha,
            escalated=self.state.escalated,
        )

    def _decider(self) -> DecisionStep:
        return DecisionStep(self.ctx, self._rollback_with_snapshot, relay=self.ctx.relay, round_limit=self.round_limit)

    def _write_stopped(self, reason: str, text: str) -> None:
        self._decider().write_stopped(reason, text)

    def _stop_checks(self) -> Any:
        from src.loop.stop_step import checks
        return checks(self)

    def _halt(self, reason: str) -> PhaseOutcome:
        from src.loop.stop_step import halt
        return halt(self, reason)

