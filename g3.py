#!/usr/bin/env python3
"""G3 Coach-Player: dual-agent orchestration CLI."""

import argparse
import sys
from pathlib import Path

# Add src to path if running from project root
sys.path.insert(0, str(Path(__file__).parent))

from src.config import resolve_config
from src.orchestrator import Orchestrator


def cmd_go(args):
    """Run a new duel session."""
    import os
    cli_args = {
        "plan_file": args.plan,
        "working_dir": os.path.abspath(getattr(args, "working_dir", ".")),
        "max_rounds": getattr(args, "max_rounds", None),
        "agent_a": getattr(args, "agent_a", None),
        "agent_b": getattr(args, "agent_b", None),
        "judge": getattr(args, "judge", None),
        "judge_2": getattr(args, "judge_2", None),
        "judge_mode": getattr(args, "judge_mode", None),
        "selection": getattr(args, "selection", None),
        "autonomous": getattr(args, "autonomous", None),
        "run_tests": getattr(args, "run_tests", None),
        "run_bug_detection": getattr(args, "run_bug_detection", None),
        "ask_feedback": getattr(args, "ask_feedback", None),
        "verbose": getattr(args, "verbose", None),
        "dry_run": getattr(args, "dry_run", False),
    }
    
    cfg, providers = resolve_config(cli_args)
    
    if cfg.dry_run:
        print("DRY RUN")
        print(f"  agents  : {cfg.agent_a} vs {cfg.agent_b}")
        print(f"  judge   : {cfg.judge}")
        print(f"  plan    : {cfg.plan_file}")
        print(f"  rounds  : {cfg.max_rounds}")
        print(f"  bugs    : {'on' if cfg.run_bug_detection else 'off'}")
        return
    
    orch = Orchestrator(cfg, providers)
    result = orch.run()

    # Bug fix #3: Exit with error code if run failed
    if not result.success:
        print(f"\n❌ Failed: {result.error}")
        sys.exit(1)


def cmd_resume(args):
    """Resume an interrupted session."""
    from src.state import SessionManager
    
    sessions_root = Path(".g3") / "sessions"
    resumable = SessionManager.find_resumable(str(sessions_root))
    
    if not resumable:
        print("No resumable sessions found.")
        return
    
    # Use specified session or most recent
    if getattr(args, "session", None):
        session_data = next(
            (s for s in resumable if s["session_id"] == args.session),
            None
        )
        if not session_data:
            print(f"Session {args.session} not found or not resumable.")
            return
    else:
        session_data = resumable[0]
    
    print(f"Resuming session: {session_data['session_id']}")
    cfg, providers = resolve_config(session_data.get("config", {}))
    orch = Orchestrator(cfg, providers, session_id=session_data["session_id"])
    result = orch.resume()

    # Bug fix #3: Exit with error code if run failed
    if not result.success:
        print(f"\n❌ Failed: {result.error}")
        sys.exit(1)


def cmd_insights(args):
    """Show learning insights."""
    from src.learning.recorder import RunRecorder
    from src.learning.analyzer import InsightsAnalyzer
    
    recorder = RunRecorder()
    runs = recorder.load_all()
    analyzer = InsightsAnalyzer()
    insights = analyzer.rebuild(runs)
    
    print(f"\n{'='*60}")
    print(f"  G3 Learning Insights")
    print(f"{'='*60}")
    print(f"\nTotal runs: {insights.get('total_runs', 0)}")
    
    pairs = insights.get("agent_pairs", {})
    if pairs:
        print("\nAgent Pairs:")
        for pair, stats in pairs.items():
            print(f"  {pair}:")
            print(f"    runs: {stats.get('runs', 0)}")
            print(f"    avg bugs: {stats.get('avg_bug_score', '?')}")
            if stats.get("approve_rate") is not None:
                print(f"    approve rate: {stats['approve_rate']:.0%}")
    
    task_types = insights.get("task_types", {})
    if task_types:
        print("\nTask Types:")
        for task_type, stats in task_types.items():
            print(f"  {task_type}:")
            print(f"    runs: {stats.get('runs', 0)}")
            print(f"    best pair: {stats.get('best_pair', '?')}")
            print(f"    avg bugs: {stats.get('avg_bug_score', '?')}")
    
    weights = insights.get("calibrated_weights", {})
    if weights:
        print("\nCalibrated Weights:")
        for key, value in weights.items():
            if key != "calibration_confidence":
                print(f"  {key}: {value}")
        print(f"  confidence: {weights.get('calibration_confidence', 'unknown')}")
    
    warnings = insights.get("timeout_insights", [])
    if warnings:
        print("\n⚠️  Warnings:")
        for w in warnings:
            print(f"  {w}")


