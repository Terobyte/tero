"""Red tests for hunt bugs B05/B13/B14/B15/B18/B19/B26/B37 (writer B)."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.config import Config, apply_cli_args
from src.loop.agents import extract_json
from src.loop.ledger import Ledger, history, new_run_dir


def _ns(**over):
    base = {
        "plan": None, "player_spec": None, "judge_spec": None,
        "tester_spec": None, "escalation_spec": None,
        "no_escalation": False, "test_command": None,
        "test_command_scoped": None, "max_rounds": None, "verbose": False,
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_B05_stderr_flood_does_not_hang(tmp_path):
    repo_root = str(Path(__file__).resolve().parents[1])
    script = (
        "import asyncio, sys\n"
        "from src.providers.subprocess_runner import run_subprocess_jsonl\n"
        "async def main():\n"
        "    gen = run_subprocess_jsonl(\n"
        "        [sys.executable, '-c', \"import sys; sys.stderr.write('x'*3000000); sys.stderr.flush(); print('{}')\"],\n"
        f"        {str(tmp_path)!r},\n"
        "    )\n"
        "    events = []\n"
        "    async for ev in gen:\n"
        "        events.append(ev)\n"
        "    print('GOT', len(events), flush=True)\n"
        "try:\n"
        "    asyncio.run(asyncio.wait_for(main(), 5))\n"
        "except TimeoutError:\n"
        "    print('TIMEOUT_CAUGHT', flush=True)\n"
    )
    import signal
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=True, cwd=repo_root,
    )
    try:
        out, _ = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
        pytest.fail("B05: hang — сценарий с 3MB в stderr не завершился за 15 с")
    assert ("GOT" in out) or ("TIMEOUT_CAUGHT" in out), (
        f"B05: сценарий не завершился корректно за 15 с: rc={proc.returncode} out={out[-500:]!r}"
    )


def test_B13_judge_switch_resets_stale_model():
    cfg = apply_cli_args(Config(), _ns(judge_spec="claude"))
    assert cfg.judge_provider == "claude"
    assert cfg.judge_model != "gpt-5.6-terra", (
        f"B13: --judge claude оставил чужую модель codex: {cfg.judge_model!r}"
    )


def test_B14_menu_save_keeps_providers_section(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".tero").mkdir(parents=True)
    (home / ".tero" / "config.yaml").write_text(
        yaml.dump({"providers": {"codex": {"command": "/x/codex"}},
                   "defaults": {"player_provider": "muse"}}),
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    from src import menu
    menu._save_global_default(Config())
    data = yaml.safe_load((home / ".tero" / "config.yaml").read_text())
    assert "providers" in data, (
        f"B14: секция providers потеряна при сохранении меню, ключи: {sorted(data)}"
    )


def test_B15_menu_save_drops_project_values(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    from src import menu
    menu._save_global_default(Config(plan_file="docs/a.md", test_command="make t"))
    data = yaml.safe_load((home / ".tero" / "config.yaml").read_text())
    defaults = data.get("defaults", {})
    assert defaults.get("plan_file") != "docs/a.md", (
        f"B15: проектный plan_file сохранён в глобальные defaults: {defaults.get('plan_file')!r}"
    )
    assert defaults.get("test_command") != "make t", (
        f"B15: проектный test_command сохранён в глобальные defaults: {defaults.get('test_command')!r}"
    )


def test_B18_exclude_tero_in_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "-C", str(repo), "commit", "-qm", "init"], check=True,
    )
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", str(wt)],
                   check=True, capture_output=True, text=True)
    assert (wt / ".git").is_file()
    from src.loop.git import exclude_tero
    try:
        exclude_tero(str(wt))
    except Exception as exc:
        pytest.fail(f"B18: exclude_tero падает в worktree: {type(exc).__name__}: {exc}")
    out = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "--git-path", "info/exclude"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    exclude = Path(out) if Path(out).is_absolute() else wt / out
    assert ".tero/" in exclude.read_text().splitlines(), (
        f"B18: .tero/ не попал в exclude worktree ({exclude})"
    )


def test_B19_fenced_block_does_not_shadow_final_bare_json():
    text = '```json\n{"a": 1}\n```\nтекст\n{"verdict": "SHIP"}'
    data = extract_json(text)
    assert data is not None and data.get("verdict") == "SHIP", (
        f"B19: вернулся ранний fenced-блок вместо последнего объекта: {data!r}"
    )


def test_B26_history_survives_truncated_ledger(tmp_path):
    good = new_run_dir(tmp_path, "good")
    Ledger(good).event("run_start", plan="a.md")
    Ledger(good).event("run_end", reason="ship", phases_shipped=1, phases_total=1)
    bad = new_run_dir(tmp_path, "bad")
    Ledger(bad).event("run_start", plan="b.md")
    with (bad / "ledger.jsonl").open("a") as fh:
        fh.write('{"event": "run_e')
    try:
        rows = history(tmp_path)
    except json.JSONDecodeError as exc:
        pytest.fail(f"B26: history падает на обрезанном ledger: {exc}")
    assert "good" in [r["slug"] for r in rows], (
        f"B26: целый ран потерян: {[r['slug'] for r in rows]}"
    )
    try:
        assert history(tmp_path, limit=0) == []
    except json.JSONDecodeError as exc:
        pytest.fail(f"B26: history(limit=0) падает: {exc}")


def test_B37_adapter_survives_non_dict_event():
    from src.providers.gemini import GeminiProvider
    for bad in (42, None):
        try:
            GeminiProvider()._adapt_gemini_event(bad)
        except AttributeError as exc:
            pytest.fail(f"B37: _adapt_gemini_event({bad!r}) падает: AttributeError: {exc}")
