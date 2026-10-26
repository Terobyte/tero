"""Start checks of `tero go` before the first phase: tree, providers, families."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.config import (
    Config,
    ConfigError,
    UnknownProviderError,
    family_key,
    normalize_provider,
)
from src.loop.free import check_free_models
from src.loop.gates import TestCommand, detect, print_red_tail, run_all
from src.loop.git import DirtyWorktreeError, current_branch, ensure_clean, status_short
from src.loop.state import RunState
from src.providers import free_models


def _doing(state: RunState, doing: str) -> None:
    try:
        state.update(doing=doing)
    except Exception:
        pass


def start_checks(
    cfg: Config,
    repo: Path,
    state: RunState,
    *,
    check_ready: Callable[[str, str], tuple[bool, str]] | None = None,
    list_models: Callable[[], list[str]] | None = None,
    agy_preflight: Callable[[str, str, str], str | None] | None = None,
) -> tuple[str, str] | None:
    """(reason, outcome status) of the first failed check; None when all pass."""
    _doing(state, "проверяю дерево …")
    dirty_status = status_short(repo)
    if dirty_status:
        branch = current_branch(repo)
        if branch.startswith("tero/"):
            hint = "dirty tero branch; run: git reset --hard && git clean -fd"
        else:
            hint = "dirty worktree; commit or stash before running tero go"
        return _dirty_reason(hint, dirty_status), "error"

    try:
        ensure_clean(repo)
    except DirtyWorktreeError:
        return _dirty_reason("dirty worktree", status_short(repo)), "error"

    _doing(state, "проверяю провайдеров …")
    try:
        _validate_providers(cfg)
    except UnknownProviderError as exc:
        return str(exc), "error"

    try:
        check_free_models(cfg, list_models or free_models.list_models)
    except ConfigError as exc:
        return str(exc), "error"

    same_family = _same_family_as_judge(cfg)
    if same_family:
        return same_family, "error"

    if check_ready:
        print("проверка провайдеров …", flush=True)
        # without a scoped runner the tester is never called (phase.py tester_enabled),
        # so its CLI must not block the run (v4 BUG-005)
        command = detect(repo, test_command=cfg.test_command,
                         test_command_scoped=cfg.test_command_scoped)
        scoped = bool(command and command.run_files)
        for role, provider, model in _roles(cfg):
            if role == "tester" and not scoped:
                continue
            ok, why = check_ready(provider, model)
            if not ok:
                return f"{role} provider ({provider}) not ready: {why}", "error"

    if agy_preflight:
        checked: set[str] = set()
        for role, model in _agy_roles(cfg):
            if model in checked:
                continue
            checked.add(model)
            preflight_error = agy_preflight(role, model, cfg.working_dir)
            if preflight_error is not None:
                return preflight_error, "stopped"
    return None


def start_gate(cfg: Config, repo: Path, command: TestCommand) -> bool:
    """The start-of-run test run; False — red, the run does not start."""
    cmd_str = " ".join(command.run_all)
    detected = "" if cfg.test_command.strip() else f" (автодетект: {command.name})"
    print(f"стартовый прогон тестов: {cmd_str}{detected} …", flush=True)
    gate = run_all(command, repo=repo, timeout_s=cfg.test_timeout_s)
    if not gate.ok:
        print(f"стартовый прогон тестов: КРАСНЫЕ (код {gate.exit_code})", flush=True)
        print_red_tail(gate.output)
        return False
    return True


def _dirty_reason(hint: str, status: str) -> str:
    """Dirty-worktree reason: starts with 'dirty' so string-matching callers
    still work, but carries the hint and up to 10 status lines for the console."""
    lines = status.splitlines()[:10]
    body = "\n".join(f"    {line}" for line in lines)
    return f"dirty: {hint}" + (f"\n{body}" if body else "")


def _same_family_as_judge(cfg: Config) -> str:
    """Why the judge shares a family with a model that writes code, or ""."""
    judge = f"{cfg.judge_provider}:{cfg.judge_model}"
    judge_family = family_key(judge)
    for role, provider, model in _roles(cfg):
        if role not in ("player", "escalation"):
            continue
        if family_key(f"{provider}:{model}") == judge_family:
            # escalation swaps the player mid-phase, so it counts as the player
            return (
                f"судья из семьи исполнителя ({judge_family}): "
                f"{role} {provider}:{model}, judge {judge}"
            )
    return ""


def _validate_providers(cfg: Config) -> None:
    for _role, provider, _model in _roles(cfg):
        if provider:
            normalize_provider(provider)


def _roles(cfg: Config) -> list[tuple[str, str, str]]:
    rows = [
        ("player", cfg.player_provider, cfg.player_model),
        ("judge", cfg.judge_provider, cfg.judge_model),
    ]
    if not cfg.no_tester:
        rows.append(("tester", cfg.tester_provider, cfg.tester_model))
    if cfg.escalation_provider:
        rows.append(("escalation", cfg.escalation_provider, cfg.escalation_model))
    return rows


def _agy_roles(cfg: Config) -> list[tuple[str, str]]:
    """Роли, идущие через `agy` (включая алиас `gemini`), до первой фазы."""
    return [
        (role, model)
        for role, provider, model in _roles(cfg)
        if provider in ("agy", "gemini")
    ]
