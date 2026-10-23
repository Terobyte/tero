"""Call a role, extract JSON, validate, repair once."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from src.loop.schemas import SCHEMAS, ValidateCtx, validate
from src.process_guard import ProcessGuard

# Overridable in tests: how long a role can go silent before we print a
# "still working" line, so a slow provider (opencode: 81s to first event,
# codex: 17s reasoning, a judge with no tool calls) never looks hung.
HEARTBEAT_S = 30.0

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any] | None:
    """Last JSON object in the assistant text: fenced block, else bare `{...}`."""
    fenced: list[tuple[int, dict[str, Any]]] = []
    for match in _FENCE.finditer(text or ""):
        parsed = _loads_object(match.group(1))
        if parsed is not None:
            fenced.append((match.start(), parsed))
    bare = _bare_objects(text or "")
    if fenced and (not bare or fenced[-1][0] > bare[-1][0]):
        return fenced[-1][1]
    return bare[-1][1] if bare else None


def _bare_objects(text: str) -> list[tuple[int, dict[str, Any]]]:
    decoder = json.JSONDecoder()
    found: list[tuple[int, dict[str, Any]]] = []
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(obj, dict):
            found.append((start, obj))
        index = end
    return found


def _loads_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def with_schema(prompt: str, schema: dict) -> str:
    """Spell the schema out: opencode/gemini/cursor ignore output_schema."""
    return (
        f"{prompt}\n\n## Формат финального сообщения\n"
        "Один JSON-объект строго по этой JSON Schema, без текста вокруг:\n"
        f"```json\n{json.dumps(schema, ensure_ascii=False)}\n```"
    )


def validate_role(
    role: str,
    data: dict[str, Any] | None,
    ctx: ValidateCtx | None = None,
) -> list[str]:
    if data is None:
        return ["no JSON object in the final message"]
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
        self._last_output_ts = 0.0

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

        text = await self._call(
            provider_name, model, with_schema(prompt, schema), schema, read_only, timeout, role, effort,
            emit=kwargs.get("emit"),
        )
        if text is None:
            return {"__unavailable__": True, "error": self.last_error}
        data = extract_json(text)
        errors = validate_role(role, data, ctx)
        if errors:
            repair = (
                "верни только JSON по схеме; твой прошлый ответ:\n"
                f"{text[-4000:]}\nошибки: {errors}"
            )
            text = await self._call(
                provider_name, model, with_schema(repair, schema), schema, read_only, timeout, role, effort,
                emit=kwargs.get("emit"),
            )
            if text is None:
                return {"__unavailable__": True, "error": self.last_error}
            data = extract_json(text)
            errors = validate_role(role, data, ctx)
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
    ) -> str | None:
        for attempt in range(2):
            before = self.guard.snapshot_pids()
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
                    ),
                    timeout=timeout,
                )
                return text
            except Exception as exc:  # provider auth/network
                self.last_error = str(exc) or type(exc).__name__
                print(f"  {provider_name} ошибка: {exc}", flush=True)
                if attempt == 0 and self.retry_wait_s:
                    print(f"  повтор через {self.retry_wait_s:.0f} с", flush=True)
                    await asyncio.sleep(self.retry_wait_s)
            finally:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
                self.guard.kill_new_processes(before)
        return None

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


def _default_factory(working_dir: str) -> Callable[[str], Any]:
    def factory(name: str):
        from src.config import load_provider_configs
        from src.providers import create_provider

        configs = load_provider_configs(working_dir)
        return create_provider(name, configs.get(name))

    return factory


_ACTIVITY_KEYS = (
    "file_path",
    "path",
    "filePath",
    "file",
    "command",
    "pattern",
    "url",
    "query",
    "prompt",
    "description",
)
_ACTIVITY_PATH_KEYS = {"file_path", "path", "filePath", "file"}


def _activity(block: Any, working_dir: str = "", emit=None, role: str = "") -> str:
    """One console line per tool call, so a long role call is not silent."""
    name = getattr(block, "name", "")
    if not name or getattr(block, "type", "tool_use") != "tool_use" or name == "StructuredOutput":
        return ""
    params = getattr(block, "input", None) or {}
    key = next((k for k in _ACTIVITY_KEYS if params.get(k)), None)
    if key is None:
        if emit is not None:
            emit("tool", role=role, text=name)
        return name
    arg = str(params[key])
    if key in _ACTIVITY_PATH_KEYS and working_dir:
        arg = ", ".join(_relative_path(p, working_dir) for p in arg.split(", "))
    arg = " ".join(arg.split())
    arg = _truncate_tail(arg, 100)
    line = f"{name} {arg}".strip()
    if emit is not None:
        emit("tool", role=role, text=line)
    return line


def _relative_path(value: str, working_dir: str) -> str:
    """Absolute paths inside working_dir read as `Read src/foo.py`, not a
    100-char-truncated tmpdir prefix that hides the file name."""
    try:
        candidate = Path(value)
        if not candidate.is_absolute():
            return value
        return str(candidate.resolve().relative_to(Path(working_dir).resolve()))
    except (OSError, ValueError):
        return value


def _truncate_tail(text: str, limit: int) -> str:
    """Truncate from the LEFT (keep the tail) so a long path keeps its file name."""
    if len(text) <= limit:
        return text
    return "..." + text[-(limit - 3):]


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
    parts: list[str] = []
    async for message in provider.run(**kwargs):
        if on_output is not None:
            on_output()
        adapted = normalize_message(message)
        if adapted is None:
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
    return "\n".join(parts)
