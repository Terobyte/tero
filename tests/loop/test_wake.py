"""Пробуждение агента: одна функция под замком, трамплин, лимиты (план 09, фаза 2)."""

from __future__ import annotations

import io
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest
import yaml

from src.loop import wake as wake_mod
from src.loop.human_key import sign_file
from src.loop.state import RunState

SLEEPER = "import time;print('hello-wake', flush=True);time.sleep(20)"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".tero").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TERO_WOKEN", raising=False)
    run_dir = tmp_path / "repo" / ".tero" / "runs" / "20200101-000000-demo"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({"status": "waiting_agent"}), encoding="utf-8")
    return home, run_dir


def _global_cfg(home: Path, **defaults) -> None:
    (home / ".tero" / "config.yaml").write_text(yaml.safe_dump({"defaults": defaults}), encoding="utf-8")


def _sleeper_cmd() -> str:
    return shlex.join([sys.executable, "-c", SLEEPER])


class _Fake:
    """Фейковый Popen трамплина: pid внука на stdout."""

    def __init__(self, argv, **kw):
        self.argv, self.kw, self.pid = argv, kw, 111
        self.stdout = io.BytesIO(b"4242\n")

    def wait(self, timeout=None):
        return 0


def _fake_pid(monkeypatch, calls: list):
    def factory(argv, **kw):
        calls.append((argv, kw))
        return _Fake(argv, **kw)

    monkeypatch.setattr(wake_mod, "popen_factory", factory)
    monkeypatch.setattr(wake_mod, "current_pid_started", lambda pid: 1.0)


def _decisions(run_dir: Path) -> list[dict]:
    return [d for d in (RunState.read(run_dir / "state.json") or {}).get("decisions", [])
            if d.get("kind") == "wake"]


def test_trampoline_grandchild(env, monkeypatch):
    home, run_dir = env
    _global_cfg(home, wake_command=_sleeper_cmd())
    procs: list[subprocess.Popen] = []

    def factory(argv, **kw):
        procs.append(subprocess.Popen(argv, **kw))
        return procs[-1]

    monkeypatch.setattr(wake_mod, "popen_factory", factory)
    res = wake_mod.wake(run_dir, "question", "1-1", alive=lambda *a: False)
    assert res["ok"], res
    pid = res["pid"]
    try:
        assert pid != procs[0].pid, "pid внука, не sh"
        assert not psutil.pid_exists(procs[0].pid) or procs[0].poll() is not None, "sh дождались"
        assert psutil.Process(pid).ppid() != os.getpid()
        assert pid not in [c.pid for c in psutil.Process().children(recursive=True)]
        assert os.getpgid(pid) != os.getpgid(0), "killpg группы владельца его не заденет"
        log = home / ".tero" / "wake.log"
        deadline = time.monotonic() + 5
        while "hello-wake" not in (log.read_text() if log.exists() else "") and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "hello-wake" in log.read_text()
        woken = (RunState.read(run_dir / "state.json") or {})["woken"]
        assert woken[0]["pid"] == pid and woken[0]["question_id"] == "1-1"
        assert wake_mod.global_woken_pids() == {pid}
    finally:
        os.kill(pid, signal.SIGTERM)


def test_wake_two_concurrent_one_agent(env, monkeypatch):
    home, run_dir = env
    _global_cfg(home, wake_command=_sleeper_cmd())
    launched: list[subprocess.Popen] = []

    def factory(argv, **kw):
        launched.append(subprocess.Popen(argv, **kw))
        return launched[-1]

    monkeypatch.setattr(wake_mod, "popen_factory", factory)
    barrier, out = threading.Barrier(2), []

    def go(key):
        barrier.wait()
        out.append(wake_mod.wake(run_dir, "question", key, alive=lambda *a: True))

    threads = [threading.Thread(target=go, args=(k,)) for k in ("1-1", "1-2")]
    [t.start() for t in threads]
    [t.join(20) for t in threads]
    try:
        assert len(launched) == 1
        assert sorted(r["ok"] for r in out) == [False, True]
        assert any("агент дежурит" in r["result"] for r in out)
    finally:
        for r in out:
            if r["ok"]:
                os.kill(r["pid"], signal.SIGTERM)


