"""HUD window (phase 8): pywebview window over run files.

No `webview` import at module level (`test_every_src_module_imports`).
The window is created with inline `html=` (no `url=`, no local server),
`frameless + on_top + focus=False + transparent + vibrancy`, and macOS
never steals focus (`no_focus_steal`, called after
`import webview.platforms.cocoa` and before `webview.start`).

Any error in `run` is written to `.tero/hud.log` and the process exits;
`tero go` never knows about the HUD.
"""

from __future__ import annotations

import json
import time
import traceback
from importlib import resources
from pathlib import Path

from src.errors import scrub_secrets
from src.fsio import append_text, atomic_write_json, read_json_dict
from src.hud.model import build_view
from src.hud.registry import is_dead
from src.hud.tail import Tail

EVENTS_CAP = 2000


def config_path() -> Path:
    return Path.home() / ".tero" / "hud.json"


def log_path() -> Path:
    return Path.home() / ".tero" / "hud.log"


def run_log_path(run_dir: str | Path) -> Path:
    """`<repo>/.tero/hud.log`: same file the HUD launcher writes to.

    `run_dir` is `<repo>/.tero/runs/<run>`; the launcher opens
    `run_dir.parent.parent / "hud.log"` for the detached HUD process.
    """
    return Path(run_dir).parent.parent / "hud.log"


COLLAPSED_H = 120
EXPANDED_H = 340
WINDOW_W = 340


def age_color(age_s: int | float | None) -> str:
    """Age bucket for the "N с назад" tail of the header."""
    if age_s is None:
        return "normal"
    try:
        age = float(age_s)
    except (TypeError, ValueError):
        return "normal"
    if age > 300:
        return "red"
    if age > 60:
        return "yellow"
    return "normal"


def no_focus_steal() -> None:
    """Keep the HUD from stealing keyboard focus (macOS, T3).

    `webview.platforms.cocoa` sets `setActivationPolicy_(0)` on import and
    `first_show` always calls `activateIgnoringOtherApps_(YES)`; showing the
    window before this runs would still grab focus. So the process opts into
    `NSApplicationActivationPolicyAccessory` (pyobjc, import inside, skipped
    without pyobjc) and the `activateIgnoringOtherApps_` call on
    `BrowserView.app` is muted.
    """
    import webview.platforms.cocoa as cocoa

    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )
    except Exception:
        pass
    try:
        cocoa.BrowserView.app.activateIgnoringOtherApps_ = lambda flag: None
    except Exception:
        pass


def _load_cfg() -> dict:
    data = read_json_dict(config_path())
    return data if isinstance(data, dict) else {}


def _save_cfg(patch: dict) -> dict:
    cfg = _load_cfg()
    cfg.update(patch)
    try:
        config_path().parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(config_path(), cfg, ensure_ascii=False)
    except Exception:
        pass
    return cfg


class Bridge:
    """`js_api` bridge: run files -> `build_view` (phase 7)."""

    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)
        self._tail = Tail(self.run_dir / "stream.jsonl")
        self._events: list[dict] = []
        self._state: dict | None = None
        self._expanded = bool(_load_cfg().get("expanded", False))

    def view(self) -> dict:
        try:
            raw = (self.run_dir / "state.json").read_text(encoding="utf-8")
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                self._state = parsed
        except Exception:
            pass
        try:
            self._events.extend(self._tail.read_new())
        except Exception:
            pass
        self._events = self._events[-EVENTS_CAP:]
        now = time.time()
        data = build_view(self._state, self._events, now, lambda s: not is_dead(s))
        data["age_color"] = age_color(data.get("last_event_age_s"))
        data["expanded"] = self._expanded
        if isinstance(self._state, dict):
            data["stop_text"] = _stop_text(self._state)
        else:
            data["stop_text"] = ""
        return data

    def toggle(self) -> bool:
        self._expanded = not self._expanded
        _save_cfg({"expanded": self._expanded})
        try:
            window = getattr(self, "_window", None)
            if window is not None:
                window.resize(WINDOW_W, EXPANDED_H if self._expanded else COLLAPSED_H)
        except Exception:
            pass
        return self._expanded

    def save_pos(self, x: int, y: int) -> bool:
        try:
            _save_cfg({"x": int(x), "y": int(y)})
        except Exception:
            return False
        return True


def _stop_text(state: dict) -> str:
    if state.get("status") != "stopped":
        return ""
    return str(state.get("text") or state.get("reason") or "стоп")


def _read_html() -> str:
    """`view.html` next to this module, wheel-safe via package resources."""
    return resources.files(__package__ or "src.hud").joinpath("view.html").read_text(
        encoding="utf-8"
    )


def run(run_dir: str | Path) -> int:
    """Open the HUD window for a run directory. Never raises."""
    try:
        import webview
        import webview.platforms.cocoa  # noqa: F401 (order: import -> no_focus_steal -> start)

        no_focus_steal()
        html = _read_html()
        bridge = Bridge(run_dir)
        cfg = _load_cfg()
        expanded = bool(cfg.get("expanded", False))
        bridge._expanded = expanded
        kwargs: dict = {
            "html": html,
            "js_api": bridge,
            "frameless": True,
            "on_top": True,
            "focus": False,
            "transparent": True,
            "vibrancy": True,
            "width": WINDOW_W,
            "height": EXPANDED_H if expanded else COLLAPSED_H,
        }
        if isinstance(cfg.get("x"), int) and isinstance(cfg.get("y"), int):
            kwargs["x"] = cfg["x"]
            kwargs["y"] = cfg["y"]
        window = webview.create_window("tero HUD", **kwargs)
        try:
            bridge._window = window
        except Exception:
            pass
        webview.start(http_server=False)
        return 0
    except Exception as exc:
        try:
            try:
                path = run_log_path(run_dir)
            except Exception:
                path = log_path()
            append_text(path, f"{time.time():.0f} view.run failed: {scrub_secrets(repr(exc))}\n")
            append_text(path, scrub_secrets(traceback.format_exc()))
        except Exception:
            pass
        return 1
