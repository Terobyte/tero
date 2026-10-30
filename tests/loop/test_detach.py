"""`tero go --detach`: parent launches the owner, prints protocol, returns."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

from src.config import Config, apply_cli_args
from src.cli_entry import build_parser, prepare_go_config
from src.hud import registry as registry_mod
from src.loop import detach as detach_mod


def _go_args(repo: Path, **overrides):
    base = {
        "plan": None, "working_dir": str(repo), "no_menu": False,
        "no_hud": False, "verbose": None,
        "player_spec": None, "judge_spec": None, "tester_spec": None,
        "escalation_spec": None, "no_escalation": False,
        "test_command": None, "test_command_scoped": None,
        "max_rounds": None,
        "decisions": False, "detach": True, "owner": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _owner_stub(path: Path, *, status: str = "running") -> None:
    path.write_text(textwrap.dedent(f"""\
        import json, os, sys, time
        import psutil
        run_dir = sys.argv[1]
        os.makedirs(run_dir, exist_ok=True)
        with open(os.path.join(run_dir, "state.json"), "w") as fh:
            json.dump({{"status": {status!r}, "pid": os.getpid(),
                        "pid_started": psutil.Process().create_time()}}, fh)
        time.sleep(4)
        """))


EXPECTED_PROTOCOL = (
    "1. `tero go --detach`, потом `tero wait --messages` фоновой командой — спать.\n"
    "2. `wait` вышел: `0` → отчитаться; `4` → отчитаться и перечислить долг; "
    "`3` → `tero status --json`; если открытого вопроса уже нет (ответил человек) — "
    "снова `tero wait --messages`; иначе решить, `tero answer <id> … --why …`, снова `tero wait --messages`; "
    "`5` → `tero resume`, снова `tero wait --messages`; `2` → отчитаться; `6` → снова `tero wait --messages`; "
    "`7` → текст сообщения уже в выводе; на вопрос отвечай только если статус `waiting_agent`, "
    "снова `tero wait --messages`; `8` → ты сирота по мнению tero: если ты жив и это читаешь — `tero status`, затем снова "
    "`tero wait --messages`; `9` от `tero resume` → владелец занят, подождать 10 с и повторить.\n"
    "3. Если сессия закрылась, прогон живёт; новый агент подхватывает его: "
    "`tero status` + `tero wait --messages`."
)


class _Proc:
    def __init__(self, pid, code=None):
        self.pid = pid
        self._code = code
        self.args = None
        self.kwargs = None

    def poll(self):
        return self._code


def test_detach_prints_protocol_and_returns(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = repo / ".tero" / "runs" / "20200101-000000-demo"
    stub = tmp_path / "owner_stub.py"
    _owner_stub(stub)

    monkeypatch.setenv("PATH", str(tmp_path) + ":" + os.environ.get("PATH", ""))

    real_popen = subprocess.Popen

    procs: list = []

    def popen(argv, **kwargs):
        if isinstance(argv, list) and argv[:2] == [sys.executable, "-m"]:
            proc = real_popen(
                [sys.executable, str(stub), str(run_dir)],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            procs.append(proc)
            return proc
        return real_popen(argv, **kwargs)

    args = _go_args(repo)
    try:
        code = detach_mod.detach_go(args, popen=popen, poll_s=0.01, ready_s=10)
        out = capsys.readouterr().out
    finally:
        for proc in procs:
            try:
                proc.kill()
            except Exception:
                pass
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
    assert code == 0
    assert "20200101-000000-demo" in out
    assert "state.json" in out
    assert detach_mod.AGENT_PROTOCOL in out
    assert detach_mod.AGENT_PROTOCOL == EXPECTED_PROTOCOL
    assert "AGENT_PROTOCOL" not in out  # the text itself, not the name


def test_detach_hud_does_not_flash_dead(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = repo / ".tero" / "runs" / "20200101-000000-demo"
    stub = tmp_path / "owner_stub.py"
    _owner_stub(stub)

    real_popen = subprocess.Popen

    def popen(argv, **kwargs):
        if isinstance(argv, list) and argv[:2] == [sys.executable, "-m"]:
            return real_popen(
                [sys.executable, str(stub), str(run_dir)],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return real_popen(argv, **kwargs)

    owner = None
    orig_popen = popen

    def capture(argv, **kwargs):
        nonlocal owner
        proc = orig_popen(argv, **kwargs)
        if isinstance(argv, list) and argv[:2] == [sys.executable, "-m"]:
            owner = proc
        return proc

    args = _go_args(repo)
    try:
        code = detach_mod.detach_go(args, popen=capture, poll_s=0.01, ready_s=10)
        assert code == 0
        state = json.loads((run_dir / "state.json").read_text())
        assert state["pid"] == owner.pid
        # Stub writes the real owner create_time, so the pristine state file
        # must already read as alive — no patching before is_dead.
        assert registry_mod.pid_is_alive(state["pid"], state["pid_started"])
        assert not registry_mod.is_dead(state)
    finally:
        if owner is not None:
            owner.kill()
            owner.wait()
    capsys.readouterr()


def test_detach_owner_early_exit_is_1(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = "det-secret-AbC123xyz"
    monkeypatch.setenv("META_MODEL_API_KEY", secret)
    stub = tmp_path / "fail_stub.py"
    stub.write_text(textwrap.dedent(f"""\
        import sys
        print("boom META_MODEL_API_KEY={secret}")
        sys.exit(1)
        """))

    real_popen = subprocess.Popen

    def popen(argv, **kwargs):
        if isinstance(argv, list) and argv[:2] == [sys.executable, "-m"]:
            kwargs.pop("stdin", None)
            kwargs.pop("stdout", None)
            kwargs.pop("stderr", None)
            log = Path(str(repo / ".tero" / "detach.log"))
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "wb") as log_fh:
                proc = real_popen(
                    [sys.executable, str(stub)],
                    stdout=log_fh, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            proc.wait(timeout=10)
            return proc
        return real_popen(argv, **kwargs)

    args = _go_args(repo)
    code = detach_mod.detach_go(args, popen=popen, poll_s=0.01, ready_s=10)
    out = capsys.readouterr().out
    assert code == 1
    assert secret not in out
    assert "boom" in out


def test_detach_caffeinate_argv(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = repo / ".tero" / "runs" / "20200101-000000-demo"
    run_dir.mkdir(parents=True)

    calls: list[list[str]] = []

    class _Owner:
        pid = 424242

        def poll(self):
            state = {"status": "running", "pid": self.pid}
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "state.json").write_text(json.dumps(state))

    class _Caff:
        pid = 999

        def poll(self):
            return None

    def popen(argv, **kwargs):
        calls.append(list(argv))
        if argv and argv[0] == "caffeinate":
            return _Caff()
        return _Owner()

    monkeypatch.setattr(sys, "platform", "darwin")
    # caffeinate found via PATH: an executable stub in tmp_path.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "caffeinate"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))

    args = _go_args(repo)
    code = detach_mod.detach_go(
        args, popen=popen, poll_s=0.0, ready_s=10,
        clock=lambda: 0.0, sleep=lambda s: None,
    )
    capsys.readouterr()
    assert code == 0
    assert ["caffeinate", "-i", "-w", "424242"] in calls

    # No stub in PATH → owner starts, no second popen call.
    emptybin = tmp_path / "emptybin"
    emptybin.mkdir()
    monkeypatch.setenv("PATH", str(emptybin))
    calls.clear()
    (run_dir / "state.json").unlink()
    code = detach_mod.detach_go(
        args, popen=popen, poll_s=0.0, ready_s=10,
        clock=lambda: 0.0, sleep=lambda s: None,
    )
    capsys.readouterr()
    assert code == 0
    assert len(calls) == 1


def test_go_decisions_flag():
    parser = build_parser()

    def cfg_for(argv):
        args = parser.parse_args(argv)
        return prepare_go_config(args)

    assert cfg_for(["go", "--no-menu"]).decisions is False
    assert cfg_for(["go", "--decisions", "--no-menu"]).decisions is True
    assert cfg_for(["go", "--detach"]).decisions is True
    detached = parser.parse_args(["go", "--detach"])
    assert detached.detach is True
    ownerless = parser.parse_args(["go", "--owner"])
    assert ownerless.owner is True

    plain = apply_cli_args(Config(), SimpleNamespace(
        no_hud=False, plan=None, verbose=None, test_command=None,
        test_command_scoped=None, max_rounds=None, no_escalation=False,
        player_spec=None, judge_spec=None, tester_spec=None,
        escalation_spec=None, decisions=False, detach=False, owner=False,
    ))
    assert plain.decisions is False


def test_owner_argv_passes_no_tester(tmp_path):
    argv = detach_mod.owner_argv(_go_args(tmp_path, no_tester=True))
    assert "--no-tester" in argv
    assert "--no-tester" not in detach_mod.owner_argv(_go_args(tmp_path))
