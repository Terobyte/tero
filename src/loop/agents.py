"""Call a role, extract JSON, validate, repair once."""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from pathlib import Path
from typing import Any, Callable

from src.errors import classify_error, scrub_secrets
from src.loop.activity import _activity
from src.loop.compact import Compacted
from src.loop.prompts import repair_prompt
from src.loop.role_report import extract_json, with_schema
from src.loop.schemas import SCHEMAS, ValidateCtx
from src.loop.write_guard import ReadOnlyLoop, player_guard, watched
from src.process_guard import ProcessGuard

__all__ = ["LiveAgents", "extract_json", "with_schema", "validate_role"]

# Overridable in tests: how long a role can go silent before we print a
# "still working" line, so a slow provider (opencode: 81s to first event,
# codex: 17s reasoning, a judge with no tool calls) never looks hung.
HEARTBEAT_S = 30.0

# Network backoff: pause before each retry, in order; at most len() retries.
# A retry never starts when elapsed + next pause would pass the deadline.
# Overridable in tests (seconds).
RETRY_DELAYS_S = (60, 120, 300, 600, 3600)
RETRY_DEADLINE_S = 7200

_STALL_SECS = re.compile(r"(\d+(?:\.\d+)?)\s*с")


def validate_role(
    role: str,
    data: dict[str, Any] | None,
    ctx: ValidateCtx | None = None,
) -> list[str]:
    if data is None:
        return ["no JSON object in the final message"]
    from src.loop.schemas import validate

    return validate(role, data, ctx)


