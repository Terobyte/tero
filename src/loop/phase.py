"""One plan phase: player → protect tests → gate → judge → tester."""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Protocol

from src.config import Config
from src.loop.gates import GateResult, TestCommand, fingerprint, print_red_tail, run_all, run_files
from src.loop.git import (
    GitError,
    checkout_paths,
    commit,
    exclude_tero,
    file_hashes,
    has_staged_changes,
    head,
    list_files,
    list_worktree_files,
    rollback,
    secret_paths,
    stage_and_diff,
    write_tree,
    restore_tree,
)
from src.loop.ledger import Ledger
from src.loop.plan import Phase, mark_phase_done
from src.loop import snapshot as snapshot_mod
from src.loop.prompts import judge_prompt, player_prompt, tester_prompt
from src.loop.schemas import ValidateCtx
from src.loop.state import RunState, Status
from src.loop.stream import Stream

SUITE_CONFIG_NAMES = {
    "pytest.ini",
    "conftest.py",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
}
# A new one of these can skip or rewrite tests without touching a test file.
SUITE_HOOK_NAMES = {"conftest.py", "pytest.ini"}
# The judge's CLI loads these from the workspace (MCP servers, hooks, permissions).
AGENT_CONFIG_DIRS = {".claude", ".codex", ".cursor", ".gemini", ".opencode"}
AGENT_INSTRUCTION_NAMES = {
    ".mcp.json",
    "CLAUDE.md",
    "CLAUDE.local.md",
    "AGENTS.md",
    "AGENTS.override.md",
    "GEMINI.md",
    ".cursorrules",
    "opencode.json",
    "opencode.jsonc",
}
_GUARDED_NAMES = SUITE_HOOK_NAMES | AGENT_INSTRUCTION_NAMES
_GUARDED_FOLDED = {name.casefold() for name in _GUARDED_NAMES}
_AGENT_DIRS_FOLDED = {name.casefold() for name in AGENT_CONFIG_DIRS}
_SUITE_HOOKS_FOLDED = {name.casefold() for name in SUITE_HOOK_NAMES}
_PRUNE_DIRS = {".git", ".tero", "node_modules"}

UNAVAILABLE_PLAYER = {
    "status": "blocked",
    "changes": [],
    "start_here": [],
    "blocked_reason": "отчёт недоступен",
}


class _Next(Enum):
    SHIP = auto()
    USER = auto()
    STOP = auto()
    TESTER = auto()
    NEXT = auto()
    INVALID = auto()
    UNAVAILABLE = auto()


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
    test_paths: list[str] = field(default_factory=list)
    note: str = ""

    def as_prompt(self) -> str:
        extra = f" [test: {', '.join(self.test_paths)}]" if self.test_paths else ""
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
            "test_paths": self.test_paths,
        }


@dataclass
class PhaseOutcome:
    status: str
    reason: str
    user_question: str = ""
    rounds: int = 0
    commit_sha: str = ""
    escalated: bool = False
    stop_text: str = ""


@dataclass
class ProtectedTest:
    """A tester's red test, restored byte-for-byte if the player touches it."""

    digest: str
    content: str


