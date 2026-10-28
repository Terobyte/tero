"""Tester step: red tests for findings routed to the tester."""

from __future__ import annotations

from typing import Callable

from src.loop.guards import suite as suite_guard
from src.loop.gates import run_files
from src.loop.git import file_hashes, list_worktree_files, stage_and_diff, write_tree
from src.loop.guards.ignored import restore_ignored, snapshot_ignored
from src.loop.phase_ctx import PhaseContext, ProtectedTest, _dump
from src.loop.prompts import tester_prompt
from src.loop.schemas import ValidateCtx
from src.loop.guards.suite import is_test_path


class TesterStep:
    def __init__(self, ctx: PhaseContext, guard_plan: Callable[[str], None]) -> None:
        self.ctx = ctx
        self.guard_plan = guard_plan

    async def _tester(self) -> bool | None:
        """True — tests written or tester skipped/failed over to the player;
        False — unused; None — auth/quota, caller makes halt."""
        routed = [
            item
            for item in self.ctx.st.open_findings
            if item.route == "tester" and not item.test_paths
        ]
        if not routed or not self.ctx.tester_enabled or self.ctx.test_command is None:
            return True
        from datetime import datetime, timezone

        self.ctx.update_state(
            role="tester",
            provider=self.ctx.cfg.tester_provider,
            model=self.ctx.cfg.tester_model,
            step_started=datetime.now(timezone.utc).isoformat(),
        )
        before_tree = write_tree(self.ctx.repo)
        before_files = set(list_worktree_files(self.ctx.repo))
        before_ignored = snapshot_ignored(self.ctx.repo)
        prompt = tester_prompt(
            preamble=self.ctx.preamble,
            contract=self.ctx.contract,
            diff=stage_and_diff(self.ctx.repo, self.ctx.checkpoint),
            findings=[item.as_dict() for item in routed],
        )
        try:
            data = await self.ctx.agents.run_role(
                role="tester",
                provider=self.ctx.cfg.tester_provider,
                model=self.ctx.cfg.tester_model,
                effort=self.ctx.cfg.tester_effort,
                prompt=prompt,
                timeout=self.ctx.cfg.tester_timeout_s,
                ctx=ValidateCtx(),
                emit=self.ctx.emit,
            )
        finally:
            # also when the tester call raises: its edits to ignored files never stay
            for rel in restore_ignored(self.ctx.repo, before_ignored):
                self.ctx.ledger.event("ignored_violation", path=rel, round=self.ctx.st.round)
                print(f"  gitignored-файл изменён тестером: {rel} — откат", flush=True)
        self.guard_plan("tester")
        data = self.ctx.check_reply("tester", data, self.ctx.cfg.tester_provider)
        if data is None:
            if self.ctx.st.last_provider_kind == "auth":
                self.ctx.st.stop_reason = "provider_auth"
                return None
            if self.ctx.st.last_provider_kind == "quota":
                self.ctx.st.stop_reason = "provider_quota"
                return None
            # Transient failure (timeout, unavailable): don't stop the run.
            # The player repairs without red tests; the tester stays off for
            # the rest of the phase so the next rounds don't burn a call on it.
            kind = self.ctx.st.last_provider_kind or "provider_unavailable"
            self.ctx.ledger.event("tester_fallback", error_kind=kind)
            print(f"  tester упал ({kind}) — дальше правит player", flush=True)
            self.ctx.tester_enabled = False
            return True
        self.ctx.emit(
            "tester",
            round=self.ctx.st.round,
            role="tester",
            tests=data.get("tests") or [],
        )
        self.ctx.update_state(tester={"tests": len(data.get("tests") or [])})
        self.ctx.ledger.event("tester", round=self.ctx.st.round)
        self.ctx.ledger.artifact(self.ctx.round_file("tester.json"), _dump(data))
        suite_guard.revert_non_test(self.ctx.repo, before_tree, before_files)
        written = 0
        red_confirmed = 0
        for test in data.get("tests") or []:
            path = test.get("path", "")
            fid = test.get("finding_id", "")
            if not path:
                continue
            written += 1
            if not suite_guard.inside_repo(self.ctx.repo, path):
                self._note_finding(fid, "tester wrote a path outside the repo")
                continue
            if not is_test_path(path):
                suite_guard.revert_path(self.ctx.repo, before_tree, before_files, path)
                self._note_finding(fid, "tester wrote a non-test path")
                continue
            if not (self.ctx.repo / path).is_file():
                self._note_finding(fid, "tester reported a file it did not write")
                continue
            result = run_files(
                self.ctx.test_command,
                [path],
                repo=self.ctx.repo,
                timeout_s=self.ctx.cfg.test_timeout_s,
            )
            self.ctx.ledger.event("red_check", path=path, ok=result.ok)
            if result.ok:
                target = self.ctx.repo / path
                target.unlink(missing_ok=True)
                self._note_finding(fid, "tester test was green; file removed")
                continue
            red_confirmed += 1
            content = (self.ctx.repo / path).read_text() if (self.ctx.repo / path).exists() else ""
            digest = file_hashes(self.ctx.repo, [path]).get(path, "")
            self.ctx.st.protected[path] = ProtectedTest(digest, content)
            self.ctx.st.protected_by_id.setdefault(fid, [])
            if path not in self.ctx.st.protected_by_id[fid]:
                self.ctx.st.protected_by_id[fid].append(path)
            for item in self.ctx.st.open_findings:
                if item.id == fid and path not in item.test_paths:
                    item.test_paths.append(path)
        print(
            f"  tester: тестов написано {written}, red-подтверждено {red_confirmed}",
            flush=True,
        )
        return True

    def _note_finding(self, fid: str, note: str) -> None:
        for item in self.ctx.st.open_findings:
            if item.id == fid:
                item.note = note
                item.route = "player"
