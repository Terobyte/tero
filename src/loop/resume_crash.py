"""Crash-resume branching for run.resume: one dispatch call."""

from __future__ import annotations

import time
from pathlib import Path

from src.loop.resume_lock import check_index_lock  # noqa: F401


def crash_tree_of(repo: str | Path, base: str) -> str:
    from src.loop import snapshot as sm

    return sm.tree_of(Path(repo), base)


def crash_phase_shipped(repo: str | Path, base: str, run_id: str, n: int) -> bool:
    from src.loop.git import phase_shipped as _shipped

    return bool(_shipped(repo, base, run_id, n))


def _before_ref(run_name: str) -> str:
    return f"refs/tero/{run_name}/before-resume"


def _snapshot_dirty(repo_path: str | Path, run_name: str) -> None:
    """Save the dirty worktree to a before-resume ref ahead of any rollback."""
    if not run_name:
        raise ValueError("cannot back up worktree without a run id")
    from src.loop import snapshot as sm
    from src.loop.git import head as _head

    base_ref, k = _before_ref(run_name), 1
    while sm.verify(repo_path, base_ref if k == 1 else f"{base_ref}-{k}"):
        k += 1
    ref = base_ref if k == 1 else f"{base_ref}-{k}"
    sm.take_snapshot(repo_path, _head(repo_path), ref)
    if not sm.verify(repo_path, ref):
        raise RuntimeError(f"backup snapshot not found: {ref}")


def crash_decision(state: dict) -> dict:
    """Pure branch choice: no repo, no lock, no writes."""
    status = (state or {}).get("status") or ""
    try:
        idx = int((state or {}).get("phase_index", 0) or 0)
    except (TypeError, ValueError):
        idx = 0
    return {"status": status, "n": idx + 1}


def _slug_of(state: dict) -> str:
    try:
        from src.loop.report import slugify as _slugify

        cfg = (state or {}).get("config") or {}
        fields = cfg.get("fields") if isinstance(cfg.get("fields"), dict) else cfg
        plan = (fields or {}).get("plan_file") or ""
        if plan:
            return _slugify(Path(str(plan)).stem)
    except Exception:
        pass
    return ""


def dispatch(state: dict, *, repo: str | Path | None = None,
             run_id: str = "", force: bool = False,
             now: float | None = None) -> dict:
    """Crash-resume entry: one call branching for run.resume."""
    status = (state or {}).get("status") or ""
    try:
        idx = int((state or {}).get("phase_index", 0) or 0)
    except (TypeError, ValueError):
        idx = 0
    n = idx + 1
    try:
        saved_phase = (state or {}).get("crash_phase_index", idx)
        saved_phase = int(saved_phase)
    except (TypeError, ValueError):
        saved_phase = idx
    crashes = (state or {}).get("crash_resumes", 0) or 0
    try:
        crashes = int(crashes)
    except (TypeError, ValueError):
        crashes = 0
    if saved_phase != idx:
        crashes = 0
    if status in ("done", "stopped", "judge_pending"):
        return {"action": "other", "status": status}
    if crashes >= 2 and not force:
        return {"action": "refuse", "reason": "crash_loop",
                "text": f"фаза {n} падает при каждом запуске"}
    base = (state or {}).get("base") or ""
    q = (state or {}).get("question") if isinstance((state or {}).get("question"), dict) else {}
    if repo is not None and status in ("waiting_human", "waiting_agent", "waiting_limit"):
        try:
            live = crash_tree_of(repo, base) if base else ""
        except Exception:
            live = ""
        if live and (state or {}).get("tree_sha") == live and (q.get("id") or ""):
            return {"action": "waiting", "question": dict(q),
                    "question_id": (state or {}).get("question_id") or q.get("id")}
    if base:
        try:
            shipped = crash_phase_shipped(repo or ".", base, run_id or "", n)
        except Exception:
            shipped = False
        if repo is not None:
            try:
                _snapshot_dirty(repo, run_id or "")
            except Exception as exc:
                return {"action": "refuse", "reason": "snapshot_failed",
                        "text": f"resume: снимок не сделан, дерево не тронуто: {exc}"}
        if shipped:
            return {"action": "shipped"}
        if repo is not None:
            crash_rollback_to_head(repo, _slug_of(state or {}))
    return {"action": "rerun", "crashes": crashes,
            "at": time.time() if now is None else now}


def crash_rollback_to_head(repo: str | Path, slug: str = "") -> None:
    """Reset the run branch to its HEAD only when Git is not locked."""
    from src.loop import git as g

    repo = Path(repo)
    check_index_lock(repo)
    if slug:
        name = slug if str(slug).startswith("tero/") else f"tero/{slug}"
        import subprocess as _sp

        try:
            proc = _sp.run(["git", "rev-parse", name], cwd=repo,
                           capture_output=True, text=True, check=False)
            head = (proc.stdout or "").strip() if proc.returncode == 0 else ""
        except OSError:
            head = ""
        if head:
            g.force_checkout_run_branch(repo, slug)
            g.rollback(repo, head)
            return
    g.rollback(repo, g.head(repo))