def cmd_history(args):
    """Show session history."""
    from src.learning.recorder import RunRecorder
    
    recorder = RunRecorder()
    runs = recorder.load_all()
    
    if not runs:
        print("No runs recorded yet.")
        return
    
    limit = getattr(args, "limit", 10)
    print(f"\n{'='*60}")
    print(f"  G3 Run History (last {min(limit, len(runs))} of {len(runs)})")
    print(f"{'='*60}\n")
    
    for run in runs[-limit:]:
        verdict = run.get("judge_verdict", {})
        winner = verdict.get("winner", "?")
        action = verdict.get("action", "?")
        winner_result = run.get("results", {}).get(winner, {})
        bug_score = winner_result.get("bug_score", "?")
        
        print(f"{run.get('run_id', '?')}: {run.get('task', {}).get('type', '?'):10} "
              f"→ {winner} (bugs={bug_score}) [{action}]")


def cmd_status(args):
    """Show current session status."""
    from src.state import SessionManager
    
    sessions_root = Path(".g3") / "sessions"
    resumable = SessionManager.find_resumable(str(sessions_root))
    
    if resumable:
        latest = resumable[0]
        print(f"Active session: {latest['session_id']}")
        print(f"State: {latest.get('state', '?')}")
        print(f"Round: {latest.get('current_round', 0)}")
        print("\nRun 'tero resume' to continue.")
    else:
        print("No active sessions.")


def main():
    parser = argparse.ArgumentParser(
        prog="tero",
        description="G3 Coach-Player: dual-agent orchestration system"
    )
    sub = parser.add_subparsers(dest="command")
    
    # go command
    go = sub.add_parser("go", help="Run a new duel session")
    go.add_argument("--plan", default="requirements.md", help="Path to task/plan file (default: requirements.md)")
    go.add_argument("--working-dir", default=".", help="Working directory")
    go.add_argument("--max-rounds", type=int, help="Maximum retry rounds")
    go.add_argument("--agent-a", help="Agent A provider name")
    go.add_argument("--agent-b", help="Agent B provider name")
    go.add_argument("--judge", help="Judge provider name")
    go.add_argument("--judge-2", help="Second judge for panel mode")
    go.add_argument("--judge-mode", choices=["single", "panel"], help="Judge mode")
    go.add_argument("--selection", choices=["best", "synthesize"], help="Selection mode")
    go.add_argument("--autonomous", action="store_true", help="Skip confirmations")
    go.add_argument("--tests", action="store_true", dest="run_tests", help="Run tests")
    go.add_argument("--no-bug-detection", action="store_false", dest="run_bug_detection",
                    help="Disable bug detection")
    go.add_argument("--no-feedback", action="store_false", dest="ask_feedback",
                    help="Skip feedback prompt")
    go.add_argument("--verbose", action="store_true", help="Verbose output")
    go.add_argument("--dry-run", action="store_true", help="Show config without running")
    go.set_defaults(func=cmd_go)
    
    # resume command
    resume = sub.add_parser("resume", help="Resume interrupted session")
    resume.add_argument("--session", help="Specific session ID to resume")
    resume.set_defaults(func=cmd_resume)
    
    # insights command
    insights = sub.add_parser("insights", help="Show learning insights")
    insights.set_defaults(func=cmd_insights)
    
    # history command
    history = sub.add_parser("history", help="Show run history")
    history.add_argument("--limit", type=int, default=10, help="Number of runs to show")
    history.set_defaults(func=cmd_history)
    
    # status command
    status = sub.add_parser("status", help="Show current session status")
    status.set_defaults(func=cmd_status)
    
    args = parser.parse_args()
    
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)
    
    args.func(args)


if __name__ == "__main__":
    main()
