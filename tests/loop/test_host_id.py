"""Спек пульта 2, п. 1: pid хозяина по предкам, а не ppid (временный bash)."""

from __future__ import annotations

import os

from src.loop import host_id
from src.loop.host_id import Host, Proc, detect, strip_host_env, under_owner

# `tero wait` (python) ← bash -c ← claude (node) ← zsh ← login ← launchd
CHAIN = [
    Proc(5001, "bash", ("bash", "-c", "tero wait"), 100.0),
    Proc(4001, "node", ("node", "/opt/claude/cli.js"), 90.0),
    Proc(3001, "zsh", ("-zsh",), 80.0),
    Proc(2001, "login", ("login",), 70.0),
    Proc(1, "launchd", ("/sbin/launchd",), 1.0),
]


def test_claude_pid_from_env_wins_when_it_is_an_ancestor():
    host = detect({"CLAUDECODE": "1", "CLAUDE_PID": "4001", "CLAUDE_CODE_SESSION_ID": "s1"}, CHAIN)
    assert (host.kind, host.pid, host.started, host.session) == ("claude", 4001, 90.0, "s1")
    assert host.known


def test_first_non_wrapper_ancestor_without_claude_pid():
    host = detect({"CLAUDECODE": "1"}, CHAIN)
    assert (host.pid, host.started) == (4001, 90.0), "bash -c пропущен, взят claude"
    chain = [Proc(5001, "bash", ("bash",), 1.0), Proc(4002, "claude", ("claude",), 2.0)]
    assert detect({"CLAUDECODE": "1"}, chain).pid == 4002


def test_claimed_pid_outside_ancestry_is_refused():
    host = detect({"CLAUDECODE": "1", "CLAUDE_PID": "777"}, CHAIN)
    assert not host.known
    assert "777" in host.why


def test_pid_1_subreaper_and_wrong_name_are_refused():
    only_init = [Proc(5001, "bash", ("bash",), 1.0), Proc(1, "launchd", ("launchd",), 1.0)]
    assert not detect({"CLAUDECODE": "1"}, only_init).known
    systemd = [Proc(5001, "sh", ("sh",), 1.0), Proc(900, "systemd", ("systemd", "--user"), 1.0)]
    assert "subreaper" in detect({"CLAUDECODE": "1"}, systemd).why
    stranger = [Proc(5001, "bash", ("bash",), 1.0), Proc(4001, "vim", ("vim",), 1.0)]
    host = detect({"CLAUDECODE": "1"}, stranger)
    assert not host.known and "vim" in host.why
    # bwrap: everything above is a wrapper or pid 1 → no host, no lease, no code 8
    sandbox = [Proc(5001, "bash", ("bash",), 1.0), Proc(2, "bwrap", ("bwrap",), 1.0), Proc(1, "init", ("init",), 1.0)]
    assert not detect({"CLAUDECODE": "1"}, sandbox).known


def test_node_needs_claude_in_argv():
    plain_node = [Proc(5001, "bash", ("bash",), 1.0), Proc(4001, "node", ("node", "server.js"), 2.0)]
    assert not detect({"CLAUDECODE": "1"}, plain_node).known
    assert detect({"CLAUDECODE": "1"}, CHAIN).known


def test_host_kind_from_environment():
    assert host_id.host_kind({"CLAUDECODE": "1"}) == "claude"
    assert host_id.host_kind({"TERO_HOST": "opencode"}) == "opencode"
    assert host_id.host_kind({}) == ""
    # unknown kind: first non-wrapper ancestor, no name check
    assert detect({}, [Proc(5001, "bash", ("bash",), 1.0), Proc(4001, "vim", ("vim",), 1.0)]).pid == 4001


def test_under_owner_matches_pid_and_start_time():
    state = {"pid": 3001, "pid_started": 80.0}
    assert under_owner(state, CHAIN) is True
    assert under_owner({"pid": 3001, "pid_started": 81.5}, CHAIN) is False, "тот же pid, другой старт"
    assert under_owner({"pid": 9999, "pid_started": 1.0}, CHAIN) is False
    assert under_owner({}, CHAIN) is False


def test_strip_host_env_removes_exactly_the_session_identity():
    env = {
        "CLAUDE_PID": "1", "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s", "AI_AGENT": "x",
        "TERO_AGENT_PID": "2", "TERO_HOST": "claude", "CLAUDE_CODE_ENTRYPOINT": "cli",
        "CLAUDE_CODE_OAUTH_TOKEN": "keep", "CLAUDE_CONFIG_DIR": "keep", "PATH": "/bin",
        "CLAUDE_CODE_USE_BEDROCK": "1",
    }
    gone = strip_host_env(env)
    assert set(gone) >= {"CLAUDE_PID", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "AI_AGENT", "TERO_HOST"}
    assert env == {"CLAUDE_CODE_OAUTH_TOKEN": "keep", "CLAUDE_CONFIG_DIR": "keep",
                   "PATH": "/bin", "CLAUDE_CODE_USE_BEDROCK": "1"}


def test_real_ancestry_never_raises_and_is_nearest_first():
    chain = host_id.ancestors()
    assert isinstance(chain, list)
    if chain:
        assert chain[0].pid == os.getppid()
    assert isinstance(detect({"CLAUDECODE": "1"}), Host)
