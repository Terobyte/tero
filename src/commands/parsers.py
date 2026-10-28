"""Подпарсеры CLI: в cli_entry.py только вызовы add_*_parsers(sub)."""

from __future__ import annotations

import argparse
from typing import Any


def add_go_parsers(sub: Any) -> None:
    go_parser = sub.add_parser("go", help="Run the player → judge loop over a plan")
    go_parser.add_argument("--plan", "-p", type=str, default=None)
    go_parser.add_argument("--working-dir", "-w", type=str, default=".")
    go_parser.add_argument("--no-menu", action="store_true", default=False)
    go_parser.add_argument("--no-hud", action="store_true", default=False)
    go_parser.add_argument("--verbose", "-v", action="store_true", default=None)
    go_parser.add_argument("--player", dest="player_spec", default=None)
    go_parser.add_argument("--judge", dest="judge_spec", default=None)
    go_parser.add_argument("--tester", dest="tester_spec", default=None)
    go_parser.add_argument("--escalation", dest="escalation_spec", default=None)
    go_parser.add_argument("--no-escalation", action="store_true", default=False)
    go_parser.add_argument("--no-tester", action="store_true", default=False)
    go_parser.add_argument("--decisions", action="store_true", default=False)
    go_parser.add_argument("--detach", action="store_true", default=False)
    go_parser.add_argument("--owner", action="store_true", default=False, help=argparse.SUPPRESS)
    go_parser.add_argument("--test-command", default=None)
    go_parser.add_argument("--test-command-scoped", default=None)
    go_parser.add_argument("--max-rounds", type=int, default=None)
    go_parser.add_argument("--escalate-at", type=int, default=None)
    go_parser.add_argument("--preset", default=None, help="пресет конфига (golden, none)")


def add_run_parsers(sub: Any) -> None:
    history_parser = sub.add_parser("history", help="Show run history")
    history_parser.add_argument("--limit", "-l", type=int, default=10)
    history_parser.add_argument("--working-dir", "-w", type=str, default=".")

    stats_parser = sub.add_parser("stats", help="Verdicts, time per role, findings over runs")
    stats_parser.add_argument("--plan", type=str, default=None, help="slug плана, напр. 08-escalation-pult")
    stats_parser.add_argument("--limit", "-l", type=int, default=None)
    stats_parser.add_argument("--working-dir", "-w", type=str, default=".")

    review_parser = sub.add_parser("review", help="Show snapshot ref and diff for a run")
    review_parser.add_argument("--working-dir", "-w", type=str, default=".")
    review_parser.add_argument("--run", type=str, default=None)

    resume_parser = sub.add_parser("resume", help="Continue a stopped run from state.json")
    resume_parser.add_argument("--working-dir", "-w", type=str, default=".")
    resume_parser.add_argument("--run", type=str, default=None)
    resume_parser.add_argument("--answer", type=str, default="")
    resume_parser.add_argument("--force", action="store_true", default=False)

    sub.add_parser("hud", help="Open the HUD window for a run").add_argument(
        "--run", type=str, default=None
    )

    compact_parser = sub.add_parser(
        "compact", help="Request a context compact for a running run"
    )
    compact_parser.add_argument("--working-dir", "-w", type=str, default=".")
    compact_parser.add_argument("--run", type=str, default=None)
    compact_parser.add_argument(
        "--role", type=str, default="player", choices=("player", "judge")
    )

    wait_parser = sub.add_parser(
        "wait", help="Sleep until the run needs the agent, ends, or looks dead"
    )
    wait_parser.add_argument("--working-dir", "-w", type=str, default=".")
    wait_parser.add_argument("--run", type=str, default=None)
    wait_parser.add_argument("--timeout", type=float, default=None)
    wait_parser.add_argument("--agent-pid", type=int, default=None)

    status_parser = sub.add_parser("status", help="Show run status")
    status_parser.add_argument("--working-dir", "-w", type=str, default=".")
    status_parser.add_argument("--run", type=str, default=None)
    status_parser.add_argument("--json", action="store_true", default=False)


def add_answer_parsers(sub: Any) -> None:
    answer_parser = sub.add_parser("answer", help="Answer the open decision question")
    answer_parser.add_argument("question_id", type=str)
    answer_parser.add_argument("action", type=str)
    answer_parser.add_argument("text", type=str, nargs="?", default="")
    answer_parser.add_argument("--why", type=str, required=True)
    answer_parser.add_argument("--human", action="store_true", default=False)
    answer_parser.add_argument("--working-dir", "-w", type=str, default=".")
    answer_parser.add_argument("--run", type=str, default=None)
    answer_parser.add_argument("--agent-pid", type=int, default=None,
                               help="pid отвечающей сессии агента")

    stop_parser = sub.add_parser("stop", help="Request the running run to stop")
    stop_parser.add_argument("--working-dir", "-w", type=str, default=".")
    stop_parser.add_argument("--run", type=str, default=None)

    sub.add_parser("guide", help="Print the agent protocol and sleep guide")
    notify_parser = sub.add_parser("notify", help="Send a notification via relay")
    notify_parser.add_argument("text", type=str)
    notify_parser.add_argument("--working-dir", "-w", type=str, default=".")
    notify_parser.add_argument("--run", type=str, default=None)


def add_relay_parsers(sub: Any) -> None:
    relay_parser = sub.add_parser("relay", help="Relay setup and status")
    relay_sub = relay_parser.add_subparsers(dest="relay_command", required=True)
    setup_parser = relay_sub.add_parser("setup", help="Generate keys, print deploy steps")
    setup_parser.add_argument("--relay-url", type=str, default=None)
    setup_parser.add_argument("--node", type=str, default=None)

    pair_parser = sub.add_parser("pair", help="Bind the phone: one-time code link")
    pair_parser.add_argument("--relay-url", type=str, default=None)
    pair_parser.add_argument("--revoke", type=str, default=None)
    pair_parser.add_argument("--reset", action="store_true", default=False)


def add_all_parsers(sub: Any) -> None:
    """Все подпарсеры одним вызовом (точка подмены тестов не требуется)."""
    add_go_parsers(sub)
    add_run_parsers(sub)
    add_answer_parsers(sub)
    add_relay_parsers(sub)
