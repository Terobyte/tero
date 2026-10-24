"""Agy provider — Antigravity CLI via `agy -p ... --output-format stream-json`."""

import asyncio
import json
import re
import shutil
import subprocess
from dataclasses import dataclass

from src.errors import ProviderError, ProviderStalled, scrub_secrets
from .effort import normalize_effort
from .message_adapter import (
    AdaptedMessage,
    TextBlock,
    ToolUseBlock,
    wrap_system_prompt,
)
from .subprocess_runner import SubprocessExit, run_subprocess_jsonl


# Parallel `agy` runs share profile files: at most one call at a time.
_AGY_LOCK = asyncio.Lock()

# Pre-flight перед очередью: короткий `agy -p`; висящий CLI убивается по тишине.
AGY_LIST_TIMEOUT_S = 30
AGY_PREFLIGHT_TIMEOUT_S = 90

# A bare flash model (`gemini-3.8-flash`) means the high tier (`-high`).
_BARE_FLASH_MODEL = re.compile(r"^(gemini-\d[\w.]*-flash)$")


@dataclass
class AgyConfig:
    """Configuration for agy CLI provider."""

    command: str = "agy"
    # From `agy models` (2026-10-23); the old gemini default is gone.
    default_model: str = "gemini-3.8-flash-high"
    print_timeout_s: int = 3600
    # Seconds without stdout before the run is killed; 0 = off, the role timeout governs.
    stall_timeout_s: float = 0
    display_name: str = "Agy"


class AgyProvider:
    """Agy provider via CLI (`agy -p ... --output-format stream-json`).

    Launches agy as a subprocess, parses NDJSON stream-json output and yields
    AdaptedMessage objects the same way opencode.py does: streamed text from
    ``text_delta``, tool calls as ``tool_name`` + ``parameters`` — so
    ``normalize_message`` and ``_activity`` in agents.py work unchanged.
    """

    streams = True

    def __init__(self, config: AgyConfig | None = None):
        self.config = config or AgyConfig()
        self._last_input_tokens = 0
        self._last_output_tokens = 0

    def _reset_usage(self) -> None:
        self._last_input_tokens = 0
        self._last_output_tokens = 0

    async def run(
        self,
        prompt: str,
        system_prompt: str,
        working_dir: str,
        max_turns: int = 30,
        model: str = "",
        context_limit: int = 0,
        output_schema: dict | None = None,
        read_only: bool = False,
        effort: str = "",
    ):
        """Run agy CLI and yield adapted messages."""
        self._reset_usage()
        full_prompt = wrap_system_prompt(system_prompt, prompt)
        cmd = self._build_command(
            prompt=full_prompt,
            model=model,
            output_schema=output_schema,
            read_only=read_only,
            effort=effort,
        )
        async with _AGY_LOCK:
            _gen = run_subprocess_jsonl(
                cmd,
                working_dir,
                stdin_data=None,  # /dev/null: the prompt travels in -p
                stall_timeout=self.config.stall_timeout_s,
            )
            result_error: str | None = None
            result_failed = False
            try:
                async for event in _gen:
                    if isinstance(event, SubprocessExit):
                        if event.stalled:
                            raise ProviderStalled(
                                f"нет вывода {self.config.stall_timeout_s:g} с"
                            )
                        if event.returncode not in (None, 0) or result_failed:
                            raise ProviderError(
                                result_error or _stderr_tail(event.stderr)
                            )
                        return
                    if isinstance(event, dict) and event.get("event") == "result":
                        result = event.get("result") or {}
                        self._capture_usage(result.get("usage"))
                        err = result.get("error")
                        if err:
                            result_error = str(err)
                        if result.get("status") != "SUCCESS":
                            result_failed = True
                            continue
                        structured = result.get("structured_output")
                        if structured is not None:
                            text = json.dumps(structured)
                        else:
                            text = result.get("response") or ""
                        if text:
                            yield AdaptedMessage(
                                role="assistant",
                                content=[TextBlock(text=text)],
                                stop_reason="end_turn",
                                type="result",
                            )
                        continue
                    adapted = self._adapt_agy_event(event)
                    if adapted is not None:
                        yield adapted
            finally:
                await _gen.aclose()

    def _build_command(
        self,
        prompt: str = "",
        model: str = "",
        output_schema: dict | None = None,
        read_only: bool = False,
        effort: str = "",
    ) -> list[str]:
        """Build agy CLI command."""
        resolved_model = _resolve_model(model or self.config.default_model)
        resolved_effort = normalize_effort(effort)
        cmd = [
            self.config.command,
            "-p",
            prompt,
            "--model",
            resolved_model,
            "--output-format",
            "stream-json",
            "--print-timeout",
            f"{self.config.print_timeout_s}s",
            "--disable-slash-commands",
        ]
        if output_schema is not None:
            cmd.extend(["--json-schema", json.dumps(output_schema)])
        if resolved_effort:
            cmd.extend(["--effort", resolved_effort])
        if read_only:
            # The judge must not write to the working dir.
            cmd.extend(["--mode", "plan"])
        else:
            cmd.append("--dangerously-skip-permissions")
        return cmd

    def _adapt_agy_event(self, event: dict) -> AdaptedMessage | None:
        """Convert one agy stream-json event to an AdaptedMessage.

        Event shapes (agy 1.4.5):
          - ``init`` → ignored (metadata)
          - ``step_update`` with ``step_type == "agent_response"`` → text from
            ``text_delta``
          - ``step_update`` with ``step_type == "tool"`` and ``state ==
            "ACTIVE"`` → tool call (``tool_name`` + ``parameters``)
          - ``DONE`` steps → usage accounting only
          - ``result`` is handled in ``run`` (final text / error), never here
        """
        if not isinstance(event, dict):
            return None
        if event.get("event") != "step_update":
            return None
        upd = event.get("step_update") or {}
        if not isinstance(upd, dict):
            return None
        if upd.get("state") == "DONE":
            self._capture_usage(upd.get("usage"))
        if upd.get("step_type") != "tool":
            if upd.get("step_type") == "agent_response":
                delta = upd.get("text_delta") or ""
                if delta:
                    return AdaptedMessage(
                        role="assistant",
                        content=[TextBlock(text=delta)],
                        type="text",
                    )
            return None
        if upd.get("state") != "ACTIVE":
            return None
        info = upd.get("tool_info") or {}
        name = upd.get("tool_name") or info.get("name") or "tool"
        params = info.get("parameters") or {}
        if not isinstance(params, dict):
            params = {"output": str(params)}
        step = upd.get("step_index")
        return AdaptedMessage(
            role="assistant",
            content=[
                ToolUseBlock(
                    id=str(step) if step is not None else "",
                    name=name,
                    input=dict(params),
                )
            ],
            stop_reason="tool_use",
            type="tool_use",
        )

    def _capture_usage(self, usage) -> None:
        """Best-effort token accounting from DONE steps and the result event."""
        if not isinstance(usage, dict):
            return
        self._last_input_tokens = int(
            usage.get("input_tokens") or self._last_input_tokens or 0
        )
        self._last_output_tokens = int(
            usage.get("output_tokens") or self._last_output_tokens or 0
        )

    def check_ready(self) -> tuple[bool, str]:
        """Check if agy CLI is installed and available."""
        if not shutil.which(self.config.command):
            return False, f"'{self.config.command}' not found in PATH."
        return True, ""

    @property
    def display_name(self) -> str:
        return f"{self.config.display_name} ({self.config.default_model})"


