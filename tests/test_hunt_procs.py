"""Red proof tests for hunt bugs B06/B07/B29/B30/B32 (writer 2)."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


# ── B06: timeout/cancel hangs when a grandchild inherits the pipe ─────────────

def test_B06_timeout_kills_pipe_holding_grandchild(tmp_path):
    repo_root = str(Path(__file__).resolve().parents[1])
    pidfile = tmp_path / "b06_grandchild.pid"
    if pidfile.exists():
        pidfile.unlink()
    child_src = (
        "import subprocess, time\n"
        "from pathlib import Path\n"
        f"g = subprocess.Popen(['sleep', '30'])\n"
        f"Path({str(pidfile)!r}).write_text(str(g.pid))\n"
        "print('{}', flush=True)\n"
        "time.sleep(30)\n"
    )
    script = (
        "import asyncio, sys\n"
        "from src.providers.subprocess_runner import run_subprocess_jsonl\n"
        f"CHILD = {child_src!r}\n"
        "async def main():\n"
        f"    gen = run_subprocess_jsonl([sys.executable, '-c', CHILD], {str(tmp_path)!r})\n"
        "    async for ev in gen:\n"
        "        pass\n"
        "    print('DONE', flush=True)\n"
        "try:\n"
        "    asyncio.run(asyncio.wait_for(main(), 2))\n"
        "except TimeoutError:\n"
        "    print('TIMEOUT_CAUGHT', flush=True)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=True, cwd=repo_root,
    )
    try:
        out, _ = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        _kill_pidfile(pidfile)
        pytest.fail("B06: висит — wait_for(2) не завершил скрипт за 10 с (внук держит pipe)")
    assert "TIMEOUT_CAUGHT" in out, (
        f"B06: ожидался TimeoutError за ~2 с, вывод: {out[-500:]!r}"
    )
    try:
        pid_text = pidfile.read_text().strip()
    except OSError as exc:
        pytest.fail(f"B06: внук не записал pid-файл: {exc}")
    try:
        pid = int(pid_text)
    except ValueError:
        pytest.fail(f"B06: плохой pid в файле: {pid_text!r}")
    dead = _wait_dead(pid, timeout_s=3.0)
    try:
        assert dead, (
            f"B06: внук sleep 30 (pid={pid}) жив после таймаута — группа процессов не убита"
        )
    finally:
        _kill_pid(pid)


def _wait_dead(pid: int, timeout_s: float) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.1)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _kill_pid(pid: int) -> None:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    except PermissionError:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _kill_pidfile(pidfile: Path) -> None:
    try:
        pid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return
    _kill_pid(pid)


# ── B07: orphaned grandchild survives a finished run ──────────────────────────

@pytest.mark.asyncio
async def test_B07_finished_run_kills_orphaned_grandchild(tmp_path):
    from src.providers.subprocess_runner import run_subprocess_jsonl

    pidfile = tmp_path / "b07_grandchild.pid"
    if pidfile.exists():
        pidfile.unlink()
    child_src = (
        "import subprocess\n"
        "from pathlib import Path\n"
        "g = subprocess.Popen(['sleep', '30'],"
        " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,"
        " start_new_session=False)\n"
        f"Path({str(pidfile)!r}).write_text(str(g.pid))\n"
        "print('{}', flush=True)\n"
    )
    events = []
    async for ev in run_subprocess_jsonl(
        [sys.executable, "-c", child_src], str(tmp_path)
    ):
        events.append(ev)
    assert any(isinstance(e, dict) for e in events), (
        f"B07: генератор не отдал события CHILD-процесса: {events!r}"
    )
    try:
        pid = int(pidfile.read_text().strip())
    except OSError as exc:
        pytest.fail(f"B07: внук не записал pid-файл: {exc}")
    except ValueError as exc:
        pytest.fail(f"B07: плохой pid в файле: {exc}")
    try:
        dead = await _await_dead(pid, timeout_s=2.0)
        assert dead, (
            f"B07: внук sleep 30 (pid={pid}) жив после полного прохода генератора"
        )
    finally:
        _kill_pid(pid)


async def _await_dead(pid: int, timeout_s: float) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        await asyncio.sleep(0.1)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


# ── B29b: broken YAML -> ValueError with path ─────────────────────────────────

def test_B29b_broken_yaml_raises_valueerror_with_path(tmp_path):
    from src.config import resolve_config

    cfg_path = tmp_path / ".tero" / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("defaults: [\n")
    try:
        resolve_config({"working_dir": str(tmp_path)})
    except ValueError as exc:
        assert str(cfg_path) in str(exc) or str(tmp_path) in str(exc), (
            f"B29b: в ValueError нет пути к файлу: {exc!r}"
        )
        return
    except Exception as exc:
        pytest.fail(
            f"B29b: вместо ValueError брошено {type(exc).__name__}: {exc}"
        )
    pytest.fail("B29b: битый YAML не бросил исключение")


# ── B29c: custom model reachable in menu presets ──────────────────────────────

def test_B29c_custom_model_choice_in_allowed_presets():
    import src.menu as menu_mod
    from src.config import KNOWN_PROVIDERS

    fn_allowed = getattr(menu_mod, "_custom_model_allowed", None)
    fn_presets = getattr(menu_mod, "_model_presets_for_provider", None)
    if fn_allowed is None or fn_presets is None:
        pytest.fail("B29c: нет _custom_model_allowed/_model_presets_for_provider")
    checked = 0
    for provider in KNOWN_PROVIDERS:
        if not fn_allowed(provider):
            continue
        checked += 1
        presets = fn_presets(provider)
        assert "__custom__" in presets.values(), (
            f"B29c: провайдер {provider!r} позволяет свой ввод, "
            f"но в пресетах нет '__custom__': {sorted(presets.values())!r}"
        )
    assert checked > 0, "B29c: ни один провайдер не _custom_model_allowed"


# ── B30: numeric settings validation ─────────────────────────────────────────

def _write_project_yaml(tmp_path: Path, text: str) -> Path:
    cfg_path = tmp_path / ".tero" / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(text)
    return cfg_path


def test_B30a_zero_max_rounds_yaml_rejected(tmp_path):
    from src.config import resolve_config

    _write_project_yaml(tmp_path, "defaults:\n  max_rounds: 0\n")
    try:
        resolve_config({"working_dir": str(tmp_path)})
    except ValueError as exc:
        assert "max_rounds" in str(exc), (
            f"B30a: в ValueError нет 'max_rounds': {exc!r}"
        )
        return
    except Exception as exc:
        pytest.fail(
            f"B30a: вместо ValueError брошено {type(exc).__name__}: {exc}"
        )
    pytest.fail("B30a: max_rounds: 0 из yaml принят без ValueError")


def test_B30b_zero_max_rounds_cli_rejected():
    from src.config import Config, apply_cli_args

    args = SimpleNamespace(
        plan=None, player_spec=None, judge_spec=None, tester_spec=None,
        escalation_spec=None, no_escalation=False, test_command=None,
        test_command_scoped=None, max_rounds=0, verbose=False,
    )
    try:
        apply_cli_args(Config(), args)
    except ValueError as exc:
        assert "max_rounds" in str(exc), (
            f"B30b: в ValueError нет 'max_rounds': {exc!r}"
        )
        return
    except Exception as exc:
        pytest.fail(
            f"B30b: вместо ValueError брошено {type(exc).__name__}: {exc}"
        )
    pytest.fail("B30b: apply_cli_args(max_rounds=0) принят без ValueError")


def test_B30c_bad_player_timeout_yaml_rejected(tmp_path):
    from src.config import resolve_config

    _write_project_yaml(tmp_path, "defaults:\n  player_timeout_s: abc\n")
    try:
        resolve_config({"working_dir": str(tmp_path)})
    except ValueError as exc:
        assert "player_timeout_s" in str(exc), (
            f"B30c: в ValueError нет 'player_timeout_s': {exc!r}"
        )
        return
    except Exception as exc:
        pytest.fail(
            f"B30c: вместо ValueError брошено {type(exc).__name__}: {exc}"
        )
    pytest.fail("B30c: player_timeout_s: abc из yaml принят без ValueError")


def test_B30d_bool_max_rounds_yaml_rejected(tmp_path):
    from src.config import resolve_config

    _write_project_yaml(tmp_path, "defaults:\n  max_rounds: true\n")
    try:
        resolve_config({"working_dir": str(tmp_path)})
    except ValueError as exc:
        return
    except Exception as exc:
        pytest.fail(
            f"B30d: вместо ValueError брошено {type(exc).__name__}: {exc}"
        )
    pytest.fail("B30d: max_rounds: true из yaml принят без ValueError")


# ── B32: claude check_ready must not raise ────────────────────────────────────

def test_B32a_check_ready_timeout_returns_false(monkeypatch):
    import shutil
    import subprocess as sp

    import src.providers.claude_native as mod

    cls = getattr(mod, "ClaudeProvider", None) or getattr(
        mod, "ClaudeNativeProvider", None
    )
    if cls is None:
        pytest.fail("B32: нет ClaudeProvider/ClaudeNativeProvider")
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: "/usr/bin/claude")

    def boom(*_a, **_k):
        raise sp.TimeoutExpired(cmd="claude", timeout=10)

    monkeypatch.setattr(sp, "run", boom)
    try:
        res = cls().check_ready()
    except Exception as exc:
        pytest.fail(
            f"B32: check_ready бросает {type(exc).__name__} на TimeoutExpired: {exc}"
        )
    assert isinstance(res, (tuple, list)) and len(res) == 2, (
        f"B32: check_ready должен вернуть (False, причина), получено {res!r}"
    )
    ok, reason = res
    assert ok is False, f"B32: check_ready на таймауте должен вернуть False: {res!r}"
    assert isinstance(reason, str) and reason.strip() != "", (
        f"B32: причина на таймауте пустая: {res!r}"
    )


def test_B32b_check_ready_missing_binary_returns_false(monkeypatch):
    import shutil
    import subprocess as sp

    import src.providers.claude_native as mod

    cls = getattr(mod, "ClaudeProvider", None) or getattr(
        mod, "ClaudeNativeProvider", None
    )
    if cls is None:
        pytest.fail("B32: нет ClaudeProvider/ClaudeNativeProvider")
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: "/usr/bin/claude")

    def boom(*_a, **_k):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(sp, "run", boom)
    try:
        res = cls().check_ready()
    except Exception as exc:
        pytest.fail(
            f"B32: check_ready бросает {type(exc).__name__} на FileNotFoundError: {exc}"
        )
    assert isinstance(res, (tuple, list)) and len(res) == 2, (
        f"B32: check_ready должен вернуть (False, причина), получено {res!r}"
    )
    ok, reason = res
    assert ok is False, f"B32: check_ready без бинаря должен вернуть False: {res!r}"
    assert isinstance(reason, str) and reason.strip() != "", (
        f"B32: причина без бинаря пустая: {res!r}"
    )
