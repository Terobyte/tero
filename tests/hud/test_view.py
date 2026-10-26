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
    assert kw["vibrancy"] is True
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


def test_age_color_thresholds():
    assert hud_view.age_color(3) == "normal"
    assert hud_view.age_color(61) == "yellow"
    assert hud_view.age_color(301) == "red"


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
    assert "Фаза" in data["header"]
    assert data["doing"] == "пишу код"
    (tmp_path / "state.json").write_text("{broken", encoding="utf-8")
    data2 = bridge.view()
    assert data2["doing"] == "пишу код"
    assert data2["header"].rsplit("·", 1)[0] == data["header"].rsplit("·", 1)[0]


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
    assert bridge.view()["expanded"] is False
    bridge.save_pos(12, 34)
    assert bridge.toggle() is True
    assert hud_view.Bridge(run_dir).view()["expanded"] is True
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


def test_expanded_window_fits_detail(monkeypatch, tmp_path):
    calls, _, _ = _install_fake_webview(monkeypatch)
    cfg = tmp_path / "hud.json"
    cfg.write_text(json.dumps({"expanded": True}), encoding="utf-8")
    monkeypatch.setattr(hud_view, "config_path", lambda: cfg)
    assert hud_view.run(tmp_path) == 0
    assert calls["create_window"]["kwargs"]["height"] >= 300


def test_toggle_resizes_window(monkeypatch, tmp_path):
    cfg = tmp_path / "hud.json"
    monkeypatch.setattr(hud_view, "config_path", lambda: cfg)
    bridge = hud_view.Bridge(tmp_path)
    sizes: list = []
    bridge._window = types.SimpleNamespace(resize=lambda w, h: sizes.append((w, h)))
    bridge.toggle()
    assert sizes and sizes[-1][1] >= 300
    bridge.toggle()
    assert sizes[-1][1] <= 200


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
