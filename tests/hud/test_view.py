"""HUD window (phase 8): pywebview window over run files.

Red test on the contract: `src/hud/view.py` / `src/hud/view.html` do not
exist yet. No test opens a real window: a fake `webview` module in
`sys.modules` records `create_window` / `start` arguments.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
import time
import types
from pathlib import Path

from src.hud import app as hud_app
from src.hud import view as hud_view


def _install_fake_webview(monkeypatch):
    calls: dict = {}
    webview = types.ModuleType("webview")
    webview.__spec__ = importlib.machinery.ModuleSpec("webview", None)
    pkg = types.ModuleType("webview.platforms")
    pkg.__path__ = []  # type: ignore[attr-defined]
    cocoa = types.ModuleType("webview.platforms.cocoa")
    cocoa.__spec__ = importlib.machinery.ModuleSpec("webview.platforms.cocoa", None)
    activated: list = []

    class FakeApp:
        def activateIgnoringOtherApps_(self, flag):
            activated.append(flag)

    cocoa.BrowserView = types.SimpleNamespace(app=FakeApp())

    def create_window(*args, **kwargs):
        calls["create_window"] = {"args": args, "kwargs": kwargs}
        return types.SimpleNamespace()

    def start(*args, **kwargs):
        calls["start"] = {"args": args, "kwargs": kwargs}
        return None

    # real AppKit aborts the process without a window server (judge sandbox)
    appkit = types.ModuleType("AppKit")
    appkit.NSApplicationActivationPolicyAccessory = 1
    policies: list = []
    appkit.NSApplication = types.SimpleNamespace(
        sharedApplication=lambda: types.SimpleNamespace(setActivationPolicy_=policies.append)
    )
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    calls["policies"] = policies

    webview.create_window = create_window
    webview.start = start
    webview.platforms = pkg
    pkg.cocoa = cocoa
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setitem(sys.modules, "webview.platforms", pkg)
    monkeypatch.setitem(sys.modules, "webview.platforms.cocoa", cocoa)
    return calls, cocoa, activated


def _run_state(now: float) -> dict:
    return {
        "status": "running",
        "phase_index": 0,
        "total_phases": 1,
        "round": 1,
        "role": "player",
        "model": "m",
        "phase_started": now - 10,
        "phases": [{"title": "a", "done": False}],
    }


def test_window_uses_inline_html_no_server(monkeypatch, tmp_path):
    calls, _, _ = _install_fake_webview(monkeypatch)
    assert hud_view.run(tmp_path) == 0
    kw = calls["create_window"]["kwargs"]
    assert "html" in kw and "url" not in kw
    assert kw["frameless"] is True
    assert kw["on_top"] is True
    assert kw["focus"] is False
    assert kw["transparent"] is True
    assert kw.get("vibrancy") is not True  # see test_window_resizes_without_native_slab
    assert calls["start"]["kwargs"] == {"http_server": False}


def test_view_html_is_self_contained():
    html = Path(hud_view.__file__).with_name("view.html").read_text(encoding="utf-8")
    assert "http://" not in html
    assert "https://" not in html
    for token in ("src=", "href="):
        assert token not in html, token
    assert "@font-face" in html
    assert "data:" in html
    assert "setInterval" in html
    assert "toggle" in html


def test_bridge_view_reads_run_files(monkeypatch, tmp_path):
    monkeypatch.setattr(hud_view, "is_dead", lambda s: False)
    now = time.time()
    (tmp_path / "state.json").write_text(json.dumps(_run_state(now)), encoding="utf-8")
    ev = {
        "ts": now - 3,
        "type": "text",
        "role": "player",
        "phase": 1,
        "round": 1,
        "text": "пишу код",
    }
    (tmp_path / "stream.jsonl").write_text(json.dumps(ev) + "\n", encoding="utf-8")
    bridge = hud_view.Bridge(tmp_path)
    data = bridge.view()
    assert data["round"] == 1 and data["phase_title"]
    assert data["doing"] == "пишу код"
    (tmp_path / "state.json").write_text("{broken", encoding="utf-8")
    data2 = bridge.view()
    assert data2["doing"] == "пишу код"
    assert (data2["round"], data2["phase_title"]) == (data["round"], data["phase_title"])


def test_no_focus_steal_order(monkeypatch, tmp_path):
    calls, cocoa, activated = _install_fake_webview(monkeypatch)
    order: list = []
    real_no_focus = hud_view.no_focus_steal

    def spy_no_focus():
        order.append("no_focus_steal")
        return real_no_focus()

    monkeypatch.setattr(hud_view, "no_focus_steal", spy_no_focus)
    real_start = sys.modules["webview"].start

    def spy_start(*args, **kwargs):
        order.append("start")
        return real_start(*args, **kwargs)

    sys.modules["webview"].start = spy_start
    assert hud_view.run(tmp_path) == 0
    assert order == ["no_focus_steal", "start"]
    assert calls["policies"] == [1]
    cocoa.BrowserView.app.activateIgnoringOtherApps_(True)
    assert activated == []


def test_position_and_mode_persist(monkeypatch, tmp_path):
    _install_fake_webview(monkeypatch)
    cfg = tmp_path / "hud.json"
    monkeypatch.setattr(hud_view, "config_path", lambda: cfg)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    bridge = hud_view.Bridge(run_dir)
    assert "expanded" not in bridge.view()
    bridge.save_pos(12, 34)
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert (saved["x"], saved["y"]) == (12, 34)
    assert hud_view.run(run_dir) == 0


def test_run_error_goes_to_log(monkeypatch, tmp_path):
    _install_fake_webview(monkeypatch)
    run_dir = tmp_path / ".tero" / "runs" / "demo"
    run_dir.mkdir(parents=True)

    secret = "sk-test-0123456789abcdef0123456789abcdef"
    monkeypatch.setenv("META_MODEL_API_KEY", secret)

    def boom(*args, **kwargs):
        raise RuntimeError(f"boom {secret}")

    sys.modules["webview"].create_window = boom
    assert hud_view.run(run_dir) == 1
    log = (tmp_path / ".tero" / "hud.log").read_text(encoding="utf-8")
    assert "boom" in log
    assert secret not in log


def test_hud_main_no_webview_hint(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert hud_app.main(["--run", str(tmp_path)]) == 1
    assert "pip install tero[hud]" in capsys.readouterr().err


def test_stopped_footer_prefers_stop_reason_over_verdict(monkeypatch, tmp_path):
    monkeypatch.setattr(hud_view, "is_dead", lambda s: False)
    now = time.time()
    state = _run_state(now)
    state.update(
        {"status": "stopped", "reason": "stuck", "text": "застрял: гейт красный"}
    )
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    ev = {
        "ts": now - 60,
        "type": "verdict",
        "role": "judge",
        "phase": 1,
        "round": 1,
        "verdict": "SHIP",
        "blocking": 0,
        "first": {},
        "text": "ship",
    }
    (tmp_path / "stream.jsonl").write_text(json.dumps(ev) + "\n", encoding="utf-8")
    data = hud_view.Bridge(tmp_path).view()
    assert data.get("stop_text") and "застрял" in data["stop_text"]
    html = Path(hud_view.__file__).with_name("view.html").read_text(encoding="utf-8")
    assert "stop_text" in html
    assert html.index("stop_text") < html.index("v.verdict")


def test_phases_show_names_next_to_dots():
    html = Path(hud_view.__file__).with_name("view.html").read_text(encoding="utf-8")
    assert html.count("secs[j].title") >= 2


def test_default_window_fits_log(monkeypatch, tmp_path):
    # no collapsed mode: a fresh window is tall enough for the log, old "expanded" is ignored
    calls, _, _ = _install_fake_webview(monkeypatch)
    cfg = tmp_path / "hud.json"
    cfg.write_text(json.dumps({"expanded": False}), encoding="utf-8")
    monkeypatch.setattr(hud_view, "config_path", lambda: cfg)
    assert hud_view.run(tmp_path) == 0
    kw = calls["create_window"]["kwargs"]
    assert kw["height"] >= 300
    assert kw["min_size"][1] <= 80


def test_light_hides_into_tray_without_buttons():
    html = Path(hud_view.__file__).with_name("view.html").read_text(encoding="utf-8")
    assert 'getElementById("light").onclick' in html
    assert 'call("toggle_visible")' in html
    assert 'id="hide"' not in html and 'id="expand"' not in html


def test_run_error_goes_to_repo_hud_log(monkeypatch, tmp_path):
    _install_fake_webview(monkeypatch)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    run_dir = tmp_path / ".tero" / "runs" / "demo"
    run_dir.mkdir(parents=True)

    def boom(*args, **kwargs):
        raise RuntimeError("boom-repo")

    sys.modules["webview"].create_window = boom
    assert hud_view.run(run_dir) == 1
    assert "boom-repo" in (tmp_path / ".tero" / "hud.log").read_text(encoding="utf-8")


def test_phase_dots_have_layout_box():
    html = Path(hud_view.__file__).with_name("view.html").read_text(encoding="utf-8")
    rule = next(
        line for line in html.splitlines() if line.strip().startswith(".dot {")
    )
    assert "inline-block" in rule


def test_view_html_ships_in_package():
    import configparser

    root = Path(__file__).resolve().parent.parent.parent
    cfg = configparser.ConfigParser()
    assert cfg.read(root / "setup.cfg"), "setup.cfg with package_data"
    data = " ".join(cfg["options.package_data"].values())
    assert "*.html" in data or "view.html" in data


def test_html_loaded_via_package_resources():
    from importlib import resources

    expected = (
        resources.files("src.hud").joinpath("view.html").read_text(encoding="utf-8")
    )
    assert hud_view._read_html() == expected


def test_run_error_log_written_via_fsio(monkeypatch, tmp_path):
    import src.fsio as fsio

    assert hasattr(fsio, "append_text")
    _install_fake_webview(monkeypatch)
    run_dir = tmp_path / "runx"
    run_dir.mkdir()
    monkeypatch.setattr(hud_view, "log_path", lambda: run_dir / "hud.log")
    written: list = []
    monkeypatch.setattr(
        hud_view, "append_text", lambda p, t, **kw: written.append((str(p), t))
    )

    def boom(*args, **kwargs):
        raise RuntimeError("boom-fsio")

    sys.modules["webview"].create_window = boom
    assert hud_view.run(run_dir) == 1
    assert written and "boom-fsio" in written[0][1]


def test_window_resizes_without_native_slab(monkeypatch, tmp_path):
    # vibrancy paints a square native slab that stretches with the window
    # while the html card does not: the card must own the whole window
    calls, _, _ = _install_fake_webview(monkeypatch)
    monkeypatch.setattr(hud_view, "config_path", lambda: tmp_path / "hud.json")
    assert hud_view.run(tmp_path) == 0
    kw = calls["create_window"]["kwargs"]
    assert kw.get("vibrancy") is not True
    assert kw.get("resizable", True) is True
    assert kw["min_size"][0] >= 260
    html = Path(hud_view.__file__).with_name("view.html").read_text(encoding="utf-8")
    assert "100vh" in html


def test_window_size_persists(monkeypatch, tmp_path):
    cfg = tmp_path / "hud.json"
    monkeypatch.setattr(hud_view, "config_path", lambda: cfg)
    bridge = hud_view.Bridge(tmp_path)
    bridge.save_size(420, 150)
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert (saved["w"], saved["h"]) == (420, 150)
    bridge.save_size(100, 10)
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert (saved["w"], saved["h"]) == (hud_view.MIN_W, hud_view.MIN_H)


def test_hide_and_show_go_through_window(monkeypatch, tmp_path):
    monkeypatch.setattr(hud_view, "config_path", lambda: tmp_path / "hud.json")
    bridge = hud_view.Bridge(tmp_path)
    seen: list = []
    bridge._window = types.SimpleNamespace(hide=lambda: seen.append("hide"), show=lambda: seen.append("show"))
    assert bridge.hide() is False
    assert bridge.toggle_visible() is True
    assert seen == ["hide", "show"]


def test_tray_dot_breathes_and_menu_lines():
    from src.hud import tray

    # green breathes like the html light, other colors stay lit
    assert tray.dot_alpha("green", 0.0) == 1.0
    assert abs(tray.dot_alpha("green", tray.BREATHE_S / 2) - tray.BREATHE_MIN) < 1e-9
    assert tray.dot_alpha("yellow", tray.BREATHE_S / 2) == 1.0
    data = {"light": "yellow", "light_text": "ждёт тебя", "phase_title": "голос", "doing": "x" * 200}
    lines = tray.menu_lines(data)
    assert lines[0] == "ждёт тебя · голос"
    assert len(lines[1]) <= tray.DOING_MAX + 1
    assert tray.DOT_RGB["red"] != tray.DOT_RGB["green"]


def test_offscreen_position_is_dropped(monkeypatch, tmp_path):
    # 2026-10-28: js screenY (bottom-left origin) saved y=956 on a 956pt
    # screen; pywebview moved the window off screen and crashed in windowDidMove_
    calls, _, _ = _install_fake_webview(monkeypatch)
    sys.modules["webview"].screens = [types.SimpleNamespace(x=0, y=0, width=1470, height=956)]
    cfg = tmp_path / "hud.json"
    monkeypatch.setattr(hud_view, "config_path", lambda: cfg)
    cfg.write_text(json.dumps({"x": 0, "y": 956}), encoding="utf-8")
    assert hud_view.run(tmp_path) == 0
    assert "x" not in calls["create_window"]["kwargs"]
    cfg.write_text(json.dumps({"x": 40, "y": 500}), encoding="utf-8")
    assert hud_view.run(tmp_path) == 0
    assert (calls["create_window"]["kwargs"]["x"], calls["create_window"]["kwargs"]["y"]) == (40, 500)


def test_js_does_not_save_position():
    html = Path(hud_view.__file__).with_name("view.html").read_text(encoding="utf-8")
    assert "screenY" not in html


def test_second_hud_launch_shows_hidden_window(monkeypatch, tmp_path):
    # the menu bar item can be lost (notch, menu bar managers): `tero hud` again must bring it back
    monkeypatch.setattr(hud_view, "config_path", lambda: tmp_path / "hud.json")
    bridge = hud_view.Bridge(tmp_path)
    seen: list = []
    bridge._window = types.SimpleNamespace(hide=lambda: seen.append("hide"), show=lambda: seen.append("show"))
    bridge.hide()
    monkeypatch.setattr(hud_view.single, "take_target", lambda: tmp_path)
    assert bridge.view()["visible"] is True
    assert seen == ["hide", "show"]