@dataclass
class _State:
    round: int = 0
    escalated: bool = False
    user_question: str = ""
    open_findings: list[OpenFinding] = field(default_factory=list)
    next_id: int = 1
    suite_delta: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    gate_output: str = ""
    last_red_fp: frozenset[str] | None = None
    last_red_tree: str | None = None
    protected: dict[str, ProtectedTest] = field(default_factory=dict)
    protected_by_id: dict[str, list[str]] = field(default_factory=dict)


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
    ) -> None:
        self.phase = phase
        self.total_phases = total_phases
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
        self.guarded, self.link_targets = _snapshot_guarded(repo, self.suite_hashes)
        self.state = _State()
        self.tester_enabled = bool(test_command and test_command.run_files)
        self.contract = phase.body or phase.title
        self.run_dir = Path(ledger.run_dir)
        self.state_io = RunState(self.run_dir / "state.json")
        self.stream = Stream(self.run_dir / "stream.jsonl")
        self._role = ""

    def _emit(self, type: str, **fields: Any) -> None:
        fields.setdefault("phase", self.phase.title)
        fields.setdefault("round", self.state.round)
        if fields.get("role"):
            self._role = fields["role"]
        else:
            fields["role"] = self._role
        if "text" not in fields:
            fields["text"] = _stream_text(type, fields)
        self.stream.emit(type, **fields)

    def _update_state(self, **fields: Any) -> None:
        if fields.get("role"):
            self._role = fields["role"]
        self.state_io.update(**fields)

    def _tag(self) -> str:
        return f"[фаза {self.phase.index + 1}/{self.total_phases} · раунд {self.state.round}]"

    async def run(self) -> PhaseOutcome:
        self.ledger.event("phase_start", phase=self.phase.title, index=self.phase.index)
        from datetime import datetime, timezone

        self._update_state(
            phase=self.phase.title,
            phase_index=self.phase.index,
            phase_started=datetime.now(timezone.utc).isoformat(),
            phase_done=False,
            commit="",
            round=0,
        )
        while True:
            if self.state.round + 1 > self.cfg.max_rounds:
                return self._halt("fuse")
            self.state.round += 1
            self._update_state(round=self.state.round)
            self._emit(
                "start_step",
                phase=self.phase.title,
                round=self.state.round,
                role="player",
            )
            model, provider, effort = self._player_identity()
            extra = f" {effort}" if effort else ""
            print(
                f"  {self._tag()} player {provider}({model}{extra}) …",
                flush=True,
            )
            if not await self._player():
                return self._halt("provider_unavailable")
            if self._protect_tests():
                if self._no_progress():
                    return self._halt("stuck")
                continue
            gate = self._gate()
            if gate is not None and not gate.ok:
                if self._same_red(gate):
                    if self._no_progress():
                        return self._halt("stuck")
                self.state.gate_output = gate.output
                continue
            self.state.gate_output = gate.output if gate else "тесты не обнаружены"
            print(
                f"  {self._tag()} judge "
                f"{self.cfg.judge_provider}({self.cfg.judge_model}) …",
                flush=True,
            )
            verdict = await self._judge()
            match verdict:
                case _Next.UNAVAILABLE:
                    return self._halt("provider_unavailable")
                case _Next.INVALID:
                    if self._no_progress():
                        return self._halt("stuck")
                case _Next.USER:
                    return self._halt("user")
                case _Next.STOP:
                    return self._halt("stuck")
                case _Next.SHIP:
                    return self._ship()
                case _Next.TESTER:
                    print(
                        f"  {self._tag()} tester "
                        f"{self.cfg.tester_provider}({self.cfg.tester_model}) …",
                        flush=True,
                    )
                    if not await self._tester():
                        return self._halt("provider_unavailable")
                case _Next.NEXT:
                    pass

    async def _player(self) -> bool:
        model, provider, effort = self._player_identity()
        prompt = player_prompt(
            preamble=self.preamble,
            contract=self.contract,
            findings=[item.as_prompt() for item in self.state.open_findings],
            suite_delta=self.state.suite_delta,
            protected=list(self.state.protected),
            gate_output=_tail(self.state.gate_output),
            discarded=self.state.discarded,
        )
        from datetime import datetime, timezone

        self._update_state(
            role="player",
            provider=provider,
            model=model,
            step_started=datetime.now(timezone.utc).isoformat(),
        )
        data = await self.agents.run_role(
            role="player",
            provider=provider,
            model=model,
            effort=effort,
            prompt=prompt,
            timeout=self.cfg.player_timeout_s,
            ctx=ValidateCtx(),
            emit=self._emit,
        )
        data = self._check_reply("player", data)
        if data is None:
            return False
        report = data if data.get("status") in {"done", "blocked"} else UNAVAILABLE_PLAYER
        self.ledger.event("player", phase=self.phase.title, round=self.state.round, model=model)
        self.ledger.artifact(self._round_file("player.json"), _dump(report))
        line = f"  player: {report['status']}, изменений {len(report.get('changes') or [])}"
        if report.get("blocked_reason"):
            line += f" — {report['blocked_reason']}"
        print(line, flush=True)
        return True

    def _protect_tests(self) -> bool:
        # Restore every violation in one pass: one per round would burn a round
        # (and look like no progress) for each extra file the player touched.
        self.state.suite_delta = []
        violated = False
        for path, test in list(self.state.protected.items()):
            current = file_hashes(self.repo, [path]).get(path)
            if current == test.digest:
                continue
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(test.content)
            self.ledger.event("protected_violation", path=path, round=self.state.round)
            print(f"  защищённый тест изменён: {path} — откат", flush=True)
            violated = True

        for path, digest in self.suite_hashes.items():
            if not (self.repo / path).exists():
                checkout_paths(self.repo, self.checkpoint, [path])
                self.ledger.event("suite_violation", path=path, round=self.state.round)
                print(f"  тест удалён вне протокола: {path} — восстановлен", flush=True)
                violated = True
                continue
            current = file_hashes(self.repo, [path]).get(path)
            if current and current != digest:
                self.state.suite_delta.append(path)

        # Not a burned round: the player's code still goes to the gate and judge.
        current, _ = _snapshot_guarded(
            self.repo, self.suite_hashes, self.link_targets, set(self.guarded)
        )
        removed_dirs: set[str] = set()
        for path in sorted(set(current) | set(self.guarded), key=lambda p: (p in self.guarded, p)):
            if any(path.startswith(done + "/") for done in removed_dirs):
                # A parent dir was already removed wholesale (rmtree) above;
                # this entry is stale — nothing left to do for it.
                continue
            target = self.repo / path
            original = self.guarded.get(path)
            now = current.get(path)
            if original is not None and now is not None and now == original:
                continue
            if target.is_dir() and not target.is_symlink():
                removed_dirs.add(path)
            for anc in _strip_unsafe_ancestors(self.repo, path):
                self.ledger.event(
                    "agent_config_violation",
                    path=anc,
                    round=self.state.round,
                )
                print(
                    f"  конфиг агента изменён вне протокола: {anc} — откат",
                    flush=True,
                )
            _remove_guarded(target)
            if original is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                kind, payload = original
                if kind == "link":
                    os.symlink(payload, target)
                elif kind == "dir":
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.write_bytes(payload)
            hook = Path(path).name.casefold() in _SUITE_HOOKS_FOLDED
            self.ledger.event(
                "suite_violation" if hook else "agent_config_violation",
                path=path,
                round=self.state.round,
            )
            what = "хук тестов" if hook else "конфиг агента"
            print(f"  {what} изменён вне протокола: {path} — откат", flush=True)
        return violated

    def _gate(self) -> GateResult | None:
        if self.test_command is None:
            from datetime import datetime, timezone

            print("  тесты пропущены (нет команды)", flush=True)
            self._emit("gate", result="skipped", role="gate")
            self._update_state(
                role="gate",
                provider="",
                model="",
                step_started=datetime.now(timezone.utc).isoformat(),
                gate={"ok": True, "result": "skipped"},
            )
            self.ledger.event("gate", result="skipped")
            return None
        from datetime import datetime, timezone

        from src.loop.gates import tail_lines

        print("  тесты …", flush=True)
        self._update_state(
            role="gate",
            provider="",
            model="",
            step_started=datetime.now(timezone.utc).isoformat(),
        )
        self._emit(
            "gate_start",
            round=self.state.round,
            role="gate",
            text="тесты идут …",
        )
        started = time.monotonic()
        result = run_all(
            self.test_command,
            repo=self.repo,
            timeout_s=self.cfg.test_timeout_s,
        )
        duration = time.monotonic() - started
        tail = tail_lines(result.output)
        self._emit(
            "gate",
            ok=result.ok,
            exit_code=result.exit_code,
            round=self.state.round,
            role="gate",
            tail=tail,
        )
        self._update_state(
            gate={
                "ok": result.ok,
                "exit_code": result.exit_code,
                "tail": tail,
            }
        )
        self.ledger.event(
            "gate",
            ok=result.ok,
            exit_code=result.exit_code,
            round=self.state.round,
        )
        self.ledger.artifact(
            self._round_file("tests.log"),
            result.output,
        )
        if result.ok:
            print(
                f"  тесты: зелёные (код {result.exit_code}, {duration:.1f} с)",
                flush=True,
            )
        elif result.timed_out:
            print(f"  тесты: таймаут ({self.cfg.test_timeout_s} с)", flush=True)
            print_red_tail(result.output)
        else:
            print(f"  тесты: КРАСНЫЕ (код {result.exit_code})", flush=True)
            print_red_tail(result.output)
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

    async def _judge(self) -> _Next:
        from datetime import datetime, timezone

        self._update_state(
            role="judge",
            provider=self.cfg.judge_provider,
            model=self.cfg.judge_model,
            step_started=datetime.now(timezone.utc).isoformat(),
        )
        tree_before = write_tree(self.repo)
        ctx = ValidateCtx(
            open_ids=[item.id for item in self.state.open_findings],
            protected_ids=set(self.state.protected_by_id),
        )
        prompt = judge_prompt(
            preamble=self.preamble,
            contract=self.contract,
            open_findings=[item.as_dict() for item in self.state.open_findings],
            protected_ids=list(self.state.protected_by_id),
            suite_delta=self.state.suite_delta,
        )
        data = await self.agents.run_role(
            role="judge",
            provider=self.cfg.judge_provider,
            model=self.cfg.judge_model,
            effort=self.cfg.judge_effort,
            prompt=prompt,
            timeout=self.cfg.judge_timeout_s,
            ctx=ctx,
            emit=self._emit,
        )
        tree_after = write_tree(self.repo)
        if tree_after != tree_before:
            restore_tree(self.repo, tree_before)
            self.ledger.event("judge_tamper", round=self.state.round)
            print("  judge изменил рабочее дерево — откат", flush=True)

        data = self._check_reply("judge", data)
        if data is None:
            return _Next.UNAVAILABLE
        if data.get("__invalid__"):
            return _Next.INVALID

        self._emit(
            "verdict",
            verdict=data.get("verdict"),
            round=self.state.round,
            role="judge",
        )
        blocking = [
            item for item in data.get("findings") or [] if item.get("severity") == "blocking"
        ]
        first = blocking[0] if blocking else {}
        self._update_state(
            role="judge",
            provider=self.cfg.judge_provider,
            model=self.cfg.judge_model,
            verdict={
                "verdict": data.get("verdict"),
                "blocking": len(blocking),
                "first": {
                    "issue": first.get("problem", ""),
                    "file": first.get("file", ""),
                    "line": first.get("line"),
                },
            },
        )
        self.ledger.event(
            "judge",
            verdict=data.get("verdict"),
            round=self.state.round,
        )
        self.ledger.artifact(self._round_file("judge.json"), _dump(data))
        _print_verdict(data)
        return self._apply_verdict(data)

    def _apply_verdict(self, data: dict[str, Any]) -> _Next:
        verdict = data.get("verdict")
        if verdict == "SHIP":
            return _Next.SHIP
        if verdict == "NEEDS_USER":
            self.state.user_question = data.get("user_question", "")
            return _Next.USER

        findings = data.get("findings") or []
        self._discard_tests(findings)
        previous = {row["id"]: row["state"] for row in data.get("previous") or []}
        if self._stalled(previous) and self._no_progress():
            return _Next.STOP

        still_open = [
            item
            for item in self.state.open_findings
            if previous.get(item.id) != "resolved"
        ]
        new_blocking = self._new_blocking(findings)
        self.state.open_findings = still_open + new_blocking
        if any(item.route == "tester" for item in new_blocking) and self.tester_enabled:
            return _Next.TESTER
        return _Next.NEXT

    def _discard_tests(self, findings: list[dict[str, Any]]) -> None:
        self.state.discarded = []
        for item in findings:
            if item.get("route") != "discard_test":
                continue
            fid = item.get("id") or ""
            paths = self.state.protected_by_id.pop(fid, None)
            if not paths:
                continue
            note = "тест снят, чини код"
            for path in paths:
                target = self.repo / path
                target.unlink(missing_ok=True)
                self.state.protected.pop(path, None)
                self.state.discarded.append(f"{fid}: {note}")
            for found in self.state.open_findings:
                if found.id == fid:
                    found.test_paths = []
                    found.note = note

    def _stalled(self, previous: dict[str, str]) -> bool:
        # discard_test changes the tree; that is progress, not a stall.
        return (
            bool(self.state.open_findings)
            and not any(previous.get(item.id) == "resolved" for item in self.state.open_findings)
            and not self.state.discarded
        )

    def _new_blocking(self, findings: list[dict[str, Any]]) -> list[OpenFinding]:
        new_blocking: list[OpenFinding] = []
        for item in findings:
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
        return new_blocking

    async def _tester(self) -> bool:
        routed = [
            item
            for item in self.state.open_findings
            if item.route == "tester" and not item.test_paths
        ]
        if not routed or not self.tester_enabled or self.test_command is None:
            return True
        from datetime import datetime, timezone

        self._update_state(
            role="tester",
            provider=self.cfg.tester_provider,
            model=self.cfg.tester_model,
            step_started=datetime.now(timezone.utc).isoformat(),
        )
        before_tree = write_tree(self.repo)
        before_files = set(list_worktree_files(self.repo))
        prompt = tester_prompt(
            preamble=self.preamble,
            contract=self.contract,
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
            emit=self._emit,
        )
        data = self._check_reply("tester", data)
        if data is None:
            return False
        self._emit(
            "tester",
            round=self.state.round,
            role="tester",
            tests=data.get("tests") or [],
        )
        self._update_state(tester={"tests": len(data.get("tests") or [])})
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
            if not self._inside_repo(path):
                self._note_finding(fid, "tester wrote a path outside the repo")
                continue
            if not is_test_path(path):
                self._revert_path(before_tree, before_files, path)
                self._note_finding(fid, "tester wrote a non-test path")
                continue
            if not (self.repo / path).is_file():
                self._note_finding(fid, "tester reported a file it did not write")
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
                target.unlink(missing_ok=True)
                self._note_finding(fid, "tester test was green; file removed")
                continue
            red_confirmed += 1
            content = (self.repo / path).read_text() if (self.repo / path).exists() else ""
            digest = file_hashes(self.repo, [path]).get(path, "")
            self.state.protected[path] = ProtectedTest(digest, content)
            self.state.protected_by_id.setdefault(fid, [])
            if path not in self.state.protected_by_id[fid]:
                self.state.protected_by_id[fid].append(path)
            for item in self.state.open_findings:
                if item.id == fid and path not in item.test_paths:
                    item.test_paths.append(path)
        print(
            f"  tester: тестов написано {written}, red-подтверждено {red_confirmed}",
            flush=True,
        )
        return True

    def _inside_repo(self, path: str) -> bool:
        root = self.repo.resolve()
        return (root / path).resolve().is_relative_to(root)

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
        print("  нет прогресса → откат фазы", flush=True)
        return True

    def _round_file(self, name: str) -> str:
        return f"phase-{self.phase.index:02d}/round-{self.state.round:02d}/{name}"

    def _check_reply(self, role: str, data: Any) -> dict[str, Any] | None:
        """None when the provider is unavailable. An invalid reply is logged and still returned."""
        if not isinstance(data, dict) or data.get("__unavailable__"):
            self._provider_error(role, data)
            return None
        if data.get("__invalid__"):
            self._invalid(role, data)
        return data

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
        if self.state.escalated and self.cfg.escalation_provider:
            return (
                self.cfg.escalation_model,
                self.cfg.escalation_provider,
                self.cfg.escalation_effort,
            )
        return self.cfg.player_model, self.cfg.player_provider, self.cfg.player_effort

    def _rollback_with_snapshot(self, reason: str) -> str | None:
        run_id = self.ledger.run_dir.name
        ref = snapshot_mod.ref_name(run_id, self.phase.index, self.state.round)
        try:
            snapshot_mod.take_snapshot(self.repo, self.checkpoint, ref)
            if not snapshot_mod.verify(self.repo, ref):
                return None
        except Exception:
            return None
        self._update_state(snapshot_ref=ref, base=self.checkpoint)
        self._emit(
            "snapshot",
            ref=ref,
            base=self.checkpoint,
            reason=reason,
            text=f"снимок работы в `{ref}`",
        )
        self.ledger.event(
            "snapshot", ref=ref, base=self.checkpoint, reason=reason,
            round=self.state.round,
        )
        rollback(self.repo, self.checkpoint)
        return ref

    def _ship(self) -> PhaseOutcome:
        stage_and_diff(self.repo, self.checkpoint)
        secrets = secret_paths(self.repo)
        if secrets:
            self.ledger.event("stop", reason="secrets", paths=secrets)
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
        try:
            # Nothing to commit when the phase was already done and the plan
            # lives outside the repo: the phase still ships, on the current HEAD.
            if has_staged_changes(self.repo):
                sha = commit(self.repo, self.phase.title.lower()[:60])
            else:
                sha = head(self.repo)
        except GitError:
            self.plan_path.write_bytes(plan_bytes)
            raise
        self.ledger.event("ship", commit=sha, rounds=self.state.round)
        self._emit("ship", commit=sha, round=self.state.round, role="judge")
        self._update_state(commit=sha, phase_done=True)
        return PhaseOutcome(
            status="ship",
            reason="ship",
            rounds=self.state.round,
            commit_sha=sha,
            escalated=self.state.escalated,
        )

    def _halt_text(self, reason: str, ref: str | None = None) -> str:
        if reason == "user":
            return self.state.user_question or reason
        tail = f", работа в `{ref}`" if ref else ""
        texts = {
            "stuck": f"нет прогресса → откат фазы{tail}",
            "fuse": f"лимит раундов ({self.cfg.max_rounds}) исчерпан → откат фазы{tail}",
            "provider_unavailable": f"провайдер недоступен → откат фазы{tail}",
            "secrets": f"в дифе файлы-секреты → откат фазы{tail}",
        }
        return texts.get(reason, reason)

    def _write_stopped(self, reason: str, text: str) -> None:
        self._update_state(
            status=Status.STOPPED.value,
            reason=reason,
            text=text,
        )
        self._emit("stop", reason=reason, text=text)

    def _halt(self, reason: str) -> PhaseOutcome:
        ref = self._rollback_with_snapshot(reason)
        self.ledger.event("rollback", reason=reason, round=self.state.round)
        self.ledger.event("stop", reason=reason, question=self.state.user_question)
        if ref is None:
            text = "снимок не сделан, дерево не тронуто"
        else:
            text = self._halt_text(reason, ref)
        self._write_stopped(reason, text)
        return PhaseOutcome(
            status="stop",
            reason=reason,
            user_question=self.state.user_question,
            rounds=self.state.round,
            escalated=self.state.escalated,
            stop_text=text,
        )


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
        f"  judge: {data.get('verdict')}, blocking {len(blocking)}",
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
    lines = text.splitlines()
    return "\n".join(lines[-limit:])


