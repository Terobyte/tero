"""Auto-launch rule for the HUD (`tero go` opens it on this Mac).

Spec part A, T4a. Order: --no-hud / hud:false -> not macOS -> SSH ->
no pywebview (+ hint). The window itself (view.html) is T4b.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import Config
from src.hud.app import HUD_HINT, hud_should_launch, maybe_launch_hud

ROOT = Path(__file__).resolve().parent.parent.parent


def _flags(no_hud=False):
    return SimpleNamespace(no_hud=no_hud)


def _cfg(hud=True):
    return Config(hud=hud)


def _with_pywebview(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())


def _without_pywebview(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)


def test_launch_rule_disabled_by_flag(monkeypatch):
    _with_pywebview(monkeypatch)
    launch, reason = hud_should_launch({}, "darwin", _flags(no_hud=True), _cfg(hud=True))
    assert launch is False
    assert reason


def test_launch_rule_disabled_by_config(monkeypatch):
    _with_pywebview(monkeypatch)
    launch, reason = hud_should_launch({}, "darwin", _flags(), _cfg(hud=False))
    assert launch is False
    assert reason


def test_launch_rule_not_macos(monkeypatch):
    _with_pywebview(monkeypatch)
    launch, reason = hud_should_launch({}, "linux", _flags(), _cfg())
    assert launch is False
    assert "macOS" in reason or "darwin" in reason


def test_launch_rule_ssh_connection(monkeypatch):
    _with_pywebview(monkeypatch)
    env = {"SSH_CONNECTION": "1.2.3.4 1234 5.6.7.8 22"}
    launch, reason = hud_should_launch(env, "darwin", _flags(), _cfg())
    assert launch is False
    assert reason


def test_launch_rule_ssh_tty(monkeypatch):
    _with_pywebview(monkeypatch)
    launch, reason = hud_should_launch({"SSH_TTY": "/dev/ttys000"}, "darwin", _flags(), _cfg())
    assert launch is False
    assert reason


def test_launch_rule_no_pywebview(monkeypatch):
    _without_pywebview(monkeypatch)
    launch, reason = hud_should_launch({}, "darwin", _flags(), _cfg())
    assert launch is False
    assert "pip install tero[hud]" in reason


def test_launch_rule_launch_ok(monkeypatch):
    _with_pywebview(monkeypatch)
    launch, _ = hud_should_launch({}, "darwin", _flags(), _cfg())
    assert launch is True


def test_launch_rule_flag_checked_first(monkeypatch):
    calls = []

    def fake_find_spec(name):
        calls.append(name)
        return None

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    launch, _ = hud_should_launch({"SSH_CONNECTION": "x"}, "linux", _flags(no_hud=True), _cfg(hud=False))
    assert launch is False
    assert calls == []


def test_no_pywebview_reason_is_exact_hint(monkeypatch):
    _without_pywebview(monkeypatch)
    _, reason = hud_should_launch({}, "darwin", _flags(), _cfg())
    assert reason == HUD_HINT


def test_go_starts_hud_subprocess(monkeypatch, tmp_path):
    import subprocess

    import src.hud.app as app

    calls = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_TTY", raising=False)

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(pid=123)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    run_dir = tmp_path / ".tero" / "runs" / "x-plan"
    run_dir.mkdir(parents=True)
    maybe_launch_hud(_cfg(hud=True), run_dir)
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd[:3] == [sys.executable, "-m", "src.cli_entry"]
    assert cmd[3:] == ["hud", "--run", str(run_dir)]
    assert kwargs.get("start_new_session") is True
    assert kwargs.get("stdin") is subprocess.DEVNULL


def test_go_hud_launch_failure_does_not_raise(monkeypatch, tmp_path):
    import subprocess

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_TTY", raising=False)

    def boom(*_a, **_k):
        raise OSError("no fork")

    monkeypatch.setattr(subprocess, "Popen", boom)
    run_dir = tmp_path / ".tero" / "runs" / "x-plan"
    run_dir.mkdir(parents=True)
    maybe_launch_hud(_cfg(hud=True), run_dir)


@pytest.mark.asyncio
async def test_go_hud_launch_failure_run_still_ships(monkeypatch, tmp_path):
    """Contract: `Popen` бросает → прогон идёт (run-level, через настоящий run())."""
    import subprocess as sp

    from src.loop.run import run
    from tests.loop import fakes as scripted

    repo = scripted.init_repo(
        tmp_path, "# Demo\n\n## Add greet\n\n- [ ] greet() returns hello\n"
    )
    monkeypatch.setattr(sys, "platform", "darwin")
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: object() if name == "webview" else real_find_spec(name),
    )
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_TTY", raising=False)
    real_popen = sp.Popen

    def fake_popen(cmd, *args, **kwargs):
        if "hud" in cmd:
            raise OSError("no fork")
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(sp, "Popen", fake_popen)
    cfg = Config(
        plan_file="plan.md", working_dir=str(repo), max_rounds=8, test_timeout_s=30
    )
    agents = scripted.FakeAgents(
        repo,
        [
            scripted.player_writes(
                repo, "src/greet.py", "def greet():\n    return 'hello'\n"
            ),
            scripted.judge_returns(scripted.ship()),
        ],
    )
    outcome = await run(cfg, agents=agents)
    assert outcome.exit_code == 0, outcome.reason
    assert outcome.phases_shipped == 1


def test_go_hud_output_goes_to_tero_hud_log(monkeypatch, tmp_path):
    import subprocess

    captured = {}
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_TTY", raising=False)

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(pid=123)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    run_dir = tmp_path / ".tero" / "runs" / "x-plan"
    run_dir.mkdir(parents=True)
    maybe_launch_hud(_cfg(hud=True), run_dir)
    assert captured.get("start_new_session") is True
    assert captured.get("stdin") is subprocess.DEVNULL
    assert captured.get("stderr") is subprocess.STDOUT
    assert Path(captured["stdout"].name).resolve() == (
        tmp_path / ".tero" / "hud.log"
    ).resolve()


def test_cli_go_accepts_no_hud():
    from src.cli_entry import build_parser

    args = build_parser().parse_args(["go", "--no-hud"])
    assert args.no_hud is True


def test_cli_go_no_hud_defaults_off():
    from src.cli_entry import build_parser

    args = build_parser().parse_args(["go"])
    assert args.no_hud is False


def test_cli_hud_accepts_run():
    from src.cli_entry import build_parser

    args = build_parser().parse_args(["hud", "--run", "/tmp/x"])
    assert args.command == "hud"
    assert args.run == "/tmp/x"


def test_apply_cli_args_no_hud_disables_hud():
    from src.cli_entry import apply_cli_args

    cfg = apply_cli_args(Config(), SimpleNamespace(no_hud=True))
    assert cfg.hud is False


def test_hud_run_without_pywebview_prints_hint(tmp_path):
    if importlib.util.find_spec("webview") is not None:
        pytest.skip("pywebview is installed here")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "state.json").write_text("{}", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "src.cli_entry", "hud", "--run", str(run_dir)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0
    assert "pip install tero[hud]" in (proc.stdout + proc.stderr)
    assert HUD_HINT in (proc.stdout + proc.stderr)