def test_wake_lease_taken_between_check_and_write(env, monkeypatch):
    """F28: живая аренда, записанная другим писателем после проверки on_duty, не затирается."""
    from src.loop import lease as lease_mod

    home, run_dir = env
    _global_cfg(home, wake_command="echo hi")
    killed: list[int] = []

    def factory(argv, **kw):
        lease_mod.write(run_dir, until=time.time() + 3600, pid=999, pid_started=1.0)
        return _Fake(argv, **kw)

    monkeypatch.setattr(wake_mod, "popen_factory", factory)
    monkeypatch.setattr(wake_mod, "current_pid_started", lambda pid: 1.0)
    monkeypatch.setattr(wake_mod, "_kill", killed.append)
    res = wake_mod.wake(run_dir, "question", "1-1", alive=lambda *a: True)
    assert not res["ok"], res
    assert "агент дежурит" in res["result"]
    assert int((lease_mod.read(run_dir) or {}).get("pid") or 0) == 999, "чужая аренда сохранена"
    assert killed == [4242], "новый агент погашен"


def test_wake_once_per_uuid_including_pid0(env, monkeypatch):
    home, run_dir = env
    _global_cfg(home, wake_command="echo hi")
    calls = []

    def boom(argv, **kw):
        calls.append(argv)
        raise OSError("не запустился")

    monkeypatch.setattr(wake_mod, "popen_factory", boom)
    first = wake_mod.wake(run_dir, "message", "u-1", alive=lambda *a: False, now=1000.0)
    assert not first["ok"] and len(calls) == 1
    woken = (RunState.read(run_dir / "state.json") or {})["woken"]
    assert woken[0]["pid"] == 0 and woken[0]["msg_uuid"] == "u-1"
    again = wake_mod.wake(run_dir, "message", "u-1", alive=lambda *a: False, now=5000.0)
    assert not again["ok"] and len(calls) == 1, "на тот же uuid повторного запуска нет"


def test_wake_limits_two_counters(env, monkeypatch):
    home, run_dir = env
    _global_cfg(home, wake_command="echo hi")
    calls: list = []
    _fake_pid(monkeypatch, calls)
    dead = lambda *a: False  # noqa: E731
    base = 1_000_000.0
    first = wake_mod.wake(run_dir, "manual", "m0", alive=dead, now=base)
    assert first["ok"]
    soon = wake_mod.wake(run_dir, "manual", "mx", alive=dead, now=base + 60)
    assert not soon["ok"] and "5 мин" in soon["result"]
    for i in range(1, 10):
        assert wake_mod.wake(run_dir, "manual", f"m{i}", alive=dead, now=base + 400 * i)["ok"]
    eleventh = wake_mod.wake(run_dir, "manual", "m10", alive=dead, now=base + 400 * 10)
    assert not eleventh["ok"] and "ручных" in eleventh["result"]
    auto = wake_mod.wake(run_dir, "question", "1-1", alive=dead, now=base + 400 * 11)
    assert auto["ok"], auto
    assert len(calls) == 11
    assert [d["reason"] for d in _decisions(run_dir)][-1] == "question"


def test_wake_command_from_repo_ignored(env, monkeypatch):
    home, run_dir = env
    repo = run_dir.parent.parent.parent
    marker = repo / "pwned"
    (repo / ".tero" / "config.yaml").write_text(
        yaml.safe_dump({"defaults": {"wake_command": f"sh -c 'touch {marker}'", "host": ""}}))
    st = RunState.read(run_dir / "state.json") or {}
    RunState(run_dir / "state.json").update(config={"wake_command": f"sh -c 'touch {marker}'"})
    calls: list = []
    _fake_pid(monkeypatch, calls)
    res = wake_mod.wake(run_dir, "question", "1-1", alive=lambda *a: False)
    assert not res["ok"] and not calls and not marker.exists()
    assert "host" in res["result"]
    assert st is not None


