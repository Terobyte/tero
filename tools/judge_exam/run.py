"""Печать таблицы экзамена судей: провайдер | модель | ok | вердикт | секунды | ошибка."""

from __future__ import annotations

import argparse
import sys
from typing import Callable

DEFAULT_JUDGES: tuple[tuple[str, str], ...] = (
    ("agy", "gemini-3.8-flash-high"),
    ("claude", "claude-sonnet-5"),
    ("codex", "gpt-5.6-terra"),
    ("opencode", "opencode/mimo-v2-pro-free"),
)


def FACTORY(name: str):  # noqa: N802 - подмена в тестах через monkeypatch
    from src.providers import create_provider

    return create_provider(name)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Экзамен судей (только руками, не в гейте)")
    parser.add_argument(
        "--adapters", action="store_true", help="прогон экзамена по всем судьям"
    )
    parser.add_argument(
        "--judge",
        action="append",
        default=[],
        metavar="provider:model",
        help="только эти судьи (по умолчанию все); требует --adapters",
    )
    return parser.parse_args(argv)


def resolve_judges(args: argparse.Namespace) -> list[tuple[str, str]]:
    if not args.adapters:
        return []
    selected: list[tuple[str, str]] = []
    for spec in args.judge or []:
        if ":" in spec:
            provider, model = spec.split(":", 1)
        else:
            provider, model = spec, ""
        selected.append((provider.strip(), model.strip()))
    return selected or list(DEFAULT_JUDGES)


def main(argv: list[str] | None = None, factory: Callable[[str], object] | None = None) -> int:
    from tools.judge_exam import exam_adapter

    args = parse_args(argv)
    judges = resolve_judges(args)
    if not judges:
        print("укажите --adapters или --judge provider:model", file=sys.stderr)
        return 2
    maker = factory or FACTORY
    print("провайдер | модель | ok | вердикт | секунды | ошибка")
    for provider, model in judges:
        result = exam_adapter(provider, model, factory=maker)
        seconds = f"{result['seconds']:.1f}"
        verdict = result["verdict"] or "-"
        print(
            f"{result['provider']} | {result['model']} | "
            f"{result['ok']} | {verdict} | {seconds} | {result['error']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
