"""`tero watch` (plan 09, phase 4): one watcher per machine, marks, commands, install."""

import fcntl
import json
import os
import plistlib
import sys
from pathlib import Path

import pytest

from src.hud.registry import current_pid_started
from src.loop import human_key
from src.loop.runs_index import TEST_INDEX_ENV, register_run
from src.watch.service import Watcher, acquire_watch_lock

RUN_ID = "20261030-120000-abcd"
STOPPED_USER = {
    "status": "stopped", "reason": "user", "text": "вопрос?", "snapshot_ref": "refs/x",
    "base": "abc", "config": {"x": 1},
}


class FakeRelay:
    """In-memory /commands/* with TTL: take → (no result) → lost."""

    def __init__(self, clock, ttl=600, path=None):
        self.clock, self.ttl = clock, ttl
        self.polls, self.results, self.cmds = [], [], {}
        # path: состояние команд в файле — общее с отдельным процессом вотчера
        self.path = path

    def _load(self):
        if self.path and Path(self.path).exists():
            self.cmds = json.loads(Path(self.path).read_text())

    def _save(self):
        if self.path:
            Path(self.path).write_text(json.dumps(self.cmds))

    def add(self, cid, run_id, text=""):
        self._load()
        self.cmds[cid] = {"id": cid, "run_id": run_id, "text": text, "status": "pending",
                          "at": self.clock()}
        self._save()

    def lost(self):
        self._load()
        for c in self.cmds.values():
            if c["status"] == "taken" and self.clock() - c["at"] > self.ttl:
                c["status"] = "lost"
        self._save()
        return [c["id"] for c in self.cmds.values() if c["status"] == "lost"]

    def post(self, path, body):
        self._load()
        try:
            return self._post(path, body)
        finally:
            self._save()

    def _post(self, path, body):
        if path == "/commands/poll":
            self.polls.append(body)
            return {"pending": [{"id": c["id"], "run_id": c["run_id"]}
                                for c in self.cmds.values() if c["status"] == "pending"]}
        if path == "/commands/take":
            c = self.cmds[body["id"]]
            c["status"], c["at"] = "taken", self.clock()
            return {"id": c["id"], "run_id": c["run_id"], "text": c["text"]}
        if path == "/commands/result":
            self.results.append(body)
            self.cmds[body["id"]]["status"] = "done"
            return {"ok": True}
        return None


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv(TEST_INDEX_ENV, str(tmp_path / "runs.json"))
    repo = tmp_path / "repo"
    run_dir = repo / ".tero" / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    register_run(RUN_ID, str(repo))
    return repo, run_dir


def put_state(run_dir, **fields):
    (run_dir / "state.json").write_text(json.dumps(fields))


def make(world, relay=None, **kw):
    clock = kw.pop("clock", lambda: 1000.0)
    relay = relay or FakeRelay(clock)
    sleeps = kw.pop("sleeps", [])
    return Watcher(relay.post, clock=clock, sleep=sleeps.append, machine_id="m1", **kw), relay


def test_watch_single_instance_flock(tmp_path):
    path = tmp_path / "watch.lock"
    first = acquire_watch_lock(path)
    assert first is not None
    assert acquire_watch_lock(path) is None
    first.close()
    assert acquire_watch_lock(path) is not None


def test_watch_marks_owner_alive_without_run_lock(world, monkeypatch):
    repo, run_dir = world
    state = {"status": "running", "pid": os.getpid(), "pid_started": current_pid_started()}
    put_state(run_dir, **state)
    alive = {"v": True}
    w, relay = make(world, alive=lambda pid, started: alive["v"])

    def no_flock(*a, **k):
        raise AssertionError("watch must not take locks on the run")

    monkeypatch.setattr(fcntl, "flock", no_flock)
    w.cycle()
    mark = relay.polls[0]["marks"][0]
    assert relay.polls[0]["machine_id"] == "m1"
    assert (mark["run_id"], mark["owner_alive"]) == (RUN_ID, True)
    w.cycle()
    assert relay.polls[1]["marks"] == []  # nothing changed, full list only every 5 min
    alive["v"] = False
    w.cycle()
    assert [(m["run_id"], m["owner_alive"]) for m in relay.polls[2]["marks"]] == [(RUN_ID, False)]
    assert (run_dir / "run.lock").exists() is False


