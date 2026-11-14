"""HUD close must stop the tray loop and let the process exit."""

import threading
import types

import pytest

from src.hud import tray as hud_tray
from src.hud import view as hud_view
from tests.hud.test_view import _install_fake_webview


def test_tray_loop_stops_after_window_closed(monkeypatch, tmp_path):
    class FakeTray:
        def __init__(self, **kwargs):
            pass

        def build(self):
            pass

        def update(self, *args):
            pass

        def set_visible(self, *args):
            pass

        def call(self, fn, *args):
            return fn(*args)

    monkeypatch.setattr(hud_tray, "Tray", FakeTray)
    monkeypatch.setattr(hud_view, "TRAY_EVERY_S", 0.01)
    bridge = types.SimpleNamespace(
        view=lambda: {}, _visible=True, toggle_visible=lambda: None, save_pos=lambda *args: None
    )
    closed = threading.Event()
    closed.set()
    window = types.SimpleNamespace(
        events=types.SimpleNamespace(closed=closed), x=0, y=0, destroy=lambda: None
    )
    thread = threading.Thread(target=hud_view._tray_loop, args=(bridge, window, tmp_path), daemon=True)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_open_window_exits_process_after_start_returns(monkeypatch, tmp_path):
    _install_fake_webview(monkeypatch)
    monkeypatch.setattr(hud_view, "no_focus_steal", lambda: None)
    monkeypatch.setattr(hud_view, "config_path", lambda: tmp_path / "hud.json")
    codes: list = []

    def spy(code):
        codes.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(hud_view.os, "_exit", spy)
    with pytest.raises(SystemExit):
        hud_view._open_window(tmp_path)
    assert codes == [0]
