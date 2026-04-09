"""Main orchestration loop — coordinates all G3 components."""

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from src.config import ResolvedConfig, ProviderConfig
from src.providers.registry import ProviderRegistry
from src.worktree import WorktreeManager
from src.bug_detector import BugDetector
from src.judge import JudgeRunner
from src.duel import DuelRunner
from src.state import SessionManager, SessionState
from src.learning.recorder import RunRecorder

# Learning modules are optional — pipeline works without them
try:
    from src.learning.analyzer import InsightsAnalyzer
    from src.learning.classifier import classify_task
    from src.learning.recommender import ConfigRecommender
    _LEARNING_AVAILABLE = True
except ImportError:
    InsightsAnalyzer = None  # type: ignore[assignment,misc]
    classify_task = None  # type: ignore[assignment]
    ConfigRecommender = None  # type: ignore[assignment,misc]
    _LEARNING_AVAILABLE = False


@dataclass
class OrchestratorResult:
    success: bool
    winner: str | None
    bug_score: int
    rounds_used: int
    total_duration_s: float
    run_id: str | None
    error: str | None = None


class Orchestrator:
    def __init__(
        self,
        config: ResolvedConfig,
        providers: dict[str, ProviderConfig],
        session_id: str | None = None,
    ):
        self.config = config
        self.providers = providers

        # Create session
        self.session_id = session_id or self._generate_session_id()
        self.session_dir = Path(".g3/sessions") / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # Initialize components
        self.session = SessionManager(str(self.session_dir))
        self.registry = ProviderRegistry(providers)
        # Bug fix #9: Use config.worktree_mode instead of hardcoded "auto"
        _ws_names = {config.agent_a_workspace, config.agent_b_workspace}
        self.worktree = WorktreeManager(
            session_dir=str(self.session_dir),
            source_dir=config.working_dir,
            mode=config.worktree_mode,
            workspace_base=config.working_dir,
            exclude_names=_ws_names,
        )

        # Learning components (optional — degrade gracefully if missing)
        self.recorder = RunRecorder(f"{config.working_dir}/.g3/knowledge")
        self.analyzer = InsightsAnalyzer() if _LEARNING_AVAILABLE else None
        self.recommender = ConfigRecommender() if _LEARNING_AVAILABLE else None

    def run(self) -> OrchestratorResult:
        """Execute the full duel pipeline."""
        start_time = time.time()

        try:
            # Create session state
            self.session.create(self.session_id, vars(self.config))
            self.session.transition(SessionState.PREPARING_WORKSPACES)

            # Classify task (optional — skip if learning modules unavailable)
            classification = classify_task(self.config.plan_file) if _LEARNING_AVAILABLE else None

            # Show recommendation
            if classification and self.recommender:
                rec = self.recommender.recommend(classification.type, classification.complexity)
                if rec.confidence != "none":
                    print(f"\n📊 Recommendation (confidence: {rec.confidence}):")
                    if rec.agent_a and rec.agent_b:
                        print(f"   {rec.agent_a} + {rec.agent_b}")
                    for data in rec.supporting_data:
                        print(f"   {data}")
                    for warn in rec.warnings:
                        print(f"   {warn}")

            # Read task
            task = Path(self.config.plan_file).read_text()

            # Bug fix #9: Use config values instead of hardcoded
            bug_detector = BugDetector(
                run_tests=self.config.run_tests,
                run_types=self.config.run_types,
                run_lint=self.config.run_lint,
                run_compile=self.config.run_compile,
            )
            judge = JudgeRunner(self.registry.get(self.config.judge))
            duel = DuelRunner(
                self.registry, self.worktree, bug_detector, judge,
                workspace_a_name=self.config.agent_a_workspace,
                workspace_b_name=self.config.agent_b_workspace,
            )

            # Run rounds
            round_num = 0
            best_result = None

            while round_num < self.config.max_rounds:
                round_num += 1
                print(f"\n━━━ Round {round_num}/{self.config.max_rounds} ━━━")

                self.session.transition(SessionState.AGENTS_RUNNING, {"current_round": round_num})

                # Bug fix #9: Use config.timeout_s instead of hardcoded 600
                result = duel.run_round_sync(
                    task=task,
                    agent_a_name=self.config.agent_a,
                    agent_b_name=self.config.agent_b,
                    autonomous=self.config.autonomous,
                    timeout_s=self.config.timeout_s,
                )

                self.session.transition(SessionState.BUG_DETECTION)

                # Check verdict
                decision = result.decision
                print(f"\n⚖️  Judge: {decision.action} (confidence: {decision.confidence})")
                print(f"   {decision.reason}")

                self.session.transition(SessionState.JUDGING)
                self.session.add_round_result({
                    "round": round_num,
                    "decision": decision.action,
                    "bugs_a": result.bugs_a.total,
                    "bugs_b": result.bugs_b.total,
                })

                # Handle decision
                if decision.action in ("winner_a", "winner_b"):
                    self.session.transition(SessionState.WINNER_SELECTED)
                    winner_workspace = result.workspace_a if decision.action == "winner_a" else result.workspace_b
                    winner_bugs = result.bugs_a if decision.action == "winner_a" else result.bugs_b

                    # Promote
                    self.session.transition(SessionState.PROMOTING)
                    self._promote(winner_workspace)
                    self.session.transition(SessionState.COMPLETED)

                    # Record run
                    run_id = self.recorder.record(
                        session_id=self.session_id,
                        task_file=self.config.plan_file,
                        task_type=classification.type if classification else "unknown",
                        task_complexity=classification.complexity if classification else "unknown",
                        config=vars(self.config),
                        result_a=result.result_a,
                        result_b=result.result_b,
                        bugs_a=result.bugs_a,
                        bugs_b=result.bugs_b,
                        decision=decision,
                        rounds_used=round_num,
                        total_duration_s=time.time() - start_time,
                        weights={"bug_score": 0.5, "duration": 0.1, "retry": 0.1},
                    )

                    # Rebuild insights (optional)
                    if self.analyzer:
                        self.analyzer.rebuild(self.recorder.load_all())

                    # Feedback
                    if self.config.ask_feedback:
                        self._ask_feedback(run_id)

                    duration = time.time() - start_time
                    print(f"\n✅ Complete! Winner: {decision.action}")
                    print(f"   Bug score: {winner_bugs.total}")
                    print(f"   Duration: {int(duration // 60)}m {int(duration % 60)}s")

                    return OrchestratorResult(
                        success=True,
                        winner=decision.action,
                        bug_score=winner_bugs.total,
                        rounds_used=round_num,
                        total_duration_s=duration,
                        run_id=run_id,
                    )

                elif decision.action == "retry":
                    self.session.transition(SessionState.RETRY)
                    print("   Retrying with new round...")
                    continue

                elif decision.action == "synthesize":
                    # Bug fix #6: Raise error instead of silent fallback
                    self.session.transition(SessionState.SYNTHESIZING)
                    raise NotImplementedError(
                        "Synthesis mode is not yet implemented. "
                        "Use selection='best' for now."
                    )

            # Max rounds exceeded
            self.session.transition(SessionState.FAILED)
            duration = time.time() - start_time
            return OrchestratorResult(
                success=False,
                winner=None,
                bug_score=0,
                rounds_used=round_num,
                total_duration_s=duration,
                run_id=None,
                error="Max rounds exceeded without resolution",
            )

        except Exception as e:
            # Bug fix #4: Safe transition to FAILED - go through ROUND_FAILED if needed
            # Bug fix #7: Wrap SessionState construction to avoid ValueError masking original exception
            try:
                current_state = SessionState(self.session._state.get("state", "created"))
            except ValueError:
                current_state = None
            if current_state == SessionState.AGENTS_RUNNING:
                try:
                    self.session.transition(SessionState.ROUND_FAILED)
                except Exception:
                    pass  # Already in a valid state
            try:
                self.session.transition(SessionState.FAILED)
            except Exception:
                pass  # Already terminal

            duration = time.time() - start_time
            return OrchestratorResult(
                success=False,
                winner=None,
                bug_score=0,
                rounds_used=round_num,
                total_duration_s=duration,
                run_id=None,
                error=str(e),
            )

        finally:
            self.worktree.cleanup_all()

    def resume(self) -> OrchestratorResult:
        """Resume from saved session state.

        Bug fix #5: Previously called run() which reset the session.
        Now continues from the saved state.
        """
        start_time = time.time()

        # Load saved session state
        state = self.session.load()
        current_state = state.get("state", "created")
        current_round = state.get("current_round", 0)
        rounds_data = state.get("rounds", [])

        print(f"Resuming from state: {current_state} (round {current_round})")

        # If already completed, return error
        if current_state == SessionState.COMPLETED.value:
            return OrchestratorResult(
                success=True,
                winner=state.get("final_winner"),
                bug_score=0,
                rounds_used=current_round,
                total_duration_s=0,
                run_id=state.get("run_id"),
                error="Session already completed",
            )

        # If terminal state (failed/stopped), cannot resume
        if current_state in (SessionState.FAILED.value, SessionState.STOPPED.value):
            return OrchestratorResult(
                success=False,
                winner=None,
                bug_score=0,
                rounds_used=current_round,
                total_duration_s=0,
                run_id=None,
                error=f"Session in terminal state: {current_state}",
            )

        try:
            # Restore round counter
            # Bug fix #3: Subtract 1 so the first round_num += 1 brings us back to the interrupted round
            round_num = current_round - 1

            # Read task
            task = Path(self.config.plan_file).read_text()

            # Bug fix #9: Use config values
            bug_detector = BugDetector(
                run_tests=self.config.run_tests,
                run_types=self.config.run_types,
                run_lint=self.config.run_lint,
                run_compile=self.config.run_compile,
            )
            judge = JudgeRunner(self.registry.get(self.config.judge))
            duel = DuelRunner(
                self.registry, self.worktree, bug_detector, judge,
                workspace_a_name=self.config.agent_a_workspace,
                workspace_b_name=self.config.agent_b_workspace,
            )

            # Continue from where we left off
            while round_num < self.config.max_rounds:
                round_num += 1
                print(f"\n━━━ Round {round_num}/{self.config.max_rounds} ━━━")

                self.session.transition(SessionState.AGENTS_RUNNING, {"current_round": round_num})

                result = duel.run_round_sync(
                    task=task,
                    agent_a_name=self.config.agent_a,
                    agent_b_name=self.config.agent_b,
                    autonomous=self.config.autonomous,
                    timeout_s=self.config.timeout_s,
                )

                self.session.transition(SessionState.BUG_DETECTION)

                decision = result.decision
                print(f"\n⚖️  Judge: {decision.action} (confidence: {decision.confidence})")
                print(f"   {decision.reason}")

                self.session.transition(SessionState.JUDGING)
                self.session.add_round_result({
                    "round": round_num,
                    "decision": decision.action,
                    "bugs_a": result.bugs_a.total,
                    "bugs_b": result.bugs_b.total,
                })

                if decision.action in ("winner_a", "winner_b"):
                    self.session.transition(SessionState.WINNER_SELECTED)
                    winner_workspace = result.workspace_a if decision.action == "winner_a" else result.workspace_b
                    winner_bugs = result.bugs_a if decision.action == "winner_a" else result.bugs_b

                    self.session.transition(SessionState.PROMOTING)
                    self._promote(winner_workspace)
                    self.session.transition(SessionState.COMPLETED)

                    duration = time.time() - start_time
                    print(f"\n✅ Complete! Winner: {decision.action}")
                    print(f"   Bug score: {winner_bugs.total}")

                    return OrchestratorResult(
                        success=True,
                        winner=decision.action,
                        bug_score=winner_bugs.total,
                        rounds_used=round_num,
                        total_duration_s=duration,
                        run_id=state.get("run_id"),
                    )

                elif decision.action == "retry":
                    self.session.transition(SessionState.RETRY)
                    print("   Retrying with new round...")
                    continue

                elif decision.action == "synthesize":
                    self.session.transition(SessionState.SYNTHESIZING)
                    raise NotImplementedError(
                        "Synthesis mode is not yet implemented. "
                        "Use selection='best' for now."
                    )

            self.session.transition(SessionState.FAILED)
            duration = time.time() - start_time
            return OrchestratorResult(
                success=False,
                winner=None,
                bug_score=0,
                rounds_used=round_num,
                total_duration_s=duration,
                run_id=None,
                error="Max rounds exceeded without resolution",
            )

        except Exception as e:
            # Bug fix #7: Wrap SessionState construction to avoid ValueError masking original exception
            try:
                current = SessionState(self.session._state.get("state", "created"))
            except ValueError:
                current = None
            if current == SessionState.AGENTS_RUNNING:
                try:
                    self.session.transition(SessionState.ROUND_FAILED)
                except Exception:
                    pass
            try:
                self.session.transition(SessionState.FAILED)
            except Exception:
                pass

            duration = time.time() - start_time
            return OrchestratorResult(
                success=False,
                winner=None,
                bug_score=0,
                rounds_used=round_num,
                total_duration_s=duration,
                run_id=None,
                error=str(e),
            )

        finally:
            self.worktree.cleanup_all()

    def _promote(self, winner_workspace: str):
        """Copy winner's changes to main working directory.

        Bug fix #7: Also delete files that were removed in winner workspace.
        """
        import shutil

        ws = Path(winner_workspace)
        target = Path(self.config.working_dir)
        _skip = {".git", "__pycache__", ".g3",
                 self.config.agent_a_workspace, self.config.agent_b_workspace,
                 "node_modules", ".venv", "venv"}

        # Protected files: never delete these even if absent from winner
        _protected = {
            ".env", ".env.local", ".env.production",
            ".gitignore", ".git/config", ".gitattributes",
            "config.yaml", "config.json", "config.toml",
            "user_config.json",
        }

        # Bug fix #7: Track all files in winner workspace
        winner_files: set[Path] = set()
        for item in ws.rglob("*"):
            if item.is_file():
                rel = item.relative_to(ws)
                if any(p in _skip for p in rel.parts):
                    continue
                winner_files.add(rel)

        # Delete files in target that don't exist in winner
        for item in target.rglob("*"):
            if item.is_file():
                rel = item.relative_to(target)
                if any(p in _skip for p in rel.parts):
                    continue
                if str(rel) in _protected or rel.name in _protected:
                    continue
                if rel not in winner_files:
                    try:
                        item.unlink()
                        print(f"  Removed: {rel}")
                    except (PermissionError, OSError) as exc:
                        print(f"  Warning: failed to remove {rel}: {exc}")

        # Copy/update files from winner
        for rel in winner_files:
            src = ws / rel
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

        # Remove empty directories in target that don't exist in winner
        winner_dirs = {d.relative_to(ws) for d in ws.rglob("*") if d.is_dir()}
        for item in sorted(target.rglob("*"), reverse=True):
            if item.is_dir() and not any(item.iterdir()):
                rel = item.relative_to(target)
                if rel not in winner_dirs:
                    if not any(p in _skip for p in rel.parts):
                        item.rmdir()

        print("📦 Promoted changes to main workspace")

    def _ask_feedback(self, run_id: str):
        """Ask user for feedback on the result."""
        print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  How was the result?")
        print("  [A] Approve   [R] Reject   [P] Partial   [S] Skip")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        try:
            choice = input("  Choice: ").strip().upper()
            if choice in ("A", "R", "P", "S"):
                rating = {"A": "approve", "R": "reject", "P": "partial", "S": "skip"}[choice]
                self.recorder.update_feedback(run_id, rating)
                print(f"  ✓ Feedback recorded: {rating}")
        except (EOFError, KeyboardInterrupt):
            pass

    def _generate_session_id(self) -> str:
        from datetime import datetime, timezone

        return f"sess_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