def _stream_text(type: str, fields: dict[str, Any]) -> str:
    if type == "tool":
        return str(fields.get("text", ""))
    if type == "text":
        return str(fields.get("text", ""))
    if type == "gate":
        if fields.get("result") == "skipped":
            return "тесты пропущены (нет команды)"
        tail = str(fields.get("tail", ""))
        head = "тесты: зелёные" if fields.get("ok") else "тесты: красные"
        code = fields.get("exit_code")
        text = f"{head} (код {code})" if code is not None else head
        return f"{text}\n{tail}" if tail else text
    if type == "gate_start":
        return "тесты идут …"
    if type == "verdict":
        return str(fields.get("verdict", "") or "")
    if type == "tester":
        return f"тестов написано {len(fields.get('tests', []) or [])}"
    if type == "ship":
        return f"ship {fields.get('commit', '')}"
    if type == "stop":
        return str(fields.get("text", "") or fields.get("reason", "") or "")
    if type == "start_step":
        return f"раунд {fields.get('round', '')} начат"
    return ""


def _remove_guarded(target: Path) -> None:
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)


def _strip_unsafe_ancestors(repo: Path, rel: str) -> list[str]:
    # Never resolve outside the repo: an ancestor that is a symlink (or a
    # plain file where a dir should be) is unlinked as itself, never followed.
    stripped: list[str] = []
    parts = Path(rel).parts
    for i in range(1, len(parts)):
        anc = Path(*parts[:i])
        full = repo / anc
        if full.is_symlink() or (full.exists() and not full.is_dir()):
            full.unlink()
            stripped.append(anc.as_posix())
    return stripped