def test_default_wake_argv(env, monkeypatch):
    home, run_dir = env
    _global_cfg(home, host="claude")
    monkeypatch.setenv("CLAUDE_PID", "123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    calls: list = []
    _fake_pid(monkeypatch, calls)
    res = wake_mod.wake(run_dir, "message", "u-9", alive=lambda *a: False)
    assert res["ok"], res
    argv, kw = calls[0]
    assert argv[:2] == ["sh", "-c"] and argv[3] == str(home / ".tero" / "wake.log")
    assert argv[2] == wake_mod.TRAMPOLINE
    cmd = argv[4:]
    repo = str(run_dir.parent.parent.parent)
    task = (f"прочитай `tero inbox -w {repo} --run {run_dir}` и сделай, что просят; "
            "человеку пиши через `tero notify`")
    prompt = (f"Ты мозг tero. Выполни: tero guide --woken; "
              f"tero status --json -w {repo} --run {run_dir}; затем {task}")
    expected = ["claude", "-p", prompt, "--tools", "Bash", "--setting-sources", "",
                "--strict-mcp-config", "--permission-mode", "dontAsk", "--max-turns", "30",
                "--allowedTools",
                "Bash(tero status:*),Bash(tero guide:*),Bash(tero inbox:*),"
                "Bash(tero answer:*),Bash(tero notify:*)",
                "--disallowedTools",
                "Edit,Write,MultiEdit,NotebookEdit,WebFetch,WebSearch,Task,Agent"]
    assert cmd == expected, "argv точно по спеке п. 3"
    assert "u-9" not in " ".join(cmd), "uuid/текст сообщения не в argv"
    assert cmd[2].startswith("Ты мозг tero. Выполни: tero guide --woken;")
    assert str(run_dir) in cmd[2] and "tero inbox" in cmd[2]
    flags = cmd[3:]
    assert flags[flags.index("--tools") + 1] == "Bash"
    assert flags[flags.index("--setting-sources") + 1] == ""
    assert flags[flags.index("--permission-mode") + 1] == "dontAsk"
    assert flags[flags.index("--max-turns") + 1] == "30"
    assert "--strict-mcp-config" in flags
    assert "Bash(tero answer:*)" in flags[flags.index("--allowedTools") + 1]
    assert "Write" in flags[flags.index("--disallowedTools") + 1]
    env_ = kw["env"]
    assert env_["TERO_WOKEN"] == "1" and "CLAUDE_PID" not in env_ and "ANTHROPIC_API_KEY" not in env_
    assert kw["start_new_session"] is True and kw["cwd"] == str(run_dir.parent.parent.parent)


def test_tero_wake_cli_signed_file(env, monkeypatch):
    from src.relay.iface import FakeRelay
    from src.relay.poller import Poller

    home, run_dir = env
    _global_cfg(home, wake_command="echo hi")
    calls: list = []
    _fake_pid(monkeypatch, calls)
    monkeypatch.setattr(wake_mod, "default_alive", lambda *a: False)
    from src.hud.registry import current_pid_started
    RunState(run_dir / "state.json").update(pid=os.getpid(),
                                            pid_started=current_pid_started(os.getpid()))
    inbox = run_dir / "inbox"
    inbox.mkdir()
    # неподписанный — игнор и строка в hud.log
    (inbox / "wake.json").write_text(json.dumps({"ts": time.time()}))
    poller = Poller(run_dir, FakeRelay())
    poller.tick(0.0)
    assert not calls and not (inbox / "wake.json").exists()
    assert "wake" in (run_dir.parent.parent / "hud.log").read_text()
    # подписанный, как кладёт `tero wake` через настоящий CLI
    root = Path(__file__).resolve().parents[2]
    repo = run_dir.parent.parent.parent
    cli_env = dict(os.environ, PYTHONPATH=str(root))
    proc = subprocess.run([sys.executable, "-m", "src.cli_entry", "wake",
                           "-w", str(repo), "--run", str(run_dir)],
                          capture_output=True, text=True, cwd=str(root), env=cli_env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert json.loads((inbox / "wake.json").read_text())["sig"]
    poller.tick(100.0)
    assert len(calls) == 1
    assert _decisions(run_dir)[-1]["reason"] == "manual"
    assert sign_file  # подпись тем же ключом, что у ответов


def test_message_wakes_when_not_on_duty(env, monkeypatch):
    from src.relay.iface import Answer, FakeRelay
    from src.relay.poller import Poller

    home, run_dir = env
    _global_cfg(home, wake_command="echo hi")
    calls: list = []
    _fake_pid(monkeypatch, calls)
    monkeypatch.setattr(wake_mod, "default_alive", lambda *a: False)
    row = Answer(question_id="", action="", text="сделай", who="human", uuid="m-1", via="relay", kind="message")
    relay = FakeRelay([row])
    poller = Poller(run_dir, relay)
    poller.tick(0.0)
    assert len(calls) == 1 and (run_dir / "inbox" / "msg-m-1.json").exists()
    assert "сделай" not in " ".join(calls[0][0])
    poller.tick(100.0)
    assert len(calls) == 1, "один раз на uuid"


def test_message_waits_without_command(env, monkeypatch):
    from src.relay.iface import Answer, FakeRelay
    from src.relay.poller import Poller

    home, run_dir = env
    calls: list = []
    _fake_pid(monkeypatch, calls)
    row = Answer(question_id="", action="", text="сделай", who="human", uuid="m-2", via="relay", kind="message")
    poller = Poller(run_dir, FakeRelay([row]))
    poller.tick(0.0)
    assert not calls and (run_dir / "inbox" / "msg-m-2.json").exists(), "сообщение ждёт"


def test_woken_restricted_everywhere(env, monkeypatch):
    home, run_dir = env
    assert wake_mod.restricted_refusal("resume") == ""
    monkeypatch.setenv("TERO_WOKEN", "1")
    for cmd in ("resume", "go", "relay", "pair", "watch", "wake"):
        assert wake_mod.restricted_refusal(cmd), cmd
    assert wake_mod.restricted_refusal("answer") == ""
    monkeypatch.delenv("TERO_WOKEN")
    wake_mod.remember_global(os.getppid(), 1.0)
    assert wake_mod.restricted_refusal("resume"), "по глобальному woken.json, в любом прогоне"
    assert os.getppid() in wake_mod.global_woken_pids()


def test_guide_woken_short(capsys):
    from src.commands.guide_cmds import HOSTS_GUIDE, run_guide

    assert "acceptEdits" not in HOSTS_GUIDE
    from types import SimpleNamespace

    assert run_guide(SimpleNamespace(woken=True, working_dir=".")) == 0
    out = capsys.readouterr().out
    assert "tero answer" in out and "retry" in out and "tero resume" not in out


def test_woken_stamp_under_owner_lock(env, monkeypatch):
    home, run_dir = env
    _global_cfg(home, wake_command="echo hi")
    _fake_pid(monkeypatch, [])
    from src.loop import state as _state

    lock = _state.owner_lock(run_dir / "state.json")
    held, real = [], _state.RunState.update

    def spy(self, **fields):
        held.append(lock._is_owned())
        return real(self, **fields)

    monkeypatch.setattr(_state.RunState, "update", spy)
    assert wake_mod.wake(run_dir, "question", "1-1", alive=lambda *a: False)["ok"]
    assert held and all(held[:3]), "метка woken пишется под owner_lock"


def test_old_woken_dict_is_read(env):
    home, run_dir = env
    RunState(run_dir / "state.json").update(woken={"1-1": 4242})
    assert wake_mod.woken_pid(run_dir, "1-1") == 4242
    assert 4242 in wake_mod.run_woken_pids(run_dir)


def test_woken_answer_marked_in_any_run(env, monkeypatch):
    """Разбуженный (по общему woken.json) помечен woken и в чужом прогоне."""
    from types import SimpleNamespace

    from src.commands import inbox_cmds

    home, run_dir = env
    wake_mod.remember_global(os.getppid(), 1.0)
    monkeypatch.setattr(inbox_cmds, "_answer_wait_s", lambda: 0.0)
    args = SimpleNamespace(question_id="1-1", action="model", text="", why="x", human=False,
                           working_dir=str(run_dir.parent.parent.parent), run=str(run_dir), agent_pid=None)
    inbox_cmds.run_answer(args)
    files = list((run_dir / "inbox").glob("*.json"))
    assert files and json.loads(files[0].read_text()).get("woken") is True


def test_wake_lease_write_failure(env, monkeypatch):
    """F1: аренда не записалась — отказ, без pid в woken и без глобальной метки."""
    from src.loop import lease as lease_mod

    home, run_dir = env
    _global_cfg(home, wake_command=_sleeper_cmd())
    calls: list = []
    _fake_pid(monkeypatch, calls)
    monkeypatch.setattr(lease_mod, "write", lambda *a, **k: False)
    res = wake_mod.wake(run_dir, "question", "1-1", alive=lambda *a: False)
    assert not res["ok"] and "аренд" in res["result"]
    assert len(calls) == 1
    assert wake_mod.global_woken_pids() == set()
    assert not (run_dir / "lease").exists()
    assert [d["reason"] for d in _decisions(run_dir)][-1] == "question"


def test_take_wake_symlink_logged_and_removed(env):
    """F4: симлинк inbox/wake.json — убрать не следуя цели, записать в hud.log."""
    home, run_dir = env
    inbox = run_dir / "inbox"
    inbox.mkdir()
    target = run_dir / "evil.json"
    target.write_text(json.dumps({"ts": 1.0}))
    (inbox / "wake.json").symlink_to(target)
    assert wake_mod.take_wake_file(run_dir) == "bad"
    assert not (inbox / "wake.json").exists() and not (inbox / "wake.json").is_symlink()
    assert target.exists(), "цель симлинка не тронута"
    assert "wake" in (run_dir.parent.parent / "hud.log").read_text()


def test_wake_forged_unsigned_lease_ignores_and_oneshot(env, monkeypatch):
    """Поддельная аренда без подписи не мешает; успешный вопрос — один раз."""
    home, run_dir = env
    _global_cfg(home, wake_command="echo hi")
    calls: list = []
    _fake_pid(monkeypatch, calls)
    (run_dir / "lease").write_text(json.dumps({"until": time.time() + 600, "pid": 424242}))
    first = wake_mod.wake(run_dir, "question", "1-1", alive=lambda *a: True)
    assert first["ok"], first
    second = wake_mod.wake(run_dir, "question", "1-1", alive=lambda *a: True)
    assert not second["ok"] and len(calls) == 1, "один раз на вопрос"


def test_wake_run_dir_with_spaces(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".tero").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TERO_WOKEN", raising=False)
    (home / ".tero" / "config.yaml").write_text(
        "defaults:\n  wake_command: \"echo {run_dir}\"\n")
    run_dir = tmp_path / "a b"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(json.dumps({"status": "waiting_agent"}))
    calls: list = []
    _fake_pid(monkeypatch, calls)
    res = wake_mod.wake(run_dir, "question", "1-1", alive=lambda *a: False)
    assert res["ok"] and calls, "wake not launched"
    assert str(run_dir) in calls[0][0], "spaced run_dir stays one argv item"


def test_wake_not_launched_without_marker(env, monkeypatch):
    """F27: метка не записалась — запуска нет (RunState.update молча не пишет)."""
    from src.loop import state as _state

    home, run_dir = env
    _global_cfg(home, wake_command="echo hi")
    RunState(run_dir / "state.json").update(status="waiting_agent", phase="p1",
                                            reason="r", extra_keep="keep-me")
    before = RunState.read(run_dir / "state.json") or {}
    calls: list = []

    def _boom(argv, **kw):
        calls.append(argv)
        raise AssertionError("Popen must not run without persisted marker")

    monkeypatch.setattr(wake_mod, "popen_factory", _boom)
    monkeypatch.setattr(_state.RunState, "update", lambda self, **kw: None)
    res = wake_mod.wake(run_dir, "question", "1-1", alive=lambda *a: False)
    assert not res["ok"] and calls == []
    after = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert after == before, "без метки состояние не переписывается целиком"


def test_wake_lease_write_failure_kills_child(env, monkeypatch):
    """F8: аренда не записалась — внук-трамплин гасится SIGTERM."""
    from src.loop import lease as lease_mod

    home, run_dir = env
    _global_cfg(home, wake_command=_sleeper_cmd())
    procs: list[subprocess.Popen] = []

    def factory(argv, **kw):
        procs.append(subprocess.Popen(argv, **kw))
        return procs[-1]

    monkeypatch.setattr(wake_mod, "popen_factory", factory)
    monkeypatch.setattr(lease_mod, "write", lambda *a, **k: False)
    res = wake_mod.wake(run_dir, "question", "9-9", alive=lambda *a: False)
    assert not res["ok"] and "аренд" in res["result"]
    assert procs, "запуск был"
    assert wake_mod.woken_list(run_dir)[0].get("pid") == 0, "pid 0 остался в метке"
    deadline = time.monotonic() + 5
    leftovers = ["x"]
    while time.monotonic() < deadline:
        leftovers = [p for p in psutil.process_iter(["cmdline"])
                     if p.info["cmdline"] and any("hello-wake" in c for c in p.info["cmdline"])]
        if not leftovers:
            break
        time.sleep(0.05)
    for p in leftovers:
        try:
            p.kill()
        except Exception:
            pass
    assert not leftovers, "внук остановлен после отказа аренды"


def test_wake_pid_patch_failure_is_failure(env, monkeypatch):
    """F9: pid/pid_started не сохранились — wake сообщает об отказе."""
    from src.loop import state as _state

    home, run_dir = env
    _global_cfg(home, wake_command="echo hi")
    calls: list = []
    _fake_pid(monkeypatch, calls)
    real = _state.RunState.update

    def flaky(self, **kw):
        items = kw.get("woken")
        if isinstance(items, list) and any(
                isinstance(e, dict) and int(e.get("pid") or 0) > 0 for e in items):
            return None  # молча не пишем pid-патч
        return real(self, **kw)

    monkeypatch.setattr(_state.RunState, "update", flaky)
    res = wake_mod.wake(run_dir, "question", "1-1", alive=lambda *a: False)
    assert not res["ok"], "незаписанный pid нельзя считать успехом"
    woken = (RunState.read(run_dir / "state.json") or {})["woken"]
    assert woken[0]["pid"] == 0 and "pid_started" not in woken[0]
    assert wake_mod.global_woken_pids() == set()


def test_tero_wake_rejects_dead_owner(env, monkeypatch, capsys):
    """F15: владелец мёртв — отказ без wake.json."""
    from types import SimpleNamespace

    from src.commands.guide_cmds import run_wake

    home, run_dir = env
    RunState(run_dir / "state.json").update(status="running", pid=99999999, pid_started=1.0)
    repo = run_dir.parent.parent.parent
    rc = run_wake(SimpleNamespace(working_dir=str(repo), run=str(run_dir)))
    assert rc != 0
    assert not (run_dir / "inbox" / "wake.json").exists()


def test_autopilot_legacy_wake_names():
    """F12: старые имена пробуждения доступны через autopilot."""
    import src.loop.autopilot as ap

    assert callable(ap.maybe_wake) and callable(ap.format_wake_command)


def test_tero_wake_rejects_inactive_run(env, monkeypatch, capsys):
    """F13: остановленный прогон — отказ без wake.json."""
    from types import SimpleNamespace

    from src.commands.guide_cmds import run_wake

    home, run_dir = env
    RunState(run_dir / "state.json").update(status="stopped")
    repo = run_dir.parent.parent.parent
    rc = run_wake(SimpleNamespace(working_dir=str(repo), run=str(run_dir)))
    assert rc != 0
    assert not (run_dir / "inbox" / "wake.json").exists()
    assert "resume" in capsys.readouterr().out.lower() or "остановлен" in capsys.readouterr().out.lower()


def test_woken_restricted_cross_run(env, monkeypatch):
    """Глобально разбуженный: retry принят, model отклонён и в своём, и в чужом прогоне."""
    from types import SimpleNamespace

    from src.commands import inbox_cmds
    from src.loop.inbox import Inbox
    from src.relay.iface import FakeRelay

    home, run_dir = env
    other = run_dir.parent / "20200101-000001-other"
    other.mkdir(parents=True)
    q = {"id": "1-1", "actions": ["retry", "model", "skip", "stop"],
         "all_actions": ["retry", "model", "skip", "stop"]}
    RunState(run_dir / "state.json").update(question=q)
    (other / "state.json").write_text(json.dumps({"status": "running", "question": q}))
    wake_mod.remember_global(os.getppid(), 1.0)
    monkeypatch.setattr(inbox_cmds, "_answer_wait_s", lambda: 0.0)
    monkeypatch.setattr(inbox_cmds, "_answer_poll_s", lambda: 0.0)
    repo = str(run_dir.parent.parent.parent)

    def submit(rd: Path, action: str) -> str:
        before = {p.name for p in (rd / "inbox").glob("*.json")} if (rd / "inbox").exists() else set()
        args = SimpleNamespace(question_id="1-1", action=action, text="player claude" if action == "model" else "",
                               why="x", human=False,
                               working_dir=repo, run=str(rd), agent_pid=None)
        inbox_cmds.run_answer(args)
        new = sorted({p.name for p in (rd / "inbox").glob("*.json")} - before)
        assert new, "ответ не записан"
        payload = json.loads(((rd / "inbox") / new[0]).read_text())
        assert payload.get("woken") is True
        Inbox(rd).take("1-1", FakeRelay())
        result = (rd / "inbox" / (new[0].rsplit(".", 1)[0] + ".result")).read_text()
        return result

    for rd in (run_dir, other):
        assert submit(rd, "retry") == "принято", rd
        assert "retry|skip|stop" in submit(rd, "model"), rd


def test_tero_wake_running_without_pid_rejected(env, capsys):
    """F18: status running без pid/pid_started — отказ без wake.json."""
    from types import SimpleNamespace

    from src.commands.guide_cmds import run_wake

    home, run_dir = env
    RunState(run_dir / "state.json").update(status="running")
    st = RunState.read(run_dir / "state.json") or {}
    st.pop("pid", None)
    st.pop("pid_started", None)
    (run_dir / "state.json").write_text(json.dumps(st), encoding="utf-8")
    repo = run_dir.parent.parent.parent
    rc = run_wake(SimpleNamespace(working_dir=str(repo), run=str(run_dir)))
    assert rc != 0
    assert not (run_dir / "inbox" / "wake.json").exists()


def test_tero_wake_waiting_agent_without_pid_rejected(env, capsys):
    """F24: waiting_agent без pid/pid_started — отказ без wake.json."""
    from types import SimpleNamespace

    from src.commands.guide_cmds import run_wake

    home, run_dir = env  # {"status": "waiting_agent"}, владельца с живым pid нет
    repo = run_dir.parent.parent.parent
    rc = run_wake(SimpleNamespace(working_dir=str(repo), run=str(run_dir)))
    assert rc != 0
    assert not (run_dir / "inbox" / "wake.json").exists()


def test_wake_no_command_single_refusal(env, monkeypatch):
    """F19: повторные циклы без команды — один отказ в decisions."""
    home, run_dir = env
    (home / ".tero" / "config.yaml").write_text("defaults: {}\n", encoding="utf-8")
    first = wake_mod.wake(run_dir, "question", "1-1", alive=lambda *a: False, now=1000.0)
    assert not first["ok"]
    second = wake_mod.wake(run_dir, "question", "1-1", alive=lambda *a: False, now=1010.0)
    assert not second["ok"]
    assert len(_decisions(run_dir)) == 1, "один вопрос — один отказ"


def test_wake_bad_command_logged_and_acked(env, monkeypatch):
    """F20: кривая wake_command — отказ в decisions, без исключения."""
    from src.relay.iface import FakeRelay
    from src.relay.poller import Poller

    home, run_dir = env
    _global_cfg(home, wake_command="sh 'unterminated")
    row_calls: list = []
    _fake_pid(monkeypatch, row_calls)
    res = wake_mod.wake(run_dir, "manual", "w-bad", alive=lambda *a: False)
    assert not res["ok"], "битая команда — отказ, не исключение"
    assert not row_calls
    assert _decisions(run_dir), "отказ записан в decisions"
    relay = FakeRelay()
    from src.relay.iface import Answer as _A
    relay.answers.append(_A(question_id="", action="", text="", who="human",
                            uuid="w-bad2", via="relay", kind="wake"))
    Poller(run_dir, relay).tick(2000.0)
    assert "w-bad2" in relay.acked, "kind: wake квитирован даже с битой командой"
