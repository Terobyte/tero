"""Start a run, walk phases, stop with the spec exit codes."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.config import (
    Config,
    ConfigError,
    UnknownProviderError,
    family_key,
    load_provider_configs,
    normalize_provider,
)
from src.hud.app import maybe_launch_hud
from src.hud.registry import Registry, current_pid_started
from src.loop.free import check_free_models
from src.loop.gates import detect, print_red_tail, run_all
from src.loop.git import (
    DirtyWorktreeError,
    checkout_run_branch,
    current_branch,
    ensure_clean,
    exclude_tero,
    is_repo,
    run_branch_exists,
    status_short,
)
from src.loop.ledger import Ledger, new_run_dir
from src.loop.lock import acquire
from src.loop.phase import AgentRunner, PhaseOutcome, run_phase
from src.loop.plan import Phase, load_phases, load_preamble
from src.loop.state import RunState, Status
from src.providers import free_models

_SLUG = re.compile(r"[^a-zA-Z0-9]+")


@dataclass
class RunOutcome:
    status: str
    exit_code: int
    reason: str = ""
    phases_shipped: int = 0
    phases_total: int = 0
    run_dir: str = ""


async def run(
    cfg: Config,
    *,
    agents: AgentRunner,
    check_ready: Callable[[str, str], tuple[bool, str]] | None = None,
    list_models: Callable[[], list[str]] | None = None,
    agy_preflight: Callable[[str, str, str], str | None] | None = None,
) -> RunOutcome:
    repo = Path(cfg.working_dir).expanduser().resolve()
    if not is_repo(repo):
        return RunOutcome(status="error", exit_code=1, reason="not a git repo")

    # exclude_tero() before any write into .tero/ (incl. run.lock, state.json):
    # a fresh clone without ".tero/" in exclude would otherwise read
    # "?? .tero/" as a dirty tree on every start.
    exclude_tero(repo)
    lock = acquire(repo)
    if lock is None:
        return RunOutcome(
            status="error", exit_code=1, reason="another run holds this repo"
        )

    # State dir exists before the dirty-tree check, so a failed start can
    # leave state.json=stopped for the HUD while history() still skips it
    # (history only lists dirs with ledger.jsonl, created below on success).
    slug = slugify(Path(cfg.plan_file).stem)
    run_dir = new_run_dir(repo, slug)
    state = RunState(run_dir / "state.json")
    pid_started = current_pid_started()
    registry = Registry()
    try:
        registry.register(run_dir.name, run_dir / "state.json",
                           pid=os.getpid(), pid_started=pid_started)
    except Exception:
        pass
    from src.loop import pulse as pulse_mod

    try:
        state.update(
            status=Status.STARTING.value,
            pid=os.getpid(),
            pid_started=pid_started,
            plan=cfg.plan_file,
            doing="старт",
            config=_public_config(cfg),
            hang_after_s=max(
                cfg.player_timeout_s,
                cfg.judge_timeout_s,
                cfg.tester_timeout_s,
                cfg.test_timeout_s,
                cfg.stall_timeout_s,
            )
            + 300,
        )
    except Exception:
        pass
    pulse = None
    try:
        pulse = pulse_mod.Pulse(run_dir)
    except Exception:
        # F4: a ticker that never started must surface — but without
        # leaking the repo lock or the registry entry.
        try:
            lock.release()
        except Exception:
            pass
        try:
            registry.unregister(run_dir.name)
        except Exception:
            pass
        raise
    try:
        maybe_launch_hud(cfg, run_dir)

        def _fail_start(reason: str, doing: str = "", status: str = "error") -> RunOutcome:
            try:
                state.update(
                    status=Status.STOPPED.value,
                    reason=reason,
                    text=reason,
                    doing=doing or "стоп",
                )
            except Exception:
                pass
            try:
                lock.release()
            except Exception:
                pass
            return RunOutcome(status=status, exit_code=1, reason=reason)

        try:
            state.update(doing="проверяю дерево …")
        except Exception:
            pass
        dirty_status = status_short(repo)
        if dirty_status:
            branch = current_branch(repo)
            if branch.startswith("tero/"):
                hint = "dirty tero branch; run: git reset --hard && git clean -fd"
            else:
                hint = "dirty worktree; commit or stash before running tero go"
            return _fail_start(_dirty_reason(hint, dirty_status))

        try:
            ensure_clean(repo)
        except DirtyWorktreeError:
            return _fail_start(_dirty_reason("dirty worktree", status_short(repo)))

        try:
            state.update(doing="проверяю провайдеров …")
        except Exception:
            pass
        try:
            _validate_providers(cfg)
        except UnknownProviderError as exc:
            return _fail_start(str(exc))

        try:
            check_free_models(cfg, list_models or free_models.list_models)
        except ConfigError as exc:
            return _fail_start(str(exc))

        same_family = _same_family_as_judge(cfg)
        if same_family:
            return _fail_start(same_family)

        if check_ready:
            print("проверка провайдеров …", flush=True)
            for role, provider, model in _roles(cfg):
                ok, why = check_ready(provider, model)
                if not ok:
                    return _fail_start(f"{role} provider ({provider}) not ready: {why}")

        if agy_preflight:
            checked: set[str] = set()
            for role, model in _agy_roles(cfg):
                if model in checked:
                    continue
                checked.add(model)
                preflight_error = agy_preflight(role, model, cfg.working_dir)
                if preflight_error is not None:
                    return _fail_start(preflight_error, status="stopped")

        try:
            state.update(doing="проверяю план …")
        except Exception:
            pass
        plan_path = repo / cfg.plan_file
        if not plan_path.is_file():
            return _fail_start(f"plan not found: {plan_path}")

        if run_branch_exists(repo, slug):
            # Resume: the [x] marks live on the run branch, not on the branch we started from.
            checkout_run_branch(repo, slug)

        phases = load_phases(plan_path)
        if not any(phase.checkboxes for phase in phases):
            return _fail_start(f"no '- [ ]' checkboxes found in {plan_path}")
        if all(phase.done for phase in phases):
            try:
                lock.release()
            except Exception:
                pass
            try:
                shutil.rmtree(run_dir, ignore_errors=True)
                run_dir.parent.rmdir()
            except Exception:
                pass
            # No branch, no run dir, no test gate: there is nothing to judge.
            return RunOutcome(
                status="ok",
                exit_code=0,
                reason="nothing_to_do",
                phases_total=len(phases),
            )

        try:
            state.update(doing="стартовые тесты …")
        except Exception:
            pass
        command = detect(
            repo,
            test_command=cfg.test_command,
            test_command_scoped=cfg.test_command_scoped,
        )
        if command is not None:
            cmd_str = " ".join(command.run_all)
            detected = "" if cfg.test_command.strip() else f" (автодетект: {command.name})"
            print(f"стартовый прогон тестов: {cmd_str}{detected} …", flush=True)
            gate = run_all(command, repo=repo, timeout_s=cfg.test_timeout_s)
            if not gate.ok:
                print(f"стартовый прогон тестов: КРАСНЫЕ (код {gate.exit_code})", flush=True)
                print_red_tail(gate.output)
                return _fail_start("red_start")

        checkout_run_branch(repo, slug)
        try:
            keep = _public_config(cfg)
            state.update(
                status=Status.RUNNING.value, doing="фаза 1",
                config=keep,
            )
        except Exception:
            pass
        ledger = Ledger(run_dir)
        ledger.event("run_start", plan=cfg.plan_file, slug=slug)
        ledger.event(
            "compact_config",
            player_at_tokens=cfg.compact_player_at_tokens,
            judge_at_tokens=cfg.compact_judge_at_tokens,
        )

        preamble = load_preamble(plan_path)
        shipped = 0

        def _end(reason: str) -> None:
            ledger.event(
                "run_end",
                reason=reason,
                phases_shipped=shipped,
                phases_total=len(phases),
            )

        try:
            for phase in phases:
                if phase.done:
                    continue
                outcome = await run_phase(
                    phase,
                    repo=repo,
                    plan_path=plan_path,
                    preamble=preamble,
                    cfg=cfg,
                    agents=agents,
                    test_command=command,
                    ledger=ledger,
                    total_phases=len(phases),
                    pulse=pulse,
                )
                _print_phase_summary(phase, outcome, total=len(phases))
                if outcome.status == "ship":
                    shipped += 1
                    continue
                if outcome.status == "judge_pending":
                    _end(outcome.reason)
                    try:
                        lock.release()
                    except Exception:
                        pass
                    return RunOutcome(
                        status="judge_pending",
                        exit_code=3,
                        reason=outcome.reason,
                        phases_shipped=shipped,
                        phases_total=len(phases),
                        run_dir=str(run_dir),
                    )
                _end(outcome.reason)
                try:
                    state.update(
                        status=Status.STOPPED.value,
                        reason=outcome.reason,
                        text=outcome.stop_text or outcome.reason,
                        doing="стоп",
                    )
                except Exception:
                    pass
                try:
                    lock.release()
                except Exception:
                    pass
                return RunOutcome(
                    status="stop",
                    exit_code=2,
                    reason=outcome.reason,
                    phases_shipped=shipped,
                    phases_total=len(phases),
                    run_dir=str(run_dir),
                )
        except (KeyboardInterrupt, asyncio.CancelledError) as exc:
            # Under asyncio.run, Ctrl+C reaches this coroutine as CancelledError.
            ledger.event("interrupted")
            _end("interrupted")
            try:
                state.update(
                    status=Status.STOPPED.value,
                    reason="interrupted",
                    text="interrupted",
                    doing="стоп",
                )
            except Exception:
                pass
            try:
                lock.release()
            except Exception:
                pass
            if isinstance(exc, asyncio.CancelledError):
                raise
            return RunOutcome(
                status="interrupted",
                exit_code=130,
                reason="interrupted",
                phases_shipped=shipped,
                phases_total=len(phases),
                run_dir=str(run_dir),
            )

        _end("ship")
        try:
            state.update(status=Status.DONE.value, doing="стоп")
        except Exception:
            pass
        try:
            lock.release()
        except Exception:
            pass
        return RunOutcome(
            status="ok",
            exit_code=0,
            reason="ship",
            phases_shipped=shipped,
            phases_total=len(phases),
            run_dir=str(run_dir),
        )
    finally:
        try:
            if pulse is not None:
                pulse.stop()
        except Exception:
            pass
        try:
            registry.unregister(run_dir.name)
        except Exception:
            pass


def _is_secret_name(name: str) -> bool:
    folded = name.lower()
    return "key" in folded or "token" in folded or "secret" in folded


def _public_config(cfg: Config) -> dict:
    return {
        f.name: getattr(cfg, f.name)
        for f in dataclasses.fields(Config)
        if not _is_secret_name(f.name)
    }


def _cfg_from_state_fields(saved: dict) -> Config:
    fields = saved.get("fields")
    source = fields if isinstance(fields, dict) else saved
    valid = {f.name for f in dataclasses.fields(Config)}
    clean = {k: v for k, v in source.items() if k in valid}
    return Config(**clean)


async def resume(
    repo: str | Path,
    run_dir: str | Path,
    *,
    agents: AgentRunner,
) -> RunOutcome:
    """Continue a stopped run from its state.json. Only judge_pending yet."""
    from src.loop import snapshot as snapshot_mod
    from src.loop.git import head as git_head

    repo_path = Path(repo).expanduser().resolve()
    run_path = Path(run_dir).expanduser().resolve()
    state = RunState.read(run_path / "state.json") or {}
    status = state.get("status") or ""

    def _refuse(reason: str) -> RunOutcome:
        print(f"стоп: {reason} (код 1)", flush=True)
        return RunOutcome(
            status="error", exit_code=1, reason=reason, run_dir=str(run_path)
        )

    if status != Status.JUDGE_PENDING.value:
        return _refuse(f"resume для `{status}` пока не умеет")
    saved = state.get("config")
    if not isinstance(saved, dict) or not saved:
        return _refuse("прогон старше resume, запусти `tero go`")
    try:
        cfg = _cfg_from_state_fields(saved)
    except (TypeError, ValueError) as exc:
        return _refuse(f"прогон старше resume, запусти `tero go` ({exc})")

    ref = state.get("snapshot_ref") or ""
    base = state.get("base") or ""
    phase_title = state.get("phase") or ""
    phase_index = state.get("phase_index", 0) or 0
    round_no = state.get("round", 1) or 1
    if not ref or not base:
        return _refuse("прогон старше resume, запусти `tero go`")

    lock = acquire(repo_path)
    if lock is None:
        return _refuse("another run holds this repo")
    from src.loop import pulse as pulse_mod

    from src.loop.stream import Stream

    done_io = RunState(run_path / "state.json")
    stream = Stream(run_path / "stream.jsonl")
    try:
        saved_hang = state.get("hang_after_s")
        if not isinstance(saved_hang, (int, float)):
            done_io.update(
                hang_after_s=max(
                    cfg.player_timeout_s,
                    cfg.judge_timeout_s,
                    cfg.tester_timeout_s,
                    cfg.test_timeout_s,
                    cfg.stall_timeout_s,
                )
                + 300,
            )
    except Exception:
        pass
    # F5: resume owns the run again via the shared status helper — the
    # starting transition is recorded in state.json, stream.jsonl and
    # ledger.jsonl like every other status change, so a setup failure or
    # early return below can restore judge_pending and a retry stays
    # possible.
    resume_pulse: Any = None
    slug = slugify(Path(cfg.plan_file).stem)
    registry = Registry()
    pid_started = current_pid_started()
    try:
        registry.register(
            run_path.name, run_path / "state.json",
            pid=os.getpid(), pid_started=pid_started,
        )
    except Exception:
        pass

    ledger: Ledger | None = None

    def _ledger() -> Ledger:
        nonlocal ledger
        if ledger is None:
            ledger = Ledger(run_path)
        return ledger

    def _write_status_transition(
        *, status: str, reason: str, text: str, round_no: int = 0,
        run_end: bool = False,
    ) -> None:
        done_io.update(
            status=status, reason=reason, text=text,
            doing="стоп" if status != Status.RUNNING.value else "resume",
            pid=os.getpid(),
            pid_started=pid_started,
        )
        try:
            stream.emit(
                "resume" if status == Status.RUNNING.value else "stop",
                status=status, reason=reason,
                phase=phase_title, round=round_no, role="judge",
                text=text,
            )
        except Exception:
            pass
        try:
            kind = (
                "resume" if status == Status.RUNNING.value else (
                    "done" if status == Status.DONE.value else "stop"
                )
            )
            _ledger().event(
                kind,
                status=status, reason=reason, text=text, round=round_no,
            )
        except Exception:
            pass
        if run_end:
            try:
                _ledger().event("run_end", reason=reason)
            except Exception:
                pass

    try:
        _write_status_transition(
            status=Status.STARTING.value, reason="resume",
            text="resume: старт", round_no=round_no,
        )
    except Exception:
        pass

    # F6: pulse startup and all subsequent setup live inside one
    # try/finally that always stops the pulse and releases the lock,
    # so a setup failure cannot leak the daemon ticker thread.
    # F5: a Pulse startup failure also restores judge_pending first,
    # so the retry stays possible (the starting transition above is
    # already recorded in state.json, stream.jsonl and ledger.jsonl).
    # Return without entering the phase loop: restore judge_pending so a
    # fixed setup can be retried. Any other unexpected failure inside the
    # whole resume body below (including a Ledger init failure or a crash
    # in the judge loop) also restores judge_pending; the pulse still
    # stops and the lock still releases via the outer finally.
    def _restore_pending() -> None:
        try:
            _write_status_transition(
                status=Status.JUDGE_PENDING.value, reason="resume_setup_failed",
                text="resume: настройка не удалась, повтор возможен",
                round_no=round_no,
            )
        except Exception:
            pass

    try:
        try:
            resume_pulse = pulse_mod.Pulse(run_path)
        except Exception:
            _restore_pending()
            raise
        # F14: статус running — только после успешной настройки, иначе
        # любая ошибка checkout/восстановления оставляет judge_pending
        # и resume можно повторить.
        try:
            # F16: существующий before-resume не перезаписываем — повторные попытки
            # сохраняют своё дерево в before-resume-2, -3, …, чтобы ручные правки
            # первой попытки оставались доступны.
            def _before_ref() -> str:
                base_ref = f"refs/tero/{run_path.name}/before-resume"
                if not snapshot_mod.verify(repo_path, base_ref):
                    return base_ref
                n = 2
                while snapshot_mod.verify(repo_path, f"{base_ref}-{n}"):
                    n += 1
                return f"{base_ref}-{n}"

            before = _before_ref()
            before_saved = False
            if snapshot_mod.tree_of(repo_path, base) != snapshot_mod.ref_tree(repo_path, ref):
                snapshot_mod.take_snapshot(repo_path, git_head(repo_path), before)
                before_saved = True
            # Конфликтующие ручные правки не останавливают resume: before-resume
            # уже сохранён, поэтому checkout идёт с -f, а дерево выравнивается
            # на ref — судья получает код из ref.
            _force_checkout_run_branch(repo_path, slug)
            if snapshot_mod.tree_of(repo_path, base) != snapshot_mod.ref_tree(repo_path, ref):
                if not before_saved:
                    before = _before_ref()
                    snapshot_mod.take_snapshot(repo_path, git_head(repo_path), before)
                    before_saved = True
                snapshot_mod.restore_snapshot(repo_path, ref)

            plan_path = repo_path / cfg.plan_file
            if not plan_path.is_file():
                _restore_pending()
                return _refuse(f"plan not found: {plan_path}")
            preamble = load_preamble(plan_path)
            phases = load_phases(plan_path)
            phase = next(
                (p for p in phases if p.index == phase_index or p.title == phase_title),
                None,
            )
            if phase is None:
                _restore_pending()
                return _refuse(f"фаза не найдена: {phase_title or phase_index}")

            command = detect(
                repo_path,
                test_command=cfg.test_command,
                test_command_scoped=cfg.test_command_scoped,
            )
            # F8: resume owns the run again — running from the start, so a later
            # player snapshot can never be mistaken for a gate-passed ref.
            # judge_pending returns only via the judge failing again below.
            _write_status_transition(
                status=Status.RUNNING.value, reason="resume",
                text=f"resume: продолжаю с судьи (раунд {round_no})",
                round_no=round_no,
            )
            _ledger().event("resume", status=status, round=round_no, ref=ref)
            shipped = sum(1 for p in phases if p.done)
            start_index = next(
                (i for i, p in enumerate(phases) if p.index == phase.index),
                phase.index,
            )

            def _end(reason: str) -> None:
                _ledger().event(
                    "run_end",
                    reason=reason,
                    phases_shipped=shipped,
                    phases_total=len(phases),
                )

            index = start_index
            first = True
            outcome = None
            while index < len(phases):
                current = phases[index]
                if current.done:
                    index += 1
                    continue
                first_kwargs = (
                    {"start_checkpoint": base, "start_round": round_no}
                    if first else {}
                )
                first = False
                outcome = await run_phase(
                    current,
                    repo=repo_path,
                    plan_path=plan_path,
                    preamble=preamble,
                    cfg=cfg,
                    agents=agents,
                    test_command=command,
                    ledger=_ledger(),
                    total_phases=len(phases),
                    start_with_judge=(current.index == phase.index),
                    pulse=resume_pulse,
                    **first_kwargs,
                )
                _print_phase_summary(current, outcome, total=len(phases))
                if outcome.status == "ship":
                    shipped += 1
                    index += 1
                    continue
                if outcome.status == "judge_pending":
                    _end(outcome.reason)
                    return RunOutcome(
                        status="judge_pending", exit_code=3, reason=outcome.reason,
                        phases_shipped=shipped,
                        phases_total=len(phases), run_dir=str(run_path),
                    )
                _end(outcome.reason)
                _write_status_transition(
                    status=Status.STOPPED.value,
                    reason=outcome.reason,
                    text=outcome.stop_text or outcome.reason,
                    round_no=round_no,
                )
                return RunOutcome(
                    status="stop", exit_code=2, reason=outcome.reason,
                    phases_shipped=shipped,
                    phases_total=len(phases), run_dir=str(run_path),
                )
            _write_status_transition(
                status=Status.DONE.value, reason="ship", text="ship",
                round_no=round_no, run_end=True,
            )
            return RunOutcome(
                status="ok", exit_code=0, reason="ship",
                phases_shipped=shipped, phases_total=len(phases),
                run_dir=str(run_path),
            )
        except Exception:
            _restore_pending()
            raise
    finally:
        try:
            if resume_pulse is not None:
                resume_pulse.stop()
        except Exception:
            pass
        try:
            lock.release()
        except Exception:
            pass
        try:
            registry.unregister(run_path.name)
        except Exception:
            pass


def _force_checkout_run_branch(repo: Path, slug: str) -> str:
    """Вернуться на ветку прогона, не теряя незакоммиченную работу.

    before-resume уже снят до вызова, поэтому конфликтующие правки можно
    сбросить: checkout идёт с `-f`, дерево дальше выравнивается на ref.
    Новую ветку (её ещё нет) создаём как обычно.
    """
    name = slug if slug.startswith("tero/") else f"tero/{slug}"
    if run_branch_exists(repo, slug):
        _force_checkout(repo, name)
    else:
        checkout_run_branch(repo, slug)
    return name


def _force_checkout(repo: Path, name: str) -> None:
    import subprocess

    from src.loop.git import GitError

    try:
        proc = subprocess.run(
            ["git", "checkout", "-f", name],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise GitError(f"git checkout -f {name} failed: {exc}") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise GitError(f"git checkout -f {name} failed: {err}")


def _dirty_reason(hint: str, status: str) -> str:
    """Dirty-worktree reason: starts with 'dirty' so string-matching callers
    still work, but carries the hint and up to 10 status lines for the console."""
    lines = status.splitlines()[:10]
    body = "\n".join(f"    {line}" for line in lines)
    return f"dirty: {hint}" + (f"\n{body}" if body else "")


def rounds_word(rounds: int) -> str:
    if 11 <= (rounds % 100) <= 14:
        return "раундов"
    last = rounds % 10
    if last == 1:
        return "раунд"
    if 2 <= last <= 4:
        return "раунда"
    return "раундов"


def phase_summary_line(phase: Phase, outcome: PhaseOutcome, *, total: int) -> str:
    rounds = outcome.rounds
    text = f"фаза {phase.index + 1}/{total} «{phase.title}»: "
    if outcome.status == "ship":
        sha = (outcome.commit_sha or "")[:7]
        return f"{text}SHIP за {rounds} {rounds_word(rounds)} → {sha}"
    if outcome.status == "judge_pending":
        return (
            f"{text}JUDGE_PENDING ({outcome.reason}) "
            f"за {rounds} {rounds_word(rounds)}"
        )
    return f"{text}STOP ({outcome.reason}) за {rounds} {rounds_word(rounds)}"


def _print_phase_summary(phase: Phase, outcome: PhaseOutcome, *, total: int) -> None:
    print(phase_summary_line(phase, outcome, total=total), flush=True)


def slugify(name: str) -> str:
    text = _SLUG.sub("-", name).strip("-").lower()
    return text or "plan"


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
        ("tester", cfg.tester_provider, cfg.tester_model),
    ]
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


def agy_preflight_check(role: str, model: str, working_dir: str) -> str | None:
    """Pre-flight `agy` до очереди: список моделей, короткий вызов.

    Возвращает текст стопа или None, когда прогон идёт дальше.
    """
    from src.providers import agy as agy_mod

    try:
        configs = load_provider_configs(working_dir)
    except Exception:
        configs = {}
    entry = dict(configs.get("agy") or {})
    command = entry.get("command", "agy")
    model = agy_mod._resolve_model(
        model or entry.get("default_model") or agy_mod.AgyConfig.default_model
    )
    try:
        listed = agy_mod.list_models(command)
    except Exception:
        listed = []
    if listed and model and model not in listed:
        print(f"`agy`: модели `{model}` нет в `agy models`, прогон идёт", flush=True)
    try:
        ok, detail = agy_mod.preflight(
            command, model, timeout_s=agy_mod.AGY_PREFLIGHT_TIMEOUT_S
        )
    except Exception as exc:
        ok, detail = False, f"`agy` не отвечает: `{exc}`"
    if not ok:
        return (
            f"{role} provider (agy) not ready: {detail}. "
            "Войди: запусти `agy` в терминале"
        )
    return None
