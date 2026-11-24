"""resume поднимает HUD снова: `go --no-hud` не наследуется, отказ — `resume --no-hud`."""

from types import SimpleNamespace

from src.cli_entry import build_parser
from src.loop.detach import resume_owner_argv


def test_resume_parser_has_no_hud_default_off():
    args = build_parser().parse_args(["resume"])
    assert args.no_hud is False
    assert build_parser().parse_args(["resume", "--no-hud"]).no_hud is True


def test_resume_owner_argv_passes_no_hud_only_when_asked():
    base = dict(working_dir=".", run=None, force=False, answer="", answer_from_inbox=False)
    assert "--no-hud" not in resume_owner_argv(SimpleNamespace(**base, no_hud=False))
    assert "--no-hud" in resume_owner_argv(SimpleNamespace(**base, no_hud=True))
