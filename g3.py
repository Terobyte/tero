#!/usr/bin/env python3
import asyncio
import sys
from src.coach_player import CoachPlayerSession
from src.cli_entry import (
    build_parser,
    prepare_go_config as _shared_prepare_go_config,
    resolve_go_config as _shared_resolve_go_config,
    run_go as _shared_run_go,
    run_history as _shared_run_history,
)


def _resolve_go_config(args):
    return _shared_resolve_go_config(args)


def _prepare_go_config(args):
    return _shared_prepare_go_config(args)


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "go":
        config = _prepare_go_config(args)
        try:
            asyncio.run(run_go(args, config=config))
        except KeyboardInterrupt:
            print("\nПрервано.")
            sys.exit(130)
    elif args.command == "history":
        run_history(args)


async def run_go(args, config=None):
    return await _shared_run_go(args, config=config, session_cls=CoachPlayerSession)


def run_history(args):
    return _shared_run_history(args)


if __name__ == "__main__":
    main()
