"""HUD auto-launch rule (spec part A, T4a).

`hud_should_launch(env, platform, flags, cfg)` checks, in order:
--no-hud / hud:false -> not macOS -> SSH -> no pywebview (+ hint).
pywebview is detected cheaply via `importlib.util.find_spec`, never imported.

The window lives in `src/hud/view.py`; `tero hud --run <dir>` without
pywebview exits with the install hint.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Mapping

HUD_HINT = "HUD: pip install tero[hud]"


def debt_line(n: int) -> str:
    return f"⚠ сдано с долгом {n}"


def hud_done_text(status: str, debt: int) -> str:
    """Текст финала HUD: done с долгом рисуется через debt_line."""
    try:
        n = int(debt or 0)
    except Exception:
        n = 0
    if status == "done" and n > 0:
        return debt_line(n)
    if status == "done":
        return "✓ сдано"
    return ""


def hud_should_launch(
    env: Mapping[str, str] | None,
    platform: str,
    flags: Any,
    cfg: Any,
) -> tuple[bool, str]:
    if getattr(flags, "no_hud", False):
        return False, "disabled by --no-hud"
    if not getattr(cfg, "hud", True):
        return False, "disabled by hud: false"
    if platform != "darwin":
        return False, f"HUD is macOS-only (platform: {platform})"
    environ = os.environ if env is None else env
    if environ.get("SSH_CONNECTION") or environ.get("SSH_TTY"):
        return False, "disabled in SSH session"
    if importlib.util.find_spec("webview") is None:
        return False, HUD_HINT
    return True, "ok"


def maybe_launch_hud(cfg: Any, run_dir: "str | Path") -> None:
    """Start `tero hud --run <run_dir>` detached when the launch rule permits.

    Best effort only: launch errors never affect the run. Uses the current
    interpreter (`sys.executable -m src.cli_entry`) so tests and installs
    resolve the same entrypoint. `pywebview` is never imported here.
    """
    import subprocess
    from types import SimpleNamespace

    try:
        launch, reason = hud_should_launch(
            None, sys.platform, SimpleNamespace(no_hud=False), cfg
        )
    except Exception:
        return
    if not launch:
        if reason == HUD_HINT:
            try:
                print(HUD_HINT, flush=True)
            except Exception:
                pass
        return
    try:
        log_path = Path(run_dir).parent.parent / "hud.log"
        log_fh = open(log_path, "a")
    except Exception:
        return
    try:
        with log_fh:
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "src.cli_entry",
                    "hud",
                    "--run",
                    str(run_dir),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    """Run `tero hud --run <run_dir>`: open the window (`src/hud/view.py`)."""
    args = list(sys.argv[1:] if argv is None else argv)
    run_dir = ""
    for i, token in enumerate(args):
        if token == "--run" and i + 1 < len(args):
            run_dir = args[i + 1]
    if not run_dir:
        print("usage: tero hud --run <run_dir>", file=sys.stderr)
        return 2
    if importlib.util.find_spec("webview") is None:
        print(HUD_HINT, file=sys.stderr)
        return 1
    from src.hud import view

    return view.run(run_dir)


if __name__ == "__main__":
    sys.exit(main())