class LiveAgents:
    """Call a real provider, extract JSON, repair once, then give up."""

    def __init__(
        self,
        working_dir: str,
        factory: Callable[[str], Any] | None = None,
        retry_wait_s: float = 30.0,
        verbose: bool = False,
    ) -> None:
        self.working_dir = working_dir
        self.factory = factory or _default_factory(working_dir)
        self.retry_wait_s = retry_wait_s
        self.verbose = verbose
        self.guard = ProcessGuard()
        self.last_error = ""
        self._last_kind = "other"
        self._last_exc: BaseException | None = None
        self._last_output_ts = 0.0
        self.run_dir: str | None = None
        try:
            from src.loop import stop_flag as _flag

            if _flag.live(Path(working_dir), require_pid=True):
                self.run_dir = str(working_dir)
        except Exception:
            pass

    async def run_role(
        self,
        role: str,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        provider_name = kwargs.get("provider") or ""
        model = kwargs.get("model") or ""
        effort = kwargs.get("effort") or ""
        prompt = kwargs.get("prompt") or ""
        timeout = kwargs.get("timeout") or 600
        ctx = kwargs.get("ctx")
        schema = SCHEMAS[role]
        read_only = role == "judge"
        compact_check = kwargs.get("compact_check")
        compact_at_tokens = kwargs.get("compact_at_tokens", 0) or 0

        guard = player_guard(role, self.working_dir, kwargs.get("emit"))
        emit = guard.emit if guard else kwargs.get("emit")
        session = guard.session if guard else None

        async def call(text_prompt: str) -> str | dict[str, Any]:
            try:
                text = await watched(guard, text_prompt, lambda p: self._call(
                    provider_name, model, with_schema(p, schema), schema, read_only, timeout, role, effort,
                    emit=emit, compact_check=compact_check, compact_at_tokens=compact_at_tokens,
                    session=session,
                ))
            except Compacted as exc:
                return {"__compacted__": True, "tokens": exc.tokens}
            if text is None:
                return {"__unavailable__": True, "error": self.last_error, "kind": self._last_kind}
            return text

        text = await call(prompt)
        if isinstance(text, dict):
            return text
        data = extract_json(text)
        errors = validate_role(role, data, ctx)
        if errors:
            text = await call(repair_prompt(role, prompt, text, errors))
            if isinstance(text, dict):
                return text
            data = extract_json(text)
            errors = validate_role(role, data, ctx)
        if errors and guard is not None and guard.never_wrote():
            return guard.report()
        if errors:
            # The phase logs errors + raw text; without them a stop is unexplainable.
            return {"__invalid__": True, "errors": errors, "raw": text}
        return data

    async def _call(
        self,
        provider_name: str,
        model: str,
        prompt: str,
        schema: dict,
        read_only: bool,
        timeout: int,
        role: str = "",
        effort: str = "",
        emit=None,
        compact_check=None,
        compact_at_tokens: int = 0,
        session: dict[str, Any] | None = None,
    ) -> str | None:
        network_retry = 0
        other_retried = False
        overall_start = _now()
        while True:
            before = self.guard.snapshot_pids()
            from src.loop import watchdog as _w

            _w.note_step_before(before)
            call_start = _now()
            self._last_output_ts = call_start
            heartbeat = asyncio.ensure_future(
                self._heartbeat(role, provider_name, call_start)
            )
            try:
                provider = self.factory(provider_name)
                text = await asyncio.wait_for(
                    _collect_text(
                        provider,
                        prompt=prompt,
                        working_dir=self.working_dir,
                        model=model,
                        output_schema=schema,
                        read_only=read_only,
                        role=role,
                        effort=effort,
                        verbose=self.verbose,
                        on_output=self._mark_output,
                        emit=emit,
                        compact_check=compact_check,
                        compact_at_tokens=compact_at_tokens,
                        session=session,
                    ),
                    timeout=timeout,
                )
                return text
            except (Compacted, ReadOnlyLoop):
                raise
            except Exception as exc:  # provider auth/network
                raw = scrub_secrets(str(exc) or type(exc).__name__)
                self.last_error = raw
                self._last_kind = classify_error(exc, raw)
                self._last_exc = exc
                if stop_requested_flag(self.run_dir or self.working_dir):
                    break
                if self._last_kind in ("timeout", "stall", "auth", "quota"):
                    if self._last_kind in ("timeout", "stall"):
                        self.last_error = self.error_text(role, timeout)
                    print(f"  {self.last_error}", flush=True)
                    break  # timeout/stall/auth/quota never retry here: phase decides
                print(f"  {provider_name} ошибка: {raw}", flush=True)
                if self._last_kind == "network":
                    delays = RETRY_DELAYS_S
                    if network_retry >= len(delays):
                        break
                    wait_s = delays[network_retry]
                    if _now() - overall_start + wait_s > RETRY_DEADLINE_S:
                        break
                    network_retry += 1
                    line = (
                        f"{role}: сеть, повтор {network_retry}/{len(delays)}"
                        f" через {wait_s / 60:g} мин"
                    )
                    print(f"  {line}", flush=True)
                    if emit is not None:
                        emit("retry", role=role, attempt=network_retry,
                             wait_s=wait_s, error=raw, text=line)
                    await asyncio.sleep(wait_s)
                    continue
                if other_retried:
                    break
                other_retried = True
                if self.retry_wait_s:
                    print(f"  повтор через {self.retry_wait_s:.0f} с", flush=True)
                    await asyncio.sleep(self.retry_wait_s)
                continue
            finally:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
                if self.run_dir and stop_requested_flag(self.run_dir):
                    _w._kill_new_processes(_w.step_before())
                else:
                    self.guard.kill_new_processes(before)
                _w.note_step_before(None)
        return None

    def error_text(self, role: str, timeout: float) -> str:
        """Console + halt text for the last failed call."""
        if self._last_kind == "timeout":
            return f"{role} не уложился в таймаут {timeout:g} с"
        if self._last_kind == "stall":
            exc = getattr(self, "_last_exc", None)
            match = _STALL_SECS.search(str(exc) if exc is not None else "")
            stall_s = match.group(1) if match else f"{timeout:g}"
            return f"{role}: нет вывода {stall_s} с, процесс убит"
        return self.last_error

    def _mark_output(self) -> None:
        self._last_output_ts = _now()

    async def _heartbeat(self, role: str, provider_name: str, call_start: float) -> None:
        poll_s = HEARTBEAT_S / 6
        while True:
            await asyncio.sleep(poll_s)
            idle = _now() - self._last_output_ts
            if idle < HEARTBEAT_S:
                continue
            elapsed = int(_now() - call_start)
            minutes, seconds = divmod(elapsed, 60)
            print(
                f"    … {role} {provider_name} работает {minutes}:{seconds:02d}",
                flush=True,
            )
            # Reset the idle marker so silence prints at most once per
            # HEARTBEAT_S, instead of on every poll while still idle.
            self._last_output_ts = _now()


def _now() -> float:
    return time.monotonic()


def stop_requested_flag(run_dir: str | None) -> bool:
    """True when a stop was requested for this run dir (else its `.tero/runs`).

    Never raises.
    """
    try:
        from pathlib import Path

        from src.loop import stop_flag as _flag

        if not run_dir:
            return False
        direct = Path(run_dir)
        if (direct / "inbox").is_dir() or (direct / "state.json").is_file():
            return bool(_flag.live(direct, require_pid=True))
        runs = direct / ".tero" / "runs"
        if not runs.is_dir():
            return False
        return any(
            _flag.live(c, require_pid=True) for c in runs.iterdir())
    except Exception:
        return False


def _default_factory(working_dir: str) -> Callable[[str], Any]:
    def factory(name: str):
        from src.config import Config, load_provider_configs, resolve_config
        from src.providers import create_provider

        configs = load_provider_configs(working_dir)
        entry = dict(configs.get(name) or {})
        provider = create_provider(name, entry if entry else None)
        if getattr(provider, "streams", False):
            stall = entry.get("stall_timeout_s", None)
            if stall is None:
                try:
                    stall = resolve_config({"working_dir": working_dir}).stall_timeout_s
                except Exception:
                    stall = Config().stall_timeout_s
            try:
                provider.config.stall_timeout_s = stall
            except Exception:
                pass
        return provider

    return factory


async def _collect_text(
    provider: Any,
    *,
    prompt: str,
    working_dir: str,
    model: str,
    output_schema: dict,
    read_only: bool,
    role: str = "",
    effort: str = "",
    verbose: bool = False,
    on_output: Callable[[], None] | None = None,
    emit=None,
    compact_check: Callable[[int], bool] | None = None,
    compact_at_tokens: int = 0,
    session: dict[str, Any] | None = None,
) -> str:
    from src.providers.message_adapter import normalize_message

    kwargs: dict[str, Any] = {
        "prompt": prompt,
        "system_prompt": "",
        "working_dir": working_dir,
        "model": model,
    }
    params = inspect.signature(provider.run).parameters
    flexible = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    if "output_schema" in params or flexible:
        kwargs["output_schema"] = output_schema
    if "read_only" in params or flexible:
        kwargs["read_only"] = read_only
    if "effort" in params or flexible:
        kwargs["effort"] = effort
    # emit goes only to providers that declare it explicitly: a **kwargs
    # wrapper (e.g. MuseProvider) would forward it to an inner run() that
    # rejects it with TypeError, failing the role as unavailable.
    if emit is not None and "emit" in params:
        kwargs["emit"] = emit
    if compact_check is not None and "compact_check" in params:
        kwargs["compact_check"] = compact_check
    if "compact_at_tokens" in params:
        kwargs["compact_at_tokens"] = compact_at_tokens
    if session is not None and "session" in params:
        kwargs["session"] = session
    parts: list[str] = []
    stream = provider.run(**kwargs)
    try:
        async for message in stream:
            if isinstance(message, Compacted):
                raise message
            if on_output is not None:
                on_output()
            adapted = normalize_message(message)
            if adapted is None:
                # Фильтрованные метаданные (init и т.п.) — не граница шага:
                # компакт решает только LiveAgents после реальных сообщений,
                # токеновые границы без сообщений проверяет сам провайдер.
                continue
            for block in adapted.content or []:
                line = _activity(block, working_dir, emit=emit, role=role)
                if line:
                    print(f"    · {line}", flush=True)
            text = adapted.get_text_content()
            if text:
                parts.append(text)
                if emit is not None:
                    emit("text", role=role, text=text[:300])
                if verbose:
                    snippet = " ".join(text.split())[:200]
                    if snippet:
                        print(f"    ✎ {snippet}", flush=True)
            if compact_check is not None:
                tokens = getattr(provider, "last_context_tokens", 0) or 0
                if compact_check(tokens):
                    raise Compacted(tokens)
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Compacted:
                raise
            except Exception:
                pass
    return "\n".join(parts)
