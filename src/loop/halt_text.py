"""Тексты стопа и вопроса точки решения по причине."""

from __future__ import annotations

from typing import Any

from src.loop.phase_ctx import PhaseContext


def _legacy_halt_text(ctx: PhaseContext, reason: str, limit: Any, ref: str | None) -> str:
    """Тексты стопа при decisions=False — дословно как до точек решения."""
    st = ctx.st
    if reason == "user":
        return st.user_question or reason
    if reason in ("provider_auth", "provider_quota"):
        return ctx.provider_text("", st.last_provider or "?")
    if reason == "provider_unavailable" and st.last_provider_kind in (
        "timeout",
        "stall",
        "auth",
        "quota",
    ):
        return st.last_provider_error or reason
    tail = f", работа в `{ref}`" if ref else ""
    texts = {
        "stuck": f"нет прогресса → откат фазы{tail}",
        "fuse": f"лимит раундов ({limit}) исчерпан → откат фазы{tail}",
        "provider_unavailable": f"провайдер недоступен → откат фазы{tail}",
        "secrets": f"в дифе файлы-секреты → откат фазы{tail}",
    }
    return texts.get(reason, reason)


def _halt_text_fallback(ctx: PhaseContext, reason: str, ref: str | None = None) -> str:
    try:
        n = ctx.phase.index + 1
    except Exception:
        n = 1
    if reason == "user":
        return ctx.st.user_question or reason
    if reason in ("provider_auth", "provider_quota"):
        return ctx.provider_text("", ctx.st.last_provider or "?")
    if reason == "provider_unavailable":
        kind = getattr(ctx.st, "last_provider_kind", "") or ""
        if kind in ("timeout", "stall", "auth", "quota"):
            return ctx.st.last_provider_error or reason
        prov = getattr(ctx.st, "last_provider", "") or "?"
        err = getattr(ctx.st, "last_provider_error", "") or ""
        base = f"провайдер {prov} не отвечает"
        if err:
            base += f" ({err})"
        return base
    if reason == "stuck":
        try:
            finds = [f.as_prompt() for f in (ctx.st.open_findings or [])[-5:]]
        except Exception:
            finds = []
        tail = f": {'; '.join(finds)}" if finds else ""
        text = f"фаза {n} застряла{tail}"
        if ref:
            text += f", работа в `{ref}`"
        return text
    if reason == "fuse":
        try:
            lim = ctx.cfg.max_rounds
        except Exception:
            lim = "?"
        text = f"фаза {n}: {lim} раундов без SHIP"
        if ref:
            text += f", работа в `{ref}`"
        return text
    if reason == "secrets":
        try:
            from src.loop.git import secret_paths as _sp

            paths = _sp(ctx.repo)
        except Exception:
            paths = []
        tail = f": {', '.join(paths)}" if paths else ""
        text = f"в дифе файлы-секреты{tail}"
        if ref:
            text += f", работа в `{ref}`"
        return text
    tail = f", работа в `{ref}`" if ref else ""
    return f"{reason}{tail}"