def test_watch_take_crash_lost(world, tmp_path):
    """F16: падает именно вотчер между take и result: отдельный процесс берёт
    команду через Watcher.cycle у того же ретранслятора и умирает (os._exit)
    до /commands/result — именно эта команда после TTL становится lost."""
    import subprocess as _sp

    repo, run_dir = world
    put_state(run_dir, **STOPPED_USER)
    now = {"t": 1000.0}
    relay = FakeRelay(lambda: now["t"], path=tmp_path / "relay.json")
    relay.add("c1", RUN_ID)
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["RELAY_STATE"] = str(relay.path)
    script = (
        "import os\n"
        "from src.watch.service import Watcher\n"
        "from tests.test_watch import FakeRelay\n"
        "relay = FakeRelay(lambda: 1000.0, path=os.environ['RELAY_STATE'])\n"
        "def post(path, body):\n"
        "    out = relay.post(path, body)\n"
        "    if path == '/commands/take':\n"
        "        os._exit(1)  # взял — и умер до /commands/result\n"
        "    return out\n"
        "w = Watcher(post, machine_id='m1', clock=lambda: 1000.0, sleep=lambda s: None)\n"
        "w.cycle()\n"
    )
    proc = _sp.Popen([sys.executable, "-c", script], env=env, cwd=str(root))
    assert proc.wait(timeout=10) != 0  # процесс вотчера погиб между take и result
    # та же команда: взята погибшим вотчером, результата нет
    assert json.loads(relay.path.read_text())["c1"]["status"] == "taken"
    assert relay.results == [] and relay.lost() == []
    now["t"] += 601
    assert relay.lost() == ["c1"]


def test_watch_resume_argv_no_phone_text(world):
    repo, run_dir = world
    put_state(run_dir, **STOPPED_USER)
    calls, codes, sleeps = [], [9, 9, 9, 0], []

    def runner(argv, **kw):
        calls.append(argv)
        return codes.pop(0), ""

    w, relay = make(world, runner=runner, sleeps=sleeps)
    relay.add("c1", RUN_ID, text="секретный текст с телефона")
    w.cycle()
    w.join()
    assert len(calls) == 4 and sleeps == [10, 10, 10]
    argv = calls[0]
    assert isinstance(argv, list) and "секретный текст с телефона" not in " ".join(argv)
    assert argv[argv.index("-w") + 1] == str(repo)
    assert argv[argv.index("--run") + 1] == str(repo / ".tero" / "runs" / RUN_ID)
    assert "--detach" in argv and "--answer-from-inbox" in argv
    signed = json.loads((run_dir / "inbox" / "resume-answer.json").read_text())
    assert signed["text"] == "секретный текст с телефона"
    assert human_key.who_of(RUN_ID, signed) == "human"
    assert relay.results[0]["code"] == 0


def test_watch_rejects_bad_run_id(world):
    w, relay = make(world, runner=lambda *a, **k: pytest.fail("must not run"))
    relay.add("c1", "../../etc")
    w.cycle()
    w.join()
    assert relay.results[0]["code"] != 0


def test_watch_install_files(tmp_path, monkeypatch):
    from src.watch import install

    home = tmp_path / "h"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    plat = install.install(platform="darwin", run=lambda *a, **k: 0)
    plist = home / "Library" / "LaunchAgents" / "com.tero.watch.plist"
    data = plistlib.loads(plist.read_bytes())
    assert data["AbandonProcessGroup"] is True
    assert data["EnvironmentVariables"]["HOME"] == str(home)
    assert data["EnvironmentVariables"]["PATH"] == "/usr/bin:/bin"
    assert install.status(platform="darwin")["installed"] is True
    install.uninstall(platform="darwin", run=lambda *a, **k: 0)
    assert not plist.exists()
    install.install(platform="linux", run=lambda *a, **k: 0)
    unit = (home / ".config" / "systemd" / "user" / "tero-watch.service").read_text()
    assert "KillMode=process" in unit and f"HOME={home}" in unit and "PATH=/usr/bin:/bin" in unit
    install.uninstall(platform="linux", run=lambda *a, **k: 0)
    assert not (home / ".config" / "systemd" / "user" / "tero-watch.service").exists()
    assert plat in ("darwin", "linux")


def test_watch_install_checks_repos(tmp_path, monkeypatch, capsys):
    from src.watch import install

    home = tmp_path / "h"
    monkeypatch.setenv("HOME", str(home))
    idx = tmp_path / "runs.json"
    monkeypatch.setenv(TEST_INDEX_ENV, str(idx))
    missing = tmp_path / "ghost-repo"
    register_run(RUN_ID, str(missing))
    install.install(platform="darwin", run=lambda *a, **k: 0)
    printed = capsys.readouterr().out + "\n".join(install.repo_access_lines())
    assert str(missing) in printed