# kind, payload: ("file", bytes), ("link", os.readlink target) or ("dir", b"")
Entry = tuple[str, Any]
def _base_guarded_entries(
    repo: Path, excluded: dict[str, str]
) -> dict[str, Entry]:
    # os.walk, not git: nested repos' worktrees and gitignored paths are walked too.
    entries: dict[str, Entry] = {}
    for walk_root, dirs, files in os.walk(repo, followlinks=False):
        dirs[:] = [d for d in dirs if d not in _PRUNE_DIRS]
        try:
            rel_root = Path(walk_root).relative_to(repo)
        except ValueError:
            continue
        for name in [*dirs, *files]:
            rel = rel_root / name if rel_root.parts else Path(name)
            posix = rel.as_posix()
            if posix in excluded:
                continue
            full = repo / rel
            folded = name.casefold()
            in_config_dir = any(
                part.casefold() in _AGENT_DIRS_FOLDED for part in rel.parts[:-1]
            )
            guarded = folded in _GUARDED_FOLDED or folded in _AGENT_DIRS_FOLDED
            if full.is_symlink():
                # one entry for the link itself; os.walk never descends into it
                if in_config_dir or guarded:
                    try:
                        entries[posix] = ("link", os.readlink(full))
                    except OSError:
                        continue
            elif (in_config_dir or guarded) and full.is_file():
                entries[posix] = ("file", full.read_bytes())
    return entries


