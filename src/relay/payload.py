"""Pieces of the relay payload that come from outside state.json (план 08-6)."""

from __future__ import annotations

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