def test_watch_is_preloaded_and_parsed():
    from src.cli_entry import PRELOAD_COMMANDS, build_parser

    assert "watch" in PRELOAD_COMMANDS
    args = build_parser().parse_args(["watch", "--status"])
    assert args.command == "watch" and args.status is True
    assert sys.executable


def test_watch_go_detach_warns_without_watch(tmp_path, monkeypatch, capsys):
    """Контракт: `tero go --detach` без установленного сторожа печатает подсказку."""
    import types

    from src.loop import detach as detach_mod

    repo = tmp_path / "repo"
    run_dir = repo / ".tero" / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({"status": "running", "pid": 424242}))
    monkeypatch.setattr(detach_mod, "_maybe_caffeinate", lambda *a, **k: None)
    monkeypatch.setattr("src.watch.install.installed", lambda: False)

    class Owner:
        pid = 424242

        def poll(self):
            return None

    args = types.SimpleNamespace(working_dir=str(repo), detach=True)
    code = detach_mod.detach_go(args, popen=lambda *a, **k: Owner(),
                                clock=lambda: 0.0, sleep=lambda s: None)
    assert code == 0
    assert "сторожа нет: `tero watch --install`" in capsys.readouterr().out


def test_watch_outbound_redaction(world, monkeypatch):
    repo, run_dir = world
    put_state(run_dir, **{**STOPPED_USER, "text": "ключ API_KEY=sk-supersecret-abc123 вопрос?"})
    from src.commands import watch_cmd as wc
    from src.relay import client as _rc

    bodies = []

    def fake_call(self, method, path, body=None, token=""):
        bodies.append((path, body))
        if path == "/commands/take":
            return {"id": "c9", "run_id": RUN_ID, "text": "x"}
        return {"pending": []}

    monkeypatch.setattr(_rc.RelayClient, "_call", fake_call)
    monkeypatch.setattr(_rc, "_secret_list", lambda: [])
    post, mid = wc._poster()
    w, _ = make(world, runner=lambda argv, **k: (0, "out API_KEY=sk-supersecret-abc123 tail"))
    w._post = post
    w.cycle()
    w._take({"id": "c9"})
    w.join()
    joined = "\n".join(json.dumps(b, ensure_ascii=False) for _, b in bodies if isinstance(b, dict))
    assert "sk-supersecret" not in joined
    assert "[redacted]" in joined


def test_watch_resume_mark_live_owner_refused(world):
    repo, run_dir = world
    from src.hud.registry import current_pid_started

    import os

    put_state(run_dir, **{"status": "running", "pid": os.getpid(),
                          "pid_started": current_pid_started(), "config": {"x": 1},
                          "snapshot_ref": "r", "base": "b"})
    from src.watch import marks as _m

    state = json.loads((run_dir / "state.json").read_text())
    assert _m.resume_mark(state, owner_alive=True)["mode"] == "refused"


def test_watch_resume_mark_other_run_refused(world, tmp_path):
    from src.watch import marks as _m

    state = {"status": "stopped", "reason": "user", "text": "q?",
             "snapshot_ref": "r", "base": "b", "config": {"x": 1}}
    assert _m.resume_mark(state, other_run=True)["mode"] == "refused"


def test_watch_status_stopped_service(tmp_path, monkeypatch):
    from src.watch import install

    home = tmp_path / "h"
    monkeypatch.setenv("HOME", str(home))
    install.install(platform="darwin", run=lambda *a, **k: 0)
    info = install.status(platform="darwin", run=lambda *a, **k: 1)
    assert info["installed"] is True and info.get("running") is False


def test_watch_install_failing_manager(tmp_path, monkeypatch):
    from src.watch import install

    home = tmp_path / "h2"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    import pytest as _p

    with _p.raises(RuntimeError):
        install.install(platform="darwin", run=lambda *a, **k: 1)


def test_watch_linger_hint_only_when_off(monkeypatch):
    from src.watch import install

    yes = install.hints("linux", run=lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "Linger=yes\n"})())
    no = install.hints("linux", run=lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "Linger=no\n"})())
    assert yes == [] and any("enable-linger" in h for h in no)


def test_watch_201_marks_all_sent(world, monkeypatch):
    import json as _j

    from src.loop.runs_index import TEST_INDEX_ENV, register_run
    from src.watch import marks as _m

    repo = world[0]
    put_state(world[1], **{"status": "stopped", "reason": "user", "text": "q",
                           "snapshot_ref": "r", "base": "b", "config": {"x": 1}})
    for i in range(200):
        rid = f"20261030-121{i:03d}-abcd"
        d = repo / ".tero" / "runs" / rid
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(_j.dumps({"status": "stopped", "reason": "user",
                                                "text": "q", "snapshot_ref": "r", "base": "b",
                                                "config": {"x": 1}}))
        register_run(rid, str(repo))
    sent = []

    def post(path, body):
        if path == "/commands/poll":
            sent.extend(body.get("marks", []))
        return {"pending": []}

    w, _ = make(world, clock=lambda: 1000.0)
    w._post = post
    w.cycle()
    assert len(sent) == 201