def _follow_link(
    repo: Path, root: Path, link: str, excluded: dict[str, str]
) -> str | None:
    # One lexical step along a symlink chain; None when the chain ends here.
    try:
        payload = os.readlink(repo / link)
    except OSError:
        return None
    if os.path.isabs(payload):
        target = Path(os.path.normpath(payload))
        for base in (root, repo.absolute()):
            try:
                rel = target.relative_to(base)
                break
            except ValueError:
                continue
        else:
            return None
    else:
        rel = Path(os.path.normpath((Path(link).parent / payload).as_posix()))
        if not rel.parts or rel.parts[0] == "..":
            return None
    posix = rel.as_posix()
    if posix == "." or posix in excluded:
        return None
    if any(part in _PRUNE_DIRS for part in rel.parts):
        return None
    return posix


def _chain_end(
    repo: Path, root: Path, link: str, excluded: dict[str, str]
) -> tuple[list[str], str | None]:
    # Step through the chain lexically via os.readlink; every intermediate
    # link inside the repo is returned so the caller can record it. None end
    # means a cycle or a step leaving the repo — the chain is dropped.
    steps: list[str] = []
    seen: set[str] = {link}
    cur = link
    while (repo / cur).is_symlink():
        nxt = _follow_link(repo, root, cur, excluded)
        if nxt is None or nxt in seen:
            return steps, None
        seen.add(nxt)
        steps.append(nxt)
        if not (repo / nxt).is_symlink():
            return steps, nxt
        cur = nxt
    # The start stopped being a symlink (retargeted mid-walk).
    return steps, (cur if steps else None)