def _resolve_model(model: str) -> str:
    """Append the high tier to a bare flash model name."""
    if _BARE_FLASH_MODEL.match(model or ""):
        return f"{model}-high"
    return model


def list_models(command: str = "agy") -> list[str]:
    """Model ids from ``agy models``; empty when the CLI is missing or fails."""
    try:
        out = subprocess.run(
            [command, "models"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=AGY_LIST_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return [line.split()[0] for line in out.stdout.splitlines() if line.split()]


def preflight(
    command: str = "agy",
    model: str = "",
    *,
    timeout_s: float = AGY_PREFLIGHT_TIMEOUT_S,
) -> tuple[bool, str]:
    """Короткий ``agy -p "ок"``: True когда CLI отвечает, иначе текст ошибки.

    ``False`` = стоп до очереди: нет входа, протухший токен, висящий процесс.
    Секреты из вывода вычищены.
    """
    cmd = [
        command,
        "-p",
        "ок",
        "--model",
        _resolve_model(model),
        "--output-format",
        "json",
        "--print-timeout",
        "60s",
    ]
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return False, f"`agy` не отвечает: тишина дольше {timeout_s:g} с"
    except OSError as exc:
        return False, scrub_secrets(f"`agy` не отвечает: {exc}")
    try:
        payload = json.loads((out.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    # `--output-format json` may print the result object bare or wrapped as `{"result": {...}}`.
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    if out.returncode == 0 and result.get("status") == "SUCCESS":
        return True, ""
    detail = result.get("error") or _stderr_tail(out.stderr)
    return False, scrub_secrets(f"`agy` не отвечает: `{detail}`")


def _stderr_tail(stderr_data: bytes | str) -> str:
    """Last lines of stderr for the ProviderError text."""
    if isinstance(stderr_data, bytes):
        text = stderr_data.decode("utf-8", errors="replace")
    else:
        text = stderr_data or ""
    return text.strip()[-2000:] or "agy exited without result"