def test_watch_runner_exception_reports_result(world):
    repo, run_dir = world
    put_state(run_dir, **STOPPED_USER)

    def boom(argv, **kw):
        raise TimeoutError("timed out after 120s")

    w, relay = make(world, runner=boom)
    relay.add("c1", RUN_ID)
    w.cycle()
    w.join()
    assert len(relay.results) == 1 and relay.results[0]["code"] != 0


def test_watch_runner_systemexit_reports_result(world):
    """F12: SystemExit runner-а в живом потоке — тоже result, а не молчание."""
    repo, run_dir = world
    put_state(run_dir, **STOPPED_USER)

    def boom(argv, **kw):
        raise SystemExit(1)

    w, relay = make(world, runner=boom)
    relay.add("c1", RUN_ID)
    w.cycle()
    w.join()
    assert len(relay.results) == 1 and relay.results[0]["code"] != 0


def test_watch_linger_hint_via_default_run(monkeypatch):
    """F11: штатный вызов --install видит stdout loginctl (Linger=yes → тихо)."""
    import subprocess as _sp

    from src.watch import install as _inst

    class R:
        def __init__(self, out):
            self.returncode = 0
            self.stdout = out

    monkeypatch.setattr(_sp, "run", lambda *a, **k: R("Linger=yes\n"))
    assert _inst.hints("linux") == []
    monkeypatch.setattr(_sp, "run", lambda *a, **k: R("Linger=no\n"))
    assert any("enable-linger" in h for h in _inst.hints("linux"))


def test_watch_resume_busy_stops_after_three_retries(world):
    """F18: постоянный код 9 — 4 запуска и 3 паузы, потом сдаёмся с кодом 9."""
    repo, run_dir = world
    put_state(run_dir, **STOPPED_USER)
    calls, sleeps = [], []

    def runner(argv, **kw):
        calls.append(argv)
        return 9, "занят"

    w, relay = make(world, runner=runner, sleeps=sleeps)
    relay.add("c1", RUN_ID, text="подскажи")
    w.cycle()
    w.join()
    assert len(calls) == 4 and sleeps == [10, 10, 10]
    assert relay.results[0]["code"] == 9


def test_watch_marks_dead_owner_other_run_not_blocking(world):
    """F13: другой прогон идёт, только если его pid/pid_started живы: второй
    `running` с мёртвым владельцем не даёт первому `refused`."""
    from src.loop.runs_index import register_run
    from src.watch import marks as _m

    repo, run_dir = world
    put_state(run_dir, **STOPPED_USER)
    other_id = "20261030-130000-zzzz"
    other_dir = repo / ".tero" / "runs" / other_id
    other_dir.mkdir(parents=True)
    (other_dir / "state.json").write_text(json.dumps({"status": "running",
                                                      "pid": 99999999, "pid_started": 1.0}))
    register_run(other_id, str(repo))
    marks = _m.collect(1000.0, alive=lambda pid, started: False)
    by_id = {m["run_id"]: m for m in marks}
    assert by_id[other_id]["owner_alive"] is False
    assert by_id[RUN_ID]["resume"]["mode"] != "refused"


def test_watch_install_atomic_no_partial(tmp_path, monkeypatch):
    """F14: установка пишет через src/fsio.py — прерывание не оставляет
    частичный plist/unit."""
    from pathlib import Path as _P

    from src.watch import install

    home = tmp_path / "h4"
    monkeypatch.setenv("HOME", str(home))

    def boom(self, target):
        raise RuntimeError("interrupted mid-write")

    monkeypatch.setattr(_P, "replace", boom)
    with pytest.raises(RuntimeError):
        install.install(platform="darwin", run=lambda *a, **k: 0)
    plist = home / "Library" / "LaunchAgents" / "com.tero.watch.plist"
    assert not plist.exists()
    agents = home / "Library" / "LaunchAgents"
    assert [p for p in agents.glob("*.tmp")] == [] if agents.exists() else True
    with pytest.raises(RuntimeError):
        install.install(platform="linux", run=lambda *a, **k: 0)
    unit = home / ".config" / "systemd" / "user" / "tero-watch.service"
    assert not unit.exists()
    user_dir = home / ".config" / "systemd" / "user"
    assert [p for p in user_dir.glob("*.tmp")] == [] if user_dir.exists() else True
