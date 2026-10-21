"""One plan phase: player → protect tests → gate → judge → tester."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from src.config import Config
from src.loop.gates import GateResult, TestCommand, fingerprint, run_all, run_files
from src.loop.git import (
    checkout_paths,
    commit,
    exclude_tero,
    file_hashes,
    head,
    list_files,
    list_worktree_files,
    rollback,
    secret_paths,
    stage_and_diff,
    status_short,
    write_tree,
    restore_tree,
)
from src.loop.ledger import Ledger
from src.loop.plan import Phase, mark_phase_done
from src.loop.prompts import judge_prompt, player_prompt, tester_prompt
from src.loop.schemas import ValidateCtx

SUITE_CONFIG_NAMES = {
    "pytest.ini",
    "conftest.py",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
}

UNAVAILABLE_PLAYER = {
    "status": "blocked",
    "changes": [],
    "start_here": [],
    "blocked_reason": "отчёт недоступен",
}


class AgentRunner(Protocol):
    async def run_role(self, role: str, **kwargs: Any) -> dict[str, Any] | None: ...


@dataclass
class OpenFinding:
    id: str
    severity: str
    route: str
    file: str
    line: int | None
    problem: str
    expected: str
    test_path: str | None = None
    note: str = ""

    def as_prompt(self) -> str:
        extra = f" [test: {self.test_path}]" if self.test_path else ""
        note = f" ({self.note})" if self.note else ""
        return f"{self.id}: {self.problem} → {self.expected}{extra}{note}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "route": self.route,
            "file": self.file,
            "line": self.line,
            "problem": self.problem,
            "expected": self.expected,
            "test_path": self.test_path,
        }


@dataclass
class PhaseOutcome:
    status: str
    reason: str
    user_question: str = ""
    rounds: int = 0
    commit_sha: str = ""
    escalated: bool = False


@dataclass
class _State:
    round: int = 0
    escalated: bool = False
    stopped: bool = False
    stop_reason: str = ""
    user_question: str = ""
    open_findings: list[OpenFinding] = field(default_factory=list)
    next_id: int = 1
    suite_delta: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    gate_output: str = ""
    last_red_fp: frozenset[str] | None = None
    last_red_tree: str | None = None
    last_red_had_fp: bool = False
    protected: dict[str, str] = field(default_factory=dict)
    protected_content: dict[str, str] = field(default_factory=dict)
    protected_by_id: dict[str, str] = field(default_factory=dict)


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
    ) -> None:
        self.phase = phase
        self.repo = repo
        self.plan_path = plan_path
        self.preamble = preamble
        self.cfg = cfg
        self.agents = agents
        self.test_command = test_command
        self.ledger = ledger
        exclude_tero(repo)
        self.checkpoint = head(repo)
        self.suite_hashes = file_hashes(repo, _suite_paths(repo))
        self.state = _State()
        self.tester_enabled = bool(test_command and test_command.run_files)

    async def run(self) -> PhaseOutcome:
        self.ledger.event("phase_start", phase=self.phase.title, index=self.phase.index)
        while True:
            self.state.round += 1
            if self.state.round > self.cfg.max_rounds:
                return self._halt("fuse")
            model, provider, effort = self._player_identity()
            extra = f" {effort}" if effort else ""
            print(
                f"  [раунд {self.state.round}] player {provider}({model}{extra}) …",
                flush=True,
            )
            if not await self._player():
                return self._provider_stop()
            if self._protect_tests():
                if self._no_progress():
                    return self._halt(self.state.stop_reason)
                continue
            gate = self._gate()
            if gate is not None and not gate.ok:
                if self._same_red(gate):
                    if self._no_progress():
                        return self._halt(self.state.stop_reason)
                self.state.gate_output = gate.output
                continue
            self.state.gate_output = gate.output if gate else "тесты не обнаружены"
            print(
                f"  [раунд {self.state.round}] judge "
                f"{self.cfg.judge_provider}({self.cfg.judge_model}) …",
                flush=True,
            )
            verdict = await self._judge()
            if verdict == "unavailable":
                return self._provider_stop()
            if verdict == "invalid":
                if self._no_progress():
                    return self._halt(self.state.stop_reason)
                continue
            if self.state.stopped:
                return self._halt(self.state.stop_reason)
            if verdict == "ship":
                return self._ship()
            if verdict == "tester":
                print(
                    f"  [раунд {self.state.round}] tester "
                    f"{self.cfg.tester_provider}({self.cfg.tester_model}) …",
                    flush=True,
                )
                if not await self._tester():
                    return self._provider_stop()

    async def _player(self) -> bool:
        model, provider, effort = self._player_identity()
        prompt = player_prompt(
            preamble=self.preamble,
            contract=self.phase.body or self.phase.title,
            findings=[item.as_prompt() for item in self.state.open_findings],
            suite_delta=self.state.suite_delta,
            protected=list(self.state.protected),
            gate_output=_tail(self.state.gate_output),
            discarded=self.state.discarded,
        )
        data = await self.agents.run_role(
            role="player",
            provider=provider,
            model=model,
            effort=effort,
            prompt=prompt,
            timeout=self.cfg.player_timeout_s,
            ctx=ValidateCtx(),
        )
        if not isinstance(data, dict) or data.get("__unavailable__"):
            self._provider_error("player", data)
            return False
        if data.get("__invalid__"):
            self._invalid("player", data)
        report = data if data.get("status") in {"done", "blocked"} else UNAVAILABLE_PLAYER
        self.ledger.event("player", phase=self.phase.title, round=self.state.round, model=model)
        self.ledger.artifact(self._round_file("player.json"), _dump(report))
        line = f"  player: {report['status']}, изменений {len(report.get('changes') or [])}"
        if report.get("blocked_reason"):
            line += f" — {report['blocked_reason']}"
        print(line, flush=True)
        self._last_player = report
        return True

    def _protect_tests(self) -> bool:
        self.state.suite_delta = []
        for path, digest in list(self.state.protected.items()):
            current = file_hashes(self.repo, [path]).get(path)
            if current == digest:
                continue
            content = self.state.protected_content.get(path)
            if content is not None:
                target = self.repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            self.ledger.event("protected_violation", path=path, round=self.state.round)
            print(f"  защищённый тест изменён: {path} — откат", flush=True)
            return True

        for path, digest in self.suite_hashes.items():
            if not (self.repo / path).exists():
                checkout_paths(self.repo, self.checkpoint, [path])
                self.ledger.event("suite_violation", path=path, round=self.state.round)
                print(f"  тест удалён вне протокола: {path} — восстановлен", flush=True)
                return True
            current = file_hashes(self.repo, [path]).get(path)
            if current and current != digest:
                self.state.suite_delta.append(path)
        return False

    def _gate(self) -> GateResult | None:
        if self.test_command is None:
            print("  тесты пропущены (нет команды)", flush=True)
            self.ledger.event("gate", result="skipped")
            return None
        print("  тесты …", flush=True)
        started = time.monotonic()
        result = run_all(
            self.test_command,
            repo=self.repo,
            timeout_s=self.cfg.test_timeout_s,
        )
        duration = time.monotonic() - started
        self.ledger.event(
            "gate",
            ok=result.ok,
            exit_code=result.exit_code,
            round=self.state.round,
        )
        self.ledger.artifact(
            f"phase-{self.phase.index:02d}/round-{self.state.round:02d}/tests.log",
            result.output,
        )
        if result.ok:
            print(
                f"  тесты: зелёные (код {result.exit_code}, {duration:.1f} с)",
                flush=True,
            )
        else:
            print(f"  тесты: КРАСНЫЕ (код {result.exit_code})", flush=True)
            for line in result.output.splitlines()[-15:]:
                print(f"    {line}", flush=True)
        return result

    def _same_red(self, result: GateResult) -> bool:
        tree_now = write_tree(self.repo)
        fp = fingerprint(result)
        same = False
        if fp is not None and self.state.last_red_had_fp and fp == self.state.last_red_fp:
            same = True
        elif fp is None and self.state.last_red_tree is not None and tree_now == self.state.last_red_tree:
            same = True
        if fp is not None:
            self.state.last_red_fp = fp
            self.state.last_red_had_fp = True
        self.state.last_red_tree = tree_now
        return same

    async def _judge(self) -> str:
        tree_before = write_tree(self.repo)
        diff = stage_and_diff(self.repo, self.checkpoint)
        status = status_short(self.repo)
        ctx = ValidateCtx(
            open_ids=[item.id for item in self.state.open_findings],
            protected_ids=set(self.state.protected_by_id),
        )
        prompt = judge_prompt(
            preamble=self.preamble,
            contract=self.phase.body or self.phase.title,
            player_report=getattr(self, "_last_player", UNAVAILABLE_PLAYER),
            diff=diff,
            status_short=status,
            suite_delta=self.state.suite_delta,
            test_output=_tail(self.state.gate_output),
            open_findings=[item.as_dict() for item in self.state.open_findings],
            protected_ids=list(self.state.protected_by_id),
        )
        data = await self.agents.run_role(
            role="judge",
            provider=self.cfg.judge_provider,
            model=self.cfg.judge_model,
            effort=self.cfg.judge_effort,
            prompt=prompt,
            timeout=self.cfg.judge_timeout_s,
            ctx=ctx,
        )
        tree_after = write_tree(self.repo)
        if tree_after != tree_before:
            restore_tree(self.repo, tree_before)
            self.ledger.event("judge_tamper", round=self.state.round)
            print("  judge изменил рабочее дерево — откат", flush=True)

        if not isinstance(data, dict) or data.get("__unavailable__"):
            self._provider_error("judge", data)
            return "unavailable"
        if data.get("__invalid__"):
            self._invalid("judge", data)
            return "invalid"

        self.ledger.event(
            "judge",
            verdict=data.get("verdict"),
            round=self.state.round,
        )
        self.ledger.artifact(self._round_file("judge.json"), _dump(data))
        _print_verdict(data)
        return self._apply_verdict(data)

    def _apply_verdict(self, data: dict[str, Any]) -> str:
        verdict = data.get("verdict")
        if verdict == "SHIP":
            return "ship"
        if verdict == "NEEDS_USER":
            self.state.stopped = True
            self.state.stop_reason = "user"
            self.state.user_question = data.get("user_question", "")
            return "user"

        self.state.discarded = []
        for item in data.get("findings") or []:
            if item.get("route") != "discard_test":
                continue
            fid = item.get("id") or ""
            path = self.state.protected_by_id.pop(fid, None)
            if not path:
                continue
            target = self.repo / path
            if target.exists():
                target.unlink()
            self.state.protected.pop(path, None)
            self.state.protected_content.pop(path, None)
            note = "тест снят, чини код"
            self.state.discarded.append(f"{fid}: {note}")
            for found in self.state.open_findings:
                if found.id == fid:
                    found.test_path = None
                    found.note = note

        previous = {row["id"]: row["state"] for row in data.get("previous") or []}
        # discard_test changes the tree; that is progress, not a stall.
        stall = (
            bool(self.state.open_findings)
            and not any(previous.get(item.id) == "resolved" for item in self.state.open_findings)
            and not self.state.discarded
        )
        if stall:
            if self._no_progress():
                return "stop"

        still_open = [
            item
            for item in self.state.open_findings
            if previous.get(item.id) != "resolved"
        ]
        new_blocking: list[OpenFinding] = []
        for item in data.get("findings") or []:
            if item.get("severity") != "blocking":
                continue
            if item.get("route") == "discard_test":
                continue
            route = item.get("route") or "player"
            if route == "tester" and not self.tester_enabled:
                route = "player"
            found = OpenFinding(
                id=f"F{self.state.next_id}",
                severity="blocking",
                route=route,
                file=item.get("file", ""),
                line=item.get("line"),
                problem=item.get("problem", ""),
                expected=item.get("expected", ""),
            )
            self.state.next_id += 1
            new_blocking.append(found)
        self.state.open_findings = still_open + new_blocking
        if any(item.route == "tester" for item in new_blocking) and self.tester_enabled:
            return "tester"
        return "next"

    async def _tester(self) -> bool:
        routed = [item for item in self.state.open_findings if item.route == "tester"]
        if not routed or not self.tester_enabled or self.test_command is None:
            return True
        before_tree = write_tree(self.repo)
        before_files = set(list_worktree_files(self.repo))
        prompt = tester_prompt(
            preamble=self.preamble,
            contract=self.phase.body or self.phase.title,
            diff=stage_and_diff(self.repo, self.checkpoint),
            findings=[item.as_dict() for item in routed],
        )
        data = await self.agents.run_role(
            role="tester",
            provider=self.cfg.tester_provider,
            model=self.cfg.tester_model,
            effort=self.cfg.tester_effort,
            prompt=prompt,
            timeout=self.cfg.tester_timeout_s,
            ctx=ValidateCtx(),
        )
        if not isinstance(data, dict) or data.get("__unavailable__"):
            self._provider_error("tester", data)
            return False
        if data.get("__invalid__"):
            self._invalid("tester", data)
        self.ledger.event("tester", round=self.state.round)
        self.ledger.artifact(self._round_file("tester.json"), _dump(data))
        self._revert_non_test(before_tree, before_files)
        written = 0
        red_confirmed = 0
        for test in data.get("tests") or []:
            path = test.get("path", "")
            fid = test.get("finding_id", "")
            if not path:
                continue
            written += 1
            if not is_test_path(path):
                self._revert_path(before_tree, before_files, path)
                self._note_finding(fid, "tester wrote a non-test path")
                continue
            result = run_files(
                self.test_command,
                [path],
                repo=self.repo,
                timeout_s=self.cfg.test_timeout_s,
            )
            self.ledger.event("red_check", path=path, ok=result.ok)
            if result.ok:
                target = self.repo / path
                if target.exists():
                    target.unlink()
                self._note_finding(fid, "tester test was green; file removed")
                continue
            red_confirmed += 1
            content = (self.repo / path).read_text() if (self.repo / path).exists() else ""
            digest = file_hashes(self.repo, [path]).get(path, "")
            self.state.protected[path] = digest
            self.state.protected_content[path] = content
            self.state.protected_by_id[fid] = path
            for item in self.state.open_findings:
                if item.id == fid:
                    item.test_path = path
        print(
            f"  tester: тестов написано {written}, red-подтверждено {red_confirmed}",
            flush=True,
        )
        return True

    def _revert_non_test(self, before_tree: str, before_files: set[str]) -> None:
        for path in list_worktree_files(self.repo):
            if is_test_path(path):
                continue
            self._revert_path(before_tree, before_files, path)

    def _revert_path(self, before_tree: str, before_files: set[str], path: str) -> None:
        target = self.repo / path
        if path in before_files:
            checkout_paths(self.repo, before_tree, [path])
        elif target.exists():
            target.unlink()

    def _note_finding(self, fid: str, note: str) -> None:
        for item in self.state.open_findings:
            if item.id == fid:
                item.note = note
                item.route = "player"

    def _no_progress(self) -> bool:
        if not self.state.escalated and self.cfg.escalation_model:
            self.state.escalated = True
            self.ledger.event("escalate", round=self.state.round, model=self.cfg.escalation_model)
            print(
                "  нет прогресса → эскалация: "
                f"{self.cfg.escalation_provider}({self.cfg.escalation_model})",
                flush=True,
            )
            return False
        print("  нет прогресса → откат фазы", flush=True)
        self.state.stopped = True
        self.state.stop_reason = "stuck"
        return True

    def _round_file(self, name: str) -> str:
        return f"phase-{self.phase.index:02d}/round-{self.state.round:02d}/{name}"

    def _invalid(self, role: str, data: dict[str, Any]) -> None:
        errors = [str(item) for item in data.get("errors") or []]
        raw = data.get("raw") or ""
        self.ledger.event("invalid", role=role, round=self.state.round, errors=errors)
        self.ledger.artifact(
            self._round_file(f"{role}.invalid.txt"),
            "ошибки:\n" + "\n".join(errors) + "\n\nответ:\n" + (raw or "(пусто)") + "\n",
        )
        print(f"  {role}: ответ не по схеме — {'; '.join(errors[:3])}", flush=True)

    def _provider_error(self, role: str, data: Any) -> None:
        error = data.get("error", "") if isinstance(data, dict) else ""
        self.ledger.event("provider_error", role=role, round=self.state.round, error=error)
        print(f"  {role}: провайдер недоступен — {error}", flush=True)

    def _player_identity(self) -> tuple[str, str, str]:
        if self.state.escalated and self.cfg.escalation_model:
            return (
                self.cfg.escalation_model,
                self.cfg.escalation_provider,
                self.cfg.escalation_effort,
            )
        return self.cfg.player_model, self.cfg.player_provider, self.cfg.player_effort

    def _ship(self) -> PhaseOutcome:
        mark_phase_done(self.plan_path, self.phase)
        stage_and_diff(self.repo, self.checkpoint)
        secrets = secret_paths(self.repo)
        if secrets:
            self.ledger.event("stop", reason="secrets", paths=secrets)
            rollback(self.repo, self.checkpoint)
            return PhaseOutcome(
                status="stop",
                reason="secrets",
                rounds=self.state.round,
                escalated=self.state.escalated,
            )
        sha = commit(self.repo, self.phase.title.lower()[:60])
        self.ledger.event("ship", commit=sha, rounds=self.state.round)
        return PhaseOutcome(
            status="ship",
            reason="ship",
            rounds=self.state.round,
            commit_sha=sha,
            escalated=self.state.escalated,
        )

    def _halt(self, reason: str) -> PhaseOutcome:
        rollback(self.repo, self.checkpoint)
        self.ledger.event("rollback", reason=reason, round=self.state.round)
        self.ledger.event("stop", reason=reason, question=self.state.user_question)
        return PhaseOutcome(
            status="stop",
            reason=reason,
            user_question=self.state.user_question,
            rounds=self.state.round,
            escalated=self.state.escalated,
        )

    def _provider_stop(self) -> PhaseOutcome:
        return self._halt("provider_unavailable")


def is_test_path(rel: str) -> bool:
    path = Path(rel)
    if any(part in {"tests", "test", "__tests__"} for part in path.parts):
        return True
    name = path.name
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith(("_test.py", "_test.go")):
        return True
    return name.endswith(
        (".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.tsx", ".spec.js")
    )


def _suite_paths(repo: Path) -> list[str]:
    paths: list[str] = []
    for rel in list_files(repo):
        if is_test_path(rel) or Path(rel).name in SUITE_CONFIG_NAMES:
            paths.append(rel)
    return paths


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _print_verdict(data: dict[str, Any]) -> None:
    findings = data.get("findings") or []
    blocking = [item for item in findings if item.get("severity") == "blocking"]
    print(
        f"  judge: {data.get('verdict')}, blocking {len(blocking)}, "
        f"advisory {len(findings) - len(blocking)}",
        flush=True,
    )
    for item in blocking[:5]:
        where = item.get("file") or "?"
        if item.get("line"):
            where += f":{item['line']}"
        print(f"    - {where}: {item.get('problem', '')}", flush=True)
    if data.get("user_question"):
        print(f"    вопрос: {data['user_question']}", flush=True)


def _tail(text: str, limit: int = 200) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-limit:])
