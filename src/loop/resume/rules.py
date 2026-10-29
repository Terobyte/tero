"""Resume refusal rules shared by run/resume/cli (extracted from session.py)."""

from __future__ import annotations

from src.config import Config
from src.loop.state import Status


_STOPPED_RESUME_REASONS = frozenset({
    "user", "stuck", "fuse",
    "provider_unavailable", "provider_auth", "provider_quota",
})

_CRASH_RESUME_STATUSES = frozenset({
    "starting", "running",
    "waiting_human", "waiting_agent", "waiting_limit",
})


def resume_refusal(state: dict, answer: str = "", force: bool = False) -> str:
    """Why resume refuses ("" means it can continue). Single copy of the rule."""
    status = state.get("status") or ""
    if status == Status.JUDGE_PENDING.value:
        saved = state.get("config")
        if not isinstance(saved, dict) or not saved:
            return "прогон старше resume, запусти `tero go`"
        if not (state.get("snapshot_ref") or "") or not (state.get("base") or ""):
            return "прогон старше resume, запусти `tero go`"
        return ""
    if status in _CRASH_RESUME_STATUSES:
        saved = state.get("config")
        if not isinstance(saved, dict) or not saved:
            return "прогон старше resume, запусти `tero go`"
        return ""
    if status != Status.STOPPED.value:
        return f"resume для `{status}` пока не умеет"
    saved = state.get("config")
    if not isinstance(saved, dict) or not saved:
        return "прогон старше resume, запусти `tero go`"
    reason = state.get("reason") or ""
    if not (state.get("snapshot_ref") or "") or not (state.get("base") or ""):
        return "снимка нет, запусти `tero go`"
    if reason == "secrets":
        return "стоп по секретам: разберись с файлами и запусти `tero go`"
    if reason not in _STOPPED_RESUME_REASONS:
        return f"resume для причины `{reason}` не умеет"
    if reason == "user" and not (answer or "").strip():
        q = state.get("text") or ""
        return f"прогон ждал ответа: `tero resume --answer \"…\"`\n{q}"
    return ""


def _cfg_from_state_fields(saved: dict) -> Config:
    """Config from the snapshot in state.json. The snapshot lies in the repo, so
    the global-only fields (wake_command, host, phone_text_answers) are taken
    from ~/.tero/config.yaml instead (спек пульта 2, приёмка 10)."""
    import dataclasses

    from src.config_env import GLOBAL_ONLY, global_defaults

    fields = saved.get("fields")
    source = fields if isinstance(fields, dict) else saved
    valid = {f.name for f in dataclasses.fields(Config)}
    clean = {k: v for k, v in source.items() if k in valid and k not in GLOBAL_ONLY}
    base = Config()
    for key, value in global_defaults().items():
        if key in GLOBAL_ONLY and key in valid and value is not None:
            fallback = getattr(base, key)
            if isinstance(fallback, bool) and isinstance(value, str):
                value = value.strip().lower() in ("true", "1", "yes")
            clean[key] = type(fallback)(value) if not isinstance(fallback, bool) else bool(value)
    return Config(**clean)


def _resume_config(state: dict, answer: str = "", force: bool = False) -> tuple[Config | None, str]:
    """The saved config of a resumable run, or (None, why resume refuses)."""
    refusal = resume_refusal(state, answer, force)
    if refusal:
        return None, refusal
    saved = state.get("config")
    assert isinstance(saved, dict) and saved
    try:
        cfg = _cfg_from_state_fields(saved)
    except (TypeError, ValueError) as exc:
        return None, f"прогон старше resume, запусти `tero go` ({exc})"
    return cfg, ""
