"""The host in the log (план 09, фаза 5): the tail of the host's Claude Code transcript.

The owner reads new lines of `<config>/projects/<cwd>/<session>.jsonl` every
2 s while the lease host is alive and appends a whitelisted shadow of them to
`<run_dir>/host.jsonl`: `{ts, role: "host", type: text|tool, text}`. Only
`assistant` lines, only `text` blocks (first non-empty line, ≤ 300) and
`tool_use` by name; no `thinking`, no results, no `user`, no subagents.
The model merges the file into `log` only. Nothing here raises.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.errors import scrub_secrets
from src.fsio import append_text

HOST_FILE = "host.jsonl"
TEXT_LIMIT = 300
TICK_S = 2.0
_PATH_TOOLS = ("Read", "Edit", "Write", "Glob")
_WORD = re.compile(r"[A-Za-z0-9._+-]{1,32}")
_TOOL_NAME = re.compile(r"[A-Za-z0-9._+_-]{1,128}")
_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_SECRET_PATH = re.compile(r"(^\.env|\.env$|\.env\.|\.pem$|\.key$|^id_[a-z0-9]+$|private[_-]?key"
                          r"|(^|[._-])(secrets?|credentials?|tokens?)([._-]|$))", re.I)
_SID = re.compile(r"[A-Za-z0-9._-]{1,128}")


def _config_dir(env: Mapping[str, str] | None, home: str | Path | None) -> Path:
    e = os.environ if env is None else env
    custom = str(e.get("CLAUDE_CONFIG_DIR") or "").strip()
    if custom:
        return Path(custom)
    return Path(home if home is not None else Path.home()) / ".claude"


def _session_data(env: Mapping[str, str] | None, pid: int | None,
                  home: str | Path | None = None) -> dict:
    """The parsed `~/.claude/sessions/<pid>.json` (`sessionId`, `cwd`); empty on any miss.

    The fallback path never follows CLAUDE_CONFIG_DIR: it only moves the transcript root.
    """
    try:
        if int(pid or 0) <= 0:
            return {}
        base = Path(home if home is not None else Path.home()) / ".claude"
        data = json.loads((base / "sessions" / f"{int(pid)}.json")
                          .read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def session_of(env: Mapping[str, str] | None, pid: int | None,
               home: str | Path | None = None) -> str:
    """Session id of the host: the environment, else `~/.claude/sessions/<pid>.json`."""
    e = os.environ if env is None else env
    got = str(e.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    if got:
        return got
    return str(_session_data(e, pid, home).get("sessionId") or "").strip()


def session_cwd(env: Mapping[str, str] | None, pid: int | None,
                home: str | Path | None = None) -> str:
    """Working dir of the host from `~/.claude/sessions/<pid>.json`; "" when unknown."""
    return str(_session_data(env, pid, home).get("cwd") or "").strip()


def transcript_path(session: str, cwd: str | None = None, env: Mapping[str, str] | None = None,
                    home: str | Path | None = None) -> Path | None:
    """`projects/<cwd with dashes>/<id>.jsonl`, else a glob over projects; no freshness guessing."""
    sid = str(session or "").strip()
    if not sid or "/" in sid or sid.startswith(".") or not _SID.fullmatch(sid):
        return None
    projects = _config_dir(env, home) / "projects"
    try:
        if cwd:
            guess = projects / re.sub(r"[^A-Za-z0-9]", "-", str(cwd)) / f"{sid}.jsonl"
            if guess.is_file():
                return guess
        found = sorted(projects.glob(f"*/{sid}.jsonl"))
    except Exception:
        return None
    return found[0] if found else None


def _scrub(text: str) -> str:
    from src.relay.redact import redact

    return redact(scrub_secrets(text), ())


def _short(value: Any) -> str:
    for line in str(value or "").splitlines():
        if line.strip():
            return _scrub(line.strip())[:TEXT_LIMIT]
    return ""


def _path_text(raw: Any, repo: Path | None) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    name = os.path.basename(text.rstrip("/")) or text
    if _SECRET_PATH.search(name):
        return name
    if os.path.isabs(text):
        try:
            # normpath first: `/repo/../outside` is lexically under the repo but is not
            full = os.path.normpath(text)
            return str(Path(full).relative_to(os.path.normpath(str(repo)))) if repo is not None else name
        except ValueError:
            return name
    if repo is not None:
        base = os.path.normpath(str(repo))
        full = os.path.normpath(os.path.join(base, text))
        if full != base and not full.startswith(base + os.sep):
            return name
    return text


def _command_word(command: Any) -> str:
    try:
        words = shlex.split(str(command or ""), posix=True)
    except ValueError:
        return "bash"
    for word in words:
        if _ASSIGN.match(word):
            continue
        base = os.path.basename(word)
        return base if _WORD.fullmatch(base) else "bash"
    return "bash"


def _tool_text(block: dict, repo: Path | None) -> str:
    name = str(block.get("name") or "")
    if not _TOOL_NAME.fullmatch(name):
        return "tool"
    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
    if name in _PATH_TOOLS:
        raw = inp.get("file_path") or inp.get("path") or inp.get("pattern")
        path = _path_text(raw, repo)
        return f"{name} {path}" if path else name
    if name == "Bash":
        return f"Bash {_command_word(inp.get('command'))}"
    return name


def extract(obj: Any, repo: str | Path | None = None) -> list[dict]:
    """`[{role, type, text}]` for one transcript line; empty for everything off the whitelist."""
    try:
        if not isinstance(obj, dict) or obj.get("type") != "assistant" or obj.get("isSidechain"):
            return []
        message = obj.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        repo_path = Path(repo) if repo is not None else None
        out = []
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text, kind = _short(block.get("text")), "text"
            elif block.get("type") == "tool_use":
                text, kind = _scrub(_tool_text(block, repo_path))[:TEXT_LIMIT], "tool"
            else:
                continue
            if text:
                out.append({"role": "host", "type": kind, "text": text})
        return out
    except Exception:
        return []


def read_host_events(run_dir: str | Path, limit: int) -> list[dict]:
    """The last `limit` rows of `host.jsonl` (whole file is small: only whitelisted lines)."""
    try:
        lines = (Path(run_dir) / HOST_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for raw in lines[-max(0, int(limit or 0)):]:
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("role") == "host":
            out.append(obj)
    return out


def session_line(run_dir: str | Path) -> str:
    """`tero status`: the host session known to the lease, or «не известна»."""
    from src.loop import lease

    try:
        known = bool(str((lease.read(run_dir) or {}).get("session") or "").strip())
    except Exception:
        known = False
    return "сессия хозяина: известна" if known else "сессия хозяина: не известна"


class HostTail:
    def __init__(self, run_dir: str | Path, repo: str | Path | None = None, *,
                 env: Mapping[str, str] | None = None, home: str | Path | None = None,
                 is_alive: Any = None) -> None:
        self.run_dir = Path(run_dir)
        self.repo = Path(repo) if repo is not None else None
        self.env, self.home = env, home
        self.is_alive = is_alive
        self._path: Path | None = None
        self._offset: int | None = None
        self._cwd_pid: Any = None
        self._cwd = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def step(self) -> int:
        """One pass; the number of rows appended. Never raises."""
        try:
            from src.loop import lease

            data = lease.read(self.run_dir)
            kw = {} if self.is_alive is None else {"is_alive": self.is_alive}
            session = str((data or {}).get("session") or "").strip()
            if not session or not lease.alive(data, **kw):
                return 0
            pid = (data or {}).get("pid")
            if pid != self._cwd_pid:  # the host's cwd picks its transcript, not alphabet
                self._cwd_pid = pid
                self._cwd = session_cwd(self.env, pid, self.home)
            path = transcript_path(session, self._cwd or None, self.env, self.home)
            if path is None:
                return 0
            if path != self._path:
                self._path = path
                self._offset = None  # first read of a transcript starts at its end
            size = path.stat().st_size
            if self._offset is None:
                self._offset = size
                return 0
            if size < self._offset:
                self._offset = 0
            with open(path, "rb") as fh:
                fh.seek(self._offset)
                chunk = fh.read()
            cut = chunk.rfind(b"\n") + 1
            self._offset += cut
            return self._append(chunk[:cut])
        except Exception:
            return 0

    def _append(self, chunk: bytes) -> int:
        rows = []
        for raw in chunk.splitlines():
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            ts = obj.get("timestamp") if isinstance(obj, dict) else None
            if not isinstance(ts, str) or not ts:
                ts = datetime.now(timezone.utc).isoformat()
            rows += [{"ts": ts, **r} for r in extract(obj, self.repo)]
        if rows:
            append_text(self.run_dir / HOST_FILE,
                        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        return len(rows)

    def start(self) -> None:
        if self._thread is not None:
            return
        self.step()  # fixes the starting offset: old lines never reach the log
        self._thread = threading.Thread(target=self._loop, daemon=True, name="tero-host-tail")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(TICK_S):
            self.step()
            try:
                from src.loop.state import RunState

                if (RunState.read(self.run_dir / "state.json") or {}).get("status") in (
                        "done", "stopped", "failed"):
                    return
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()


_GUARD = threading.Lock()
_TAILS: dict[str, HostTail] = {}


def ensure_host_tail(run_dir: str | Path, repo: str | Path | None = None) -> HostTail | None:
    """One tail thread per run; started by the owner next to the poller."""
    key = str(Path(run_dir).absolute())
    try:
        with _GUARD:
            got = _TAILS.get(key)
            if got is None or (got._thread is not None and not got._thread.is_alive()):
                got = _TAILS[key] = HostTail(run_dir, repo)
                got.start()
            return got
    except Exception:
        return None
