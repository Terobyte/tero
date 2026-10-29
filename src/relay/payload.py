"""Pieces of the relay payload that come from outside state.json (план 08-6)."""

from __future__ import annotations

from typing import Any


def _state_models(state: dict) -> list[str]:
    """Кандидаты для действия model из state.config (без judge).

    Полный список CLI (`opencode models`/`agy models`) здесь недоступен:
    сеть не держит прогон, а вопрос model принимает `model <роль> <спека>`
    свободным текстом — селект лишь подсказывает текущие спеки ролей.
    """
    try:
        cfg = state.get("config")
        if not isinstance(cfg, dict):
            return []
        out: list[str] = []
        for role in ("player", "escalation", "tester"):
            provider = str(cfg.get(f"{role}_provider") or "").strip()
            model = str(cfg.get(f"{role}_model") or "").strip()
            spec = f"{provider}:{model}" if provider and model else (model or provider)
            if spec and spec not in out:
                out.append(spec)
        return out
    except Exception:
        return []


def _recent_run_events(run_id: str, limit: int = 20, run_dir: Any = None) -> list[dict]:
    """Хвост stream.jsonl прогона для журнала пульта.

    Pusher читает только state.json с диска: точный run_dir берём из
    relay.run_dir (его ставит pusher.send_once), имя прогона — запасной путь:
    cwd/<run_id>, затем <repo>/.tero/runs/<run_id>, ~/.tero/runs/<run_id>.
    Ошибки — пустой журнал.
    """
    try:
        from pathlib import Path

        if not isinstance(run_id, str) or not run_id:
            return []
        cands = []
        try:
            if run_dir:
                cands.append(Path(run_dir) / "stream.jsonl")
        except Exception:
            pass
        cands += [Path.cwd() / run_id / "stream.jsonl",
                  Path.cwd() / ".tero" / "runs" / run_id / "stream.jsonl",
                  Path.home() / ".tero" / "runs" / run_id / "stream.jsonl"]
        try:
            repo = None
            for env_name in ("TERO_REPO", "TERO_WORKING_DIR"):
                try:
                    import os

                    raw = os.environ.get(env_name, "")
                    if raw:
                        repo = Path(raw).expanduser()
                        break
                except Exception:
                    continue
            if repo is None and run_dir:
                try:
                    parts = Path(run_dir).expanduser().resolve().parts
                    if ".tero" in parts and "runs" in parts:
                        cut = parts.index(".tero")
                        repo = Path(*parts[:cut])
                except Exception:
                    repo = None
            if repo is not None:
                cands.insert(1, Path(repo) / ".tero" / "runs" / run_id / "stream.jsonl")
        except Exception:
            pass
        lines: list[str] = []
        for cand in cands:
            try:
                lines = cand.read_text(encoding="utf-8").splitlines()[-limit:]
                if lines:
                    break
            except Exception:
                continue
    except Exception:
        return []
    import json

    out: list[dict] = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        at = row.get("ts") or row.get("at") or ""
        text = row.get("text") or row.get("type") or ""
        if text:
            out.append({"at": str(at), "text": str(text)})
    return out