def _resolve_in_repo(root: Path, rel: str) -> bool:
    # Lexically inside the repo and not the root itself; .resolve() only
    # confirms it — nothing is written through the checked path.
    if not rel or rel == "." or Path(rel).is_absolute():
        return False
    if ".." in Path(rel).parts:
        return False
    try:
        under = (root / rel).resolve()
    except OSError:
        return False
    return under != root and under.is_relative_to(root)


def _walk_target(
    repo: Path, target: str, entries: dict[str, Entry], excluded: dict[str, str]
) -> None:
    # Never walk or read a target through a link: neither the target itself
    # nor any ancestor inside the repo may be a symlink.
    probe = repo
    for part in Path(target).parts:
        probe = probe / part
        if probe.is_symlink():
            return
    full = repo / target
    if full.is_file():
        if target not in excluded and target not in entries:
            entries[target] = ("file", full.read_bytes())
    elif full.is_dir():
        if target not in excluded and target not in entries:
            entries[target] = ("dir", b"")
        for walk_root, walk_dirs, walk_files in os.walk(full, followlinks=False):
            walk_dirs[:] = [d for d in walk_dirs if d not in _PRUNE_DIRS]
            for name in [*walk_dirs, *walk_files]:
                sub = Path(walk_root) / name
                try:
                    rel = (Path(target) / sub.relative_to(full)).as_posix()
                except ValueError:
                    continue
                if rel in excluded or rel in entries:
                    continue
                if sub.is_symlink():
                    try:
                        entries[rel] = ("link", os.readlink(sub))
                    except OSError:
                        continue
                elif sub.is_file():
                    entries[rel] = ("file", sub.read_bytes())


