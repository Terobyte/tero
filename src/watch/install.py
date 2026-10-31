"""`tero watch --install|--uninstall|--status`: launchd (mac) / systemd --user (Linux)."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from src.fsio import atomic_write_bytes, atomic_write_text

LABEL = "com.tero.watch"
UNIT = "tero-watch.service"


def detect() -> str:
    return "darwin" if sys.platform == "darwin" else "linux"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT


def installed() -> bool:
    return plist_path().exists() or unit_path().exists()


def _argv() -> list[str]:
    return [sys.executable, "-m", "src.cli_entry", "watch"]


def _env() -> dict[str, str]:
    # a service starts with an empty environment: PATH and HOME are frozen here
    return {"PATH": os.environ.get("PATH", ""), "HOME": str(Path.home())}


def _run(argv: list[str], **kw: Any) -> Any:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=30, **kw)
    except Exception:
        return subprocess.CompletedProcess(argv, 1, "", "")


def _code(rc: Any) -> int:
    try:
        return int(rc.returncode)
    except (AttributeError, TypeError, ValueError):
        return int(rc)


def install(platform: str | None = None, run: Callable[..., Any] = _run) -> str:
    plat = platform or detect()
    cwd = str(Path(__file__).resolve().parents[2])
    if plat == "darwin":
        data = {"Label": LABEL, "ProgramArguments": _argv(), "WorkingDirectory": cwd,
                "EnvironmentVariables": _env(), "RunAtLoad": True, "KeepAlive": True,
                "AbandonProcessGroup": True,
                "StandardOutPath": str(Path.home() / ".tero" / "watch.log"),
                "StandardErrorPath": str(Path.home() / ".tero" / "watch.log")}
        path = plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, plistlib.dumps(data))
        if _code(run(["launchctl", "load", "-w", str(path)])) != 0:
            raise RuntimeError("launchctl load отказал")
        return "darwin"
    env = _env()
    unit = ("[Unit]\nDescription=tero watch\n\n[Service]\n"
            f"ExecStart={' '.join(_argv())}\nWorkingDirectory={cwd}\n"
            f"Environment=PATH={env['PATH']}\nEnvironment=HOME={env['HOME']}\n"
            "KillMode=process\nRestart=always\n\n[Install]\nWantedBy=default.target\n")
    path = unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, unit)
    if _code(run(["systemctl", "--user", "enable", "--now", UNIT])) != 0:
        raise RuntimeError("systemctl enable отказал")
    return "linux"


def uninstall(platform: str | None = None, run: Callable[..., int] = _run) -> None:
    plat = platform or detect()
    if plat == "darwin":
        run(["launchctl", "unload", str(plist_path())])
        plist_path().unlink(missing_ok=True)
    else:
        run(["systemctl", "--user", "disable", "--now", UNIT])
        unit_path().unlink(missing_ok=True)


def status(platform: str | None = None, run: Callable[..., Any] = _run) -> dict:
    plat = platform or detect()
    path = plist_path() if plat == "darwin" else unit_path()
    found = path.exists()
    running: bool | None = None
    if found:
        try:
            if plat == "darwin":
                rc = run(["launchctl", "list", LABEL])
            else:
                rc = run(["systemctl", "--user", "is-active", "--quiet", UNIT])
            code = rc.returncode if hasattr(rc, "returncode") else rc
            running = code == 0
        except Exception:
            running = None
    return {"platform": plat, "installed": found, "running": running, "path": str(path)}


def _index_repos() -> list[str]:
    import json

    from src.loop.runs_index import index_path

    try:
        data = json.loads(index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    repos: list[str] = []
    for entry in data.values():
        repo = entry.get("repo") if isinstance(entry, dict) else None
        if repo and str(repo) not in repos:
            repos.append(str(repo))
    return repos


def repo_access_lines() -> list[str]:
    """Репо из индекса, которые сторож должен читать: проверка + что разрешить."""
    lines = []
    for repo in _index_repos():
        ok = os.access(repo, os.R_OK | os.X_OK)
        lines.append(f"{'читается' if ok else 'нет доступа — разреши сторожу'}: {repo}")
    return lines


def hints(platform: str, run: Callable[..., Any] = _run) -> list[str]:
    if platform == "darwin":
        return ["разреши Terminal/launchd доступ к папкам репо (Full Disk Access или Files and Folders), "
                "если репо в ~/Desktop, ~/Documents или ~/Downloads"]
    try:
        import getpass

        res = run(["loginctl", "show-user", getpass.getuser(), "-p", "Linger", "--value"])
        out = res.stdout if hasattr(res, "stdout") else ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if "yes" in str(out).strip().lower():
            return []
    except Exception:
        pass
    return ["выполни один раз: `loginctl enable-linger $USER` (иначе сторож умрёт при выходе из сессии)"]
