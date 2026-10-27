"""Запись limits в state.json: только то, что CLI уже сообщил (план 08-9).

Вынесено из agents.py: файл на потолке, это первый пункт чек-листа фазы.
Новые модули не импортируют phase/run/cli_entry; здесь лишь чтение
провайдера и запись limits в уже существующий state.json того же прогона.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def save_limits(run_dir: str | Path | None, provider_name: str,
                 instance: Any = None, kind: str = "") -> None:
    """Обновить limits[provider] в state.json прогона. Не роняет прогон."""
    if not provider_name or not run_dir:
        return
    try:
        from src.loop.state import RunState
        from src.providers.limits import describe, normalize_limits

        state_path = Path(run_dir) / "state.json"
        if not state_path.is_file():
            return
        saved = RunState.read(state_path) or {}
        merged = dict(saved.get("limits") or {})
        merged[provider_name] = describe(provider_name, instance, kind)
        RunState(state_path).update(
            limits=normalize_limits(merged, (provider_name, *merged)))
    except Exception:
        pass


def track(agents: Any) -> tuple[Callable[[], None], Callable[..., None]]:
    """Перехват фабрики на время run_role: запоминает провайдера вызова.

    Возвращает (stop, note): note пишет limits после вызова, stop снимает
    перехват. Провайдер хранится на agents, чтобы не ломать сигнатуры.
    """
    orig_factory = agents.factory
    agents._tracked_provider = None

    def _factory(name: str) -> Any:
        provider = orig_factory(name)
        agents._tracked_provider = provider
        return provider

    agents.factory = _factory  # type: ignore[method-assign]

    def stop() -> None:
        try:
            agents.factory = orig_factory
        except Exception:
            pass

    def note(agents_ref: Any, provider_name: str, failed: bool) -> None:
        try:
            kind = agents_ref._last_kind if failed else ""
            save_limits(agents_ref.run_dir, provider_name,
                        agents_ref._tracked_provider, kind=kind)
        except Exception:
            pass

    return stop, note



