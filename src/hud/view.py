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
import os
import threading
import time
import traceback
from importlib import resources
from pathlib import Path

from src.errors import scrub_secrets
from src.fsio import append_text, atomic_write_json, read_json_dict
from src.hud import registry, single
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


COLLAPSED_H = 76
EXPANDED_H = 340
WINDOW_W = 340
MIN_W = 280
TRAY_EVERY_S = 1.5


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
        self._open(run_dir)
        cfg = _load_cfg()
        self._expanded = bool(cfg.get("expanded", False))
        self._w = _int_or(cfg.get("w"), WINDOW_W)
        self._h = _int_or(cfg.get("h"), EXPANDED_H)
        self._visible = True
        # js poll and the tray thread both read the run files
        self._lock = threading.Lock()

    def _open(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)
        self._tail = Tail(self.run_dir / "stream.jsonl")
        self._events: list[dict] = []
        self._state: dict | None = None

    def _live(self) -> list[Path]:
        """Run dirs of all live runs in the registry, in registry order."""
        out: list[Path] = []
        for entry in registry.Registry().list_live():
            try:
                run_dir = Path(entry["state_path"]).parent.resolve()
            except Exception:
                continue
            if run_dir not in out:
                out.append(run_dir)
        return out

    def _others(self) -> list[Path]:
        """Other live runs, starting right after the shown one (so `+N` cycles)."""
        live = self._live()
        mine = self.run_dir.resolve()
        if mine in live:
            i = live.index(mine)
            live = live[i + 1:] + live[:i]
        return live

    def switch(self, run_dir: str = "") -> str:
        """Show `run_dir`, or the next live run when empty. Returns the shown dir."""
        target = Path(run_dir) if run_dir else next(iter(self._others()), None)
        if target is not None and target.resolve() != self.run_dir.resolve():
            self._open(target)
        return str(self.run_dir)

    def view(self) -> dict:
        with self._lock:
            data = self._view()
        data["visible"] = self._visible
        return data

    def _view(self) -> dict:
        target = single.take_target()
        if target is not None:
            self.switch(str(target))
            if not self._visible:
                self._set_visible(True)
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
        try:
            others = len(self._others())
        except Exception:
            others = 0
        data["plus"] = f"+{others}" if others else ""
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
                window.resize(self._w, self._h if self._expanded else COLLAPSED_H)
        except Exception:
            pass
        return self._expanded

    def save_size(self, w: int, h: int) -> bool:
        """Width always; height only when expanded (collapsed height is fixed)."""
        try:
            self._w = max(MIN_W, int(w))
            patch: dict = {"w": self._w}
            if self._expanded:
                self._h = max(COLLAPSED_H + 60, int(h))
                patch["h"] = self._h
            _save_cfg(patch)
        except Exception:
            return False
        return True

    def hide(self) -> bool:
        """Hide into the menu bar item. Returns the new visibility."""
        return self._set_visible(False)

    def toggle_visible(self) -> bool:
        return self._set_visible(not self._visible)

    def _set_visible(self, visible: bool) -> bool:
        window = getattr(self, "_window", None)
        try:
            if window is not None:
                window.show() if visible else window.hide()
            self._visible = visible
        except Exception:
            pass
        return self._visible

    def save_pos(self, x: int, y: int) -> bool:
        try:
            _save_cfg({"x": int(x), "y": int(y)})
        except Exception:
            return False
        return True


def _int_or(value, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


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
    lock = None
    try:
        lock = single.acquire_lock()
        if lock is None:
            single.write_target(run_dir)
            return 0
        _open_window(run_dir)
        return 0
    except Exception as exc:
        _log_failure(run_dir, exc)
        return 1
    finally:
        if lock is not None:
            os.close(lock)


def _open_window(run_dir: str | Path) -> None:
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
        # no vibrancy: its native slab is square and stretches with the
        # window while the html card does not; the card paints everything
        "resizable": True,
        "min_size": (MIN_W, COLLAPSED_H - 20),
        "width": bridge._w,
        "height": bridge._h if expanded else COLLAPSED_H,
    }
    if _on_screen(cfg.get("x"), cfg.get("y"), getattr(webview, "screens", None)):
        kwargs["x"] = cfg["x"]
        kwargs["y"] = cfg["y"]
    window = webview.create_window("tero HUD", **kwargs)
    try:
        bridge._window = window
    except Exception:
        pass
    webview.start(_tray_loop, (bridge, window, run_dir), http_server=False)


def _on_screen(x, y, screens) -> bool:
    """Saved top-left lands on some screen with room for the header; unknown screens pass."""
    if not (isinstance(x, int) and isinstance(y, int)) or isinstance(x, bool) or isinstance(y, bool):
        return False
    try:
        frames = [(s.x, s.y, s.width, s.height) for s in screens or []]
    except Exception:
        frames = []
    if not frames:
        return True
    return any(sx <= x <= sx + sw - 60 and sy <= y <= sy + sh - 60 for sx, sy, sw, sh in frames)


def _save_moved(bridge: Bridge, window, last: tuple) -> tuple:
    """window.x/y are the same top-left coordinates `create_window(x=, y=)` takes."""
    try:
        pos = (int(window.x), int(window.y))
    except Exception:
        return last
    if pos != last:
        bridge.save_pos(*pos)
    return pos


def _tray_loop(bridge: Bridge, window, run_dir: str | Path) -> None:
    """Menu bar item fed from Python: a hidden webview throttles its js timers."""
    from src.hud.tray import Tray

    try:
        tray = Tray(on_toggle=bridge.toggle_visible, on_quit=window.destroy)
        bridge._tray = tray
        tray.call(tray.build)
        pos: tuple = ()
        while True:
            data = bridge.view()
            tray.call(tray.update, data, bridge._visible)
            pos = _save_moved(bridge, window, pos)
            time.sleep(TRAY_EVERY_S)
    except Exception as exc:
        _log_failure(run_dir, exc)


def _log_failure(run_dir: str | Path, exc: Exception) -> None:
    try:
        try:
            path = run_log_path(run_dir)
        except Exception:
            path = log_path()
        append_text(path, f"{time.time():.0f} view.run failed: {scrub_secrets(repr(exc))}\n")
        append_text(path, scrub_secrets(traceback.format_exc()))
    except Exception:
        pass