def _snapshot_guarded(
    repo: Path,
    suite_hashes: dict[str, str] | None = None,
    fixed_targets: set[str] | None = None,
    fixed_paths: set[str] | None = None,
) -> tuple[dict[str, Entry], set[str]]:
    excluded = suite_hashes or {}
    entries = _base_guarded_entries(repo, excluded)
    if fixed_targets is not None:
        for target in sorted(fixed_targets):
            _walk_target(repo, target, entries, excluded)
        for path in fixed_paths or ():
            probe = repo
            unsafe = False
            for part in Path(path).parts[:-1]:
                probe = probe / part
                if probe.is_symlink() or (probe.exists() and not probe.is_dir()):
                    unsafe = True
                    break
            if unsafe:
                entries.pop(path, None)
                continue
            full = repo / path
            if full.is_symlink():
                try:
                    entries[path] = ("link", os.readlink(full))
                except OSError:
                    entries.pop(path, None)
            elif full.is_file():
                entries[path] = ("file", full.read_bytes())
            elif full.is_dir() and not full.is_symlink():
                entries[path] = ("dir", b"")
            else:
                entries.pop(path, None)
        return entries, set(fixed_targets)
    root = repo.resolve()
    targets: set[str] = set()
    worklist = [posix for posix, (kind, _) in entries.items() if kind == "link"]
    visited: set[str] = set()

    def _drain() -> None:
        # Record every intermediate link along each chain with its current
        # payload, and pin + walk each chain end.
        while worklist:
            posix = worklist.pop()
            if posix in visited:
                continue
            visited.add(posix)
            steps, end = _chain_end(repo, root, posix, excluded)
            for step in steps:
                if step not in entries and (repo / step).is_symlink():
                    try:
                        entries[step] = ("link", os.readlink(repo / step))
                    except OSError:
                        continue
                    worklist.append(step)
            if end is not None and end not in targets:
                if _resolve_in_repo(root, end):
                    targets.add(end)
                    _walk_target(repo, end, entries, excluded)
                    for sub, (kind, _) in list(entries.items()):
                        if kind == "link" and sub not in visited:
                            worklist.append(sub)

    _drain()
    return entries, targets
