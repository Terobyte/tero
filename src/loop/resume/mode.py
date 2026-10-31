"""`resume` of the model: {mode: ok | needs_text | refused, why} (plan 09, phase 3).

One calculation for the owner (at the move to `stopped`) and for the watchdog
(a run whose owner died, phase 4).
"""

from __future__ import annotations

from pathlib import Path

from src.fsio import read_json_dict

CRASH_LOOP_AT = 2  # same bound as resume/crash.py


def resume_mode(state: dict, *, phone_text: bool | None = None,
                other_run: bool = False) -> dict:
    """What the phone may do with this run. Never raises."""
    try:
        if other_run:
            return {"mode": "refused", "why": "в репо идёт другой прогон"}
        from src.loop.resume.rules import resume_refusal

        refusal = resume_refusal(state, "", False)
        if refusal.startswith("прогон ждал ответа"):
            if phone_text is None:
                from src.config_env import phone_text_allowed

                phone_text = phone_text_allowed()
            if not phone_text:
                return {"mode": "refused", "why": "ответ текстом с телефона выключен"}
            return {"mode": "needs_text", "why": (state.get("text") or "")[:300]}
        if refusal:
            return {"mode": "refused", "why": refusal.split("\n")[0]}
        if (state.get("status") != "stopped"
                and int(state.get("crash_resumes", 0) or 0) >= CRASH_LOOP_AT):
            return {"mode": "refused", "why": "фаза падает при каждом запуске"}
        return {"mode": "ok", "why": ""}
    except Exception:
        return {"mode": "refused", "why": "не удалось посчитать"}


def stopped_resume(run_dir: str | Path, fields: dict) -> dict:
    """`resume_mode` for the state.json of `run_dir` with the new `fields` applied."""
    state = dict(read_json_dict(Path(run_dir) / "state.json") or {})
    state.update(fields)
    return resume_mode(state)
