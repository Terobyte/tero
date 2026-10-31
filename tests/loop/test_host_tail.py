"""Хозяин в логе (план 09, фаза 5): хвост транскрипта Claude Code → host.jsonl → log."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from src.hud.model_view import build_from_files
from src.loop import host_id, lease
from src.loop.host_tail import (HostTail, read_host_events, session_line, session_of,
                                transcript_path, extract)

SECRET = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4"


def _asst(*blocks, **extra):
    return {"type": "assistant", "message": {"content": list(blocks)}, **extra}


def _text(t):
    return {"type": "text", "text": t}


def _tool(name, **inp):
    return {"type": "tool_use", "name": name, "input": inp}


def test_host_tail_whitelist(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    long_text = ("первая строка " + "слово " * 80).strip() + "\nвторая"
    rows = extract(_asst(
        {"type": "thinking", "thinking": "тайные мысли"},
        _text(long_text),
        _text(f"ключ {SECRET}"),
        _tool("Bash", command="DATABASE_URL=postgres://u:p@h/db cmd --flag"),
        _tool("Bash", command="cat <<EOF\nтело\nEOF"),
        _tool("Bash", command="/usr/local/bin/run now"),
        _tool("Bash", command='"странное" x'),
        _tool("WebFetch", url="https://example.com/?token=abc", prompt="p"),
        _tool("Read", file_path=str(repo / "src" / "a.py")),
        _tool("Read", file_path="/etc/passwd"),
        _tool("Edit", file_path=str(repo / ".env")),
        _tool("Read", file_path="/home/u/.ssh/id_rsa"),
    ), repo)
    texts = [r["text"] for r in rows]
    assert all(r["role"] == "host" and r["type"] in ("text", "tool") for r in rows)
    assert texts[0].startswith("первая строка слово") and len(texts[0]) == 300
    assert not any("тайные" in t for t in texts)
    assert not any(SECRET in t for t in texts)
    assert any("sk-" not in t and t.startswith("ключ") for t in texts)
    assert "Bash cmd" in texts and "Bash cat" in texts and "Bash run" in texts
    assert "Bash bash" in texts
    assert "WebFetch" in texts
    assert "Read src/a.py" in texts and "Read passwd" in texts
    assert "Edit .env" in texts and "Read id_rsa" in texts
    assert not any("example.com" in t or "postgres" in t or "/etc" in t for t in texts)
    assert extract({"type": "user", "message": {"content": [_text("привет")]}}, repo) == []
    assert extract(_asst(_text("субагент"), ), repo) != []
    assert extract({**_asst(_text("субагент")), "isSidechain": True}, repo) == []
    assert extract({"type": "user", "toolUseResult": "x"}, repo) == []


def test_f11_session_ignores_config_dir(tmp_path):
    """F11: запасной путь сессии — всегда ~/.claude/sessions, CLAUDE_CONFIG_DIR меняет только корень транскриптов."""
    import json as _json

    from src.loop.host_tail import session_of as _session_of

    home = tmp_path / "h11"
    (home / ".claude" / "sessions").mkdir(parents=True)
    (home / ".claude" / "sessions" / "4242.json").write_text(
        _json.dumps({"sessionId": "sess-from-home", "cwd": "/w"}), encoding="utf-8")
    cfg = tmp_path / "c11"
    cfg.mkdir()
    assert _session_of({"CLAUDE_CONFIG_DIR": str(cfg)}, 4242, home) == "sess-from-home"


def test_f12_long_tool_name(tmp_path):
    """F12: длинное допустимое имя инструмента (например MCP) — выводится целиком, а не `tool`."""
    rows = extract(_asst(_tool("mcp__analytics_server__inspect_database_schema", foo="bar")),
                   tmp_path)
    assert [r["text"] for r in rows] == ["mcp__analytics_server__inspect_database_schema"]


def test_host_session_fallback(tmp_path):
    home = tmp_path / "home"
    (home / ".claude" / "sessions").mkdir(parents=True)
    (home / ".claude" / "sessions" / "4242.json").write_text(
        json.dumps({"sessionId": "sess-from-file", "cwd": "/w"}), encoding="utf-8")
    assert session_of({"CLAUDE_CODE_SESSION_ID": "sess-env"}, 4242, home) == "sess-env"
    assert session_of({}, 4242, home) == "sess-from-file"
    assert session_of({}, 999, home) == ""
    assert session_of({}, 0, home) == ""
    cfg = tmp_path / "cfgdir" / "sessions"
    cfg.mkdir(parents=True)
    (cfg / "7.json").write_text(json.dumps({"sessionId": "s7"}), encoding="utf-8")
    # CLAUDE_CONFIG_DIR не влияет на запасной путь сессии — только ~/.claude/sessions
    assert session_of({"CLAUDE_CONFIG_DIR": str(tmp_path / "cfgdir")}, 7, home) == ""

    # detect() берёт запасной путь, когда переменной нет
    old = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        chain = [host_id.Proc(4242, "claude", ("claude",), 100.0)]
        found = host_id.detect({"CLAUDECODE": "1"}, chain)
    finally:
        if old is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old
    assert found.pid == 4242 and found.session == "sess-from-file"

    run_dir = tmp_path / "repo" / ".tero" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    assert "сессия хозяина: не известна" in session_line(run_dir)
    lease.write(run_dir, until=time.time() + 60, pid=1, pid_started=1.0, host="claude",
                session="abc")
    assert "abc" not in session_line(run_dir) and "не известна" not in session_line(run_dir)
    lease.write(run_dir, until=time.time() + 60, pid=1, pid_started=1.0, host="claude", session="")
    assert "не известна" in session_line(run_dir)


def test_transcript_path(tmp_path):
    cfg = tmp_path / "c"
    f = cfg / "projects" / "-w-x" / "sid.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text("", encoding="utf-8")
    env = {"CLAUDE_CONFIG_DIR": str(cfg)}
    assert transcript_path("sid", "/w/x", env, tmp_path) == f
    assert transcript_path("sid", None, env, tmp_path) == f
    assert transcript_path("nope", None, env, tmp_path) is None
    assert transcript_path("", None, env, tmp_path) is None


def _setup(tmp_path):
    repo = tmp_path / "repo"
    run_dir = repo / ".tero" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    cfg = tmp_path / "c"
    tr = cfg / "projects" / "-p" / "sid.jsonl"
    tr.parent.mkdir(parents=True)
    ok = lease.write(run_dir, until=time.time() + 600, pid=os.getpid(), pid_started=1.0,
                     host="claude", session="sid")
    assert ok
    return repo, run_dir, tr, {"CLAUDE_CONFIG_DIR": str(cfg)}


def _line(tr, *blocks):
    with open(tr, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_asst(*blocks), ensure_ascii=False) + "\n")


def test_host_tail_from_end(tmp_path):
    repo, run_dir, tr, env = _setup(tmp_path)
    _line(tr, _text("старое"))
    tail = HostTail(run_dir, repo, env=env, home=tmp_path, is_alive=lambda p, s: True)
    tail.step()
    assert read_host_events(run_dir, 10) == []
    _line(tr, _text("новое"), _tool("Glob", pattern="src/*.py"))
    tail.step()
    got = [e["text"] for e in read_host_events(run_dir, 10)]
    assert got == ["новое", "Glob src/*.py"]
    tail.step()  # ничего нового — ничего не дублируется
    assert len(read_host_events(run_dir, 10)) == 2
    # хозяин умер — не читаем
    _line(tr, _text("после смерти"))
    dead = HostTail(run_dir, repo, env=env, home=tmp_path, is_alive=lambda p, s: False)
    dead.step()
    assert len(read_host_events(run_dir, 10)) == 2


def test_doing_age_ignore_host(tmp_path):
    repo, run_dir, tr, env = _setup(tmp_path)
    now = time.time()
    state = {"status": "running", "role": "player", "phase": "p", "phase_index": 0,
             "round": 1, "pid": os.getpid()}
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def iso(t):
        return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t))

    stream = [{"ts": iso(now - 100), "type": "text", "role": "player", "text": "пишу"},
              {"ts": iso(now - 50), "type": "text", "role": "player", "text": "ещё"}]
    (run_dir / "stream.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in stream), encoding="utf-8")
    before = build_from_files(run_dir, now, log_limit=40)
    host = [{"ts": iso(now - 75), "role": "host", "type": "text", "text": "я хозяин"},
            {"ts": iso(now - 1), "role": "host", "type": "tool", "text": "Read a.py"}]
    (run_dir / "host.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in host), encoding="utf-8")
    after = build_from_files(run_dir, now, log_limit=40)
    assert after["doing"] == before["doing"]
    assert after["last_event_at"] == before["last_event_at"]
    assert after["light"] == before["light"] and after["light_text"] == before["light_text"]
    assert [r["text"] for r in after["log"]] == ["пишу", "я хозяин", "ещё", "Read a.py"]
    assert [r["role"] for r in after["log"]] == ["player", "host", "player", "host"]
    cut = build_from_files(run_dir, now, log_limit=2)
    assert [r["text"] for r in cut["log"]] == ["ещё", "Read a.py"]


def test_f7_session_glob_rejected(tmp_path):
    cfg = tmp_path / "c7"
    other = cfg / "projects" / "-private" / "anything.jsonl"
    other.parent.mkdir(parents=True)
    other.write_text("", encoding="utf-8")
    env = {"CLAUDE_CONFIG_DIR": str(cfg)}
    assert transcript_path("*", None, env, tmp_path) is None


def test_f8_relative_outside_repo_basename(tmp_path):
    repo = tmp_path / "repo8"
    repo.mkdir()
    rows = extract(_asst(_tool("Read", file_path="../outside/private.txt")), repo)
    assert [r["text"] for r in rows] == ["Read private.txt"]


def test_f9_private_key_basename(tmp_path):
    repo = tmp_path / "repo9"
    repo.mkdir()
    rows = extract(_asst(_tool("Read", file_path="certs/private_key")), repo)
    assert [r["text"] for r in rows] == ["Read private_key"]


def test_host_tail_prefers_host_cwd(tmp_path):
    """F1: cwd из sessions/<pid>.json ведёт к транскрипту хозяина, а не к первому по алфавиту."""
    from src.loop.host_tail import session_cwd

    cfg = tmp_path / "c"
    (tmp_path / ".claude" / "sessions").mkdir(parents=True)
    (tmp_path / ".claude" / "sessions" / "4242.json").write_text(
        json.dumps({"sessionId": "sid", "cwd": "/wanted"}), encoding="utf-8")
    env = {"CLAUDE_CONFIG_DIR": str(cfg)}
    assert session_cwd(env, 4242, tmp_path) == "/wanted"
    assert session_cwd(env, 999, tmp_path) == ""

    other = cfg / "projects" / "-other" / "sid.jsonl"
    wanted = cfg / "projects" / "-wanted" / "sid.jsonl"
    other.parent.mkdir(parents=True)
    wanted.parent.mkdir(parents=True)
    other.write_text("", encoding="utf-8")
    wanted.write_text("", encoding="utf-8")

    repo = tmp_path / "repo"
    run_dir = repo / ".tero" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    assert lease.write(run_dir, until=time.time() + 600, pid=4242, pid_started=1.0,
                       host="claude", session="sid")
    tail = HostTail(run_dir, repo, env=env, home=tmp_path, is_alive=lambda p, s: True)
    assert tail.step() == 0
    _line(wanted, _text("своё"))
    _line(other, _text("чужое"))
    assert tail.step() == 1
    assert [e["text"] for e in read_host_events(run_dir, 10)] == ["своё"]


def test_bridge_view_merges_host(monkeypatch, tmp_path):
    """F10: локальный HUD сливает host.jsonl в log, как build_from_files."""
    from src.hud import view as hud_view

    monkeypatch.setattr(hud_view, "is_dead", lambda s: False)
    run_dir = tmp_path / "r1"
    run_dir.mkdir()
    (run_dir / "state.json").write_text(json.dumps(
        {"status": "running", "role": "player", "phase": "p", "phase_index": 0,
         "round": 1, "pid": 1}), encoding="utf-8")
    (run_dir / "host.jsonl").write_text(json.dumps(
        {"ts": "2026-10-31T00:00:00+00:00", "role": "host", "type": "text",
         "text": "host row"}) + "\n", encoding="utf-8")
    data = hud_view.Bridge(run_dir).view()
    assert any(r.get("text") == "host row" and r.get("role") == "host"
               for r in data["log"])


def test_f13_absolute_dotdot_outside_repo_basename(tmp_path):
    repo = tmp_path / "repo13"
    repo.mkdir()
    path = f"{repo}/../outside/private.txt"
    rows = extract(_asst(_tool("Read", file_path=path)), repo)
    assert [r["text"] for r in rows] == ["Read private.txt"]


def test_f14_ordinary_token_named_file_keeps_repo_path(tmp_path):
    repo = tmp_path / "repo14"
    repo.mkdir()
    rows = extract(_asst(_tool("Read", file_path="src/tokenizer.py")), repo)
    assert [r["text"] for r in rows] == ["Read src/tokenizer.py"]
    rows = extract(_asst(_tool("Read", file_path=f"{repo}/src/tokenizer.py")), repo)
    assert [r["text"] for r in rows] == ["Read src/tokenizer.py"]
