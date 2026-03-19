"""Codex provider — OpenAI-compatible via ai-cli-proxy-api."""

import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
import yaml


_MODEL_REASONING_ALIASES = {
    "gpt-5.4-medium": ("gpt-5.4", "medium"),
    "gpt-5.4-high": ("gpt-5.4", "high"),
    "gpt-5.4-ultra-high": ("gpt-5.4", "xhigh"),
}


@dataclass
class CodexConfig:
    """Configuration for Codex provider (OpenAI-compatible)."""

    api_url: str = "http://localhost:8765"
    api_key: str = ""  # Optional: some proxies don't need auth
    model: str = "gpt-5.4-medium"
    default_timeout: int = 900
    auto_start: bool = False
    proxy_repo_path: str = ""
    proxy_config_path: str = ""
    proxy_log_path: str = ""
    proxy_pid_path: str = ""
    startup_timeout_s: int = 45
    auth_dir: str = "~/.cli-proxy-api"


class CodexProvider:
    """Codex provider using OpenAI-compatible API (ai-cli-proxy-api).

    This provider connects to an OpenAI-compatible proxy server
    that exposes codex-style agents via chat completions API.
    """

    def __init__(self, config: CodexConfig | None = None):
        self.config = config or CodexConfig()

    async def run(
        self,
        prompt: str,
        system_prompt: str,
        working_dir: str,
        max_turns: int = 30,
        model: str = "",
    ) -> AsyncIterator:
        """Run Codex agent via OpenAI-compatible streaming API.

        Args:
            prompt: User prompt
            system_prompt: System instructions
            working_dir: Working directory (passed as context)
            max_turns: Maximum turns (mapped to max_tokens via proxy)
            model: Optional model override

        Yields:
            Streaming message chunks adapted to SDK format
        """
        resolved_model, reasoning_effort = self._resolve_model_and_effort(
            model or self.config.model
        )
        url = f"{self.config.api_url.rstrip('/')}/v1/chat/completions"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": resolved_model,
            "messages": messages,
            "stream": True,
            # Pass working directory context for the proxy
            "metadata": {
                "working_dir": working_dir,
                "max_turns": max_turns,
            },
        }
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        async with httpx.AsyncClient(timeout=self.config.default_timeout) as client:
            async with client.stream(
                "POST",
                url,
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()

                # Buffer consecutive text tokens so the display shows complete
                # lines instead of one [text] per character.
                text_buf = ""

                async for line in response.aiter_lines():
                    if not line or line == "data: [DONE]":
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]  # Remove "data: " prefix
                        try:
                            data = json.loads(data_str)
                            chunk = self._adapt_chunk(data)
                            if not chunk:
                                continue

                            if chunk.get("type") == "text":
                                text_buf += chunk["text"]
                                while "\n" in text_buf:
                                    complete, text_buf = text_buf.split("\n", 1)
                                    if complete:
                                        yield {"type": "text", "text": complete + "\n",
                                               "role": chunk.get("role", "assistant")}
                            else:
                                if text_buf.strip():
                                    yield {"type": "text", "text": text_buf,
                                           "role": "assistant"}
                                    text_buf = ""
                                yield chunk
                        except json.JSONDecodeError:
                            continue

                if text_buf.strip():
                    yield {"type": "text", "text": text_buf, "role": "assistant"}

    def _adapt_chunk(self, data: dict) -> dict | None:
        """Adapt OpenAI streaming chunk to internal format.

        Returns format compatible with adapt_claude_event() which expects
        type "text", "assistant", "tool_use", "tool_result", or "result".

        Note: OpenAI streaming sends role-only deltas (role="assistant", content="")
        as the first chunk to signal message start. These must be handled, not discarded.
        """
        choices = data.get("choices", [])
        if not choices:
            return None

        delta = choices[0].get("delta", {})
        content = delta.get("content", "")
        role = delta.get("role", "")
        finish_reason = choices[0].get("finish_reason", "")

        # Handle finish/end of stream
        if finish_reason:
            return {
                "type": "result",
                "result": "",
                "stop_reason": finish_reason,
            }

        # Handle role-only delta (first chunk in OpenAI streaming)
        # This signals message start and must be emitted, not discarded
        if role and not content:
            return {
                "type": "assistant",
                "role": role,
            }

        # Skip truly empty chunks (no role, no content, no finish)
        if not content:
            return None

        # Return text content chunk
        return {
            "type": "text",
            "text": content,
            "role": role or "assistant",
        }

    def check_ready(self) -> tuple[bool, str]:
        """Check if Codex proxy server is available."""
        ok, reason = self._check_health()
        if ok:
            return self._check_api_access()

        if self.config.auto_start:
            started, start_reason = self._autostart_proxy()
            if started:
                ok, wait_reason = self._wait_for_health()
                if not ok:
                    return False, wait_reason
                return self._check_api_access()
            return False, start_reason or reason

        return False, reason

    def _check_health(self) -> tuple[bool, str]:
        """Check if Codex proxy server health endpoint responds."""
        try:
            with httpx.Client(timeout=5.0) as client:
                base_url = self.config.api_url.rstrip("/")
                for url in (f"{base_url}/health", base_url or self.config.api_url):
                    response = client.get(url)
                    if response.status_code == 200:
                        return True, ""
                return False, f"Codex proxy returned status {response.status_code}"
        except httpx.ConnectError:
            return False, f"Cannot connect to Codex proxy at {self.config.api_url}"
        except Exception as e:
            return False, f"Codex proxy check failed: {e}"

    def _check_api_access(self) -> tuple[bool, str]:
        """Validate downstream API key and that at least one model is available."""
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(
                    f"{self.config.api_url.rstrip('/')}/v1/models",
                    headers=headers,
                )
        except httpx.ConnectError:
            return False, f"Cannot connect to Codex proxy at {self.config.api_url}"
        except Exception as e:
            return False, f"Codex proxy model check failed: {e}"

        if response.status_code == 401:
            return False, f"Codex proxy rejected the configured API key for {self.config.api_url}"
        if response.status_code != 200:
            return False, f"Codex proxy model check returned status {response.status_code}"

        try:
            payload = response.json()
        except ValueError:
            return False, "Codex proxy returned invalid JSON from /v1/models"

        models = payload.get("data", [])
        if not models:
            return (
                False,
                "Codex proxy is running, but no Codex/OpenAI models are configured. "
                "Add a codex credential to CLIProxyAPI or run a Codex login once.",
            )
        return True, ""

    @property
    def display_name(self) -> str:
        """Human-readable name for UI."""
        return f"Codex ({self.config.model})"

    @staticmethod
    def _resolve_model_and_effort(model: str) -> tuple[str, str]:
        """Split UI-friendly codex aliases into base model + reasoning effort."""
        return _MODEL_REASONING_ALIASES.get(model, (model, ""))

    def _autostart_proxy(self) -> tuple[bool, str]:
        """Start ai-cli-proxy-api in the background when configured."""
        if self._is_api_port_open():
            return True, ""

        pid_path = self._pid_path()
        existing_pid = self._read_pid(pid_path)
        if existing_pid and self._pid_alive(existing_pid):
            return True, ""

        repo_path = self._proxy_repo_path()
        if repo_path is None:
            return False, "Codex proxy auto-start failed: ai-cli-proxy-api repo not found"

        config_path = self._proxy_config_path(repo_path)
        if config_path is None:
            return False, "Codex proxy auto-start failed: no config.yaml or config.example.yaml found"

        command = self._proxy_command(repo_path)
        if not command:
            return False, "Codex proxy auto-start failed: neither CLIProxyAPI binary nor `go` was found"

        log_path = self._log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.parent.mkdir(parents=True, exist_ok=True)

        with open(log_path, "ab") as log_file:
            process = subprocess.Popen(
                [*command, "-config", str(config_path)],
                cwd=str(repo_path),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )

        pid_path.write_text(str(process.pid))
        return True, ""

    def _wait_for_health(self) -> tuple[bool, str]:
        """Wait for the proxy health endpoint after auto-start."""
        deadline = time.time() + max(self.config.startup_timeout_s, 1)
        last_reason = f"Cannot connect to Codex proxy at {self.config.api_url}"

        while time.time() < deadline:
            ok, reason = self._check_health()
            if ok:
                return True, ""
            last_reason = reason
            time.sleep(0.5)

        log_path = self._log_path()
        if log_path.exists():
            last_reason = f"{last_reason}. See log: {log_path}"
        return False, last_reason

    def _proxy_repo_path(self) -> Path | None:
        """Resolve ai-cli-proxy-api repo path from config or nearby checkout."""
        if self.config.proxy_repo_path:
            path = Path(self.config.proxy_repo_path).expanduser().resolve()
            return path if path.exists() else None

        current = Path(__file__).resolve()
        for parent in current.parents:
            candidate = parent / "ai-cli-proxy-api"
            if candidate.is_dir():
                return candidate
        return None

    def _proxy_config_path(self, repo_path: Path) -> Path | None:
        """Resolve config path for the proxy server."""
        if self.config.proxy_config_path:
            path = Path(self.config.proxy_config_path).expanduser().resolve()
            return path if path.exists() else None

        repo_config = repo_path / "config.yaml"
        if repo_config.exists():
            return repo_config

        generated = self._generated_proxy_config_path()
        self._ensure_generated_proxy_config(generated)
        if generated.exists():
            return generated

        for candidate in (repo_path / "config.example.yaml",):
            if candidate.exists():
                return candidate
        return None

    def _proxy_command(self, repo_path: Path) -> list[str]:
        """Resolve how to launch the proxy server."""
        binary_path = repo_path / "CLIProxyAPI"
        if binary_path.exists() and os.access(binary_path, os.X_OK):
            return [str(binary_path)]

        go_bin = shutil.which("go")
        if go_bin:
            return [go_bin, "run", "./cmd/server"]

        return []

    def _log_path(self) -> Path:
        """Resolve log file path for the background proxy process."""
        if self.config.proxy_log_path:
            return Path(self.config.proxy_log_path).expanduser().resolve()
        return Path("~/.g3/logs/cli-proxy-api.log").expanduser()

    def _pid_path(self) -> Path:
        """Resolve pid file path for the background proxy process."""
        if self.config.proxy_pid_path:
            return Path(self.config.proxy_pid_path).expanduser().resolve()
        return Path("~/.g3/run/cli-proxy-api.pid").expanduser()

    def _generated_proxy_config_path(self) -> Path:
        """Path for auto-generated local proxy configuration."""
        return Path("~/.g3/cliproxy/config.yaml").expanduser()

    def _ensure_generated_proxy_config(self, path: Path) -> None:
        """Write a minimal local proxy config that matches g3 expectations."""
        if path.exists():
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        _host, port = self._host_port()
        config = {
            "host": "127.0.0.1",
            "port": port or 8317,
            "auth-dir": self.config.auth_dir,
            "api-keys": [self.config.api_key or "g3-local-key"],
            "debug": False,
            "logging-to-file": False,
            "usage-statistics-enabled": False,
        }
        path.write_text(yaml.safe_dump(config, sort_keys=False))

    @staticmethod
    def _read_pid(pid_path: Path) -> int | None:
        """Read a pid file if present."""
        try:
            raw = pid_path.read_text().strip()
            return int(raw) if raw else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Return True when a process id is currently alive."""
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _is_api_port_open(self) -> bool:
        """Best-effort TCP check before attempting auto-start."""
        host, port = self._host_port()
        if port <= 0:
            return False
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            return False

    def _host_port(self) -> tuple[str, int]:
        """Parse host and port from api_url."""
        try:
            host_port = self.config.api_url.split("://", 1)[-1]
            host_port = host_port.split("/", 1)[0]
            host, port = host_port.rsplit(":", 1)
            return host, int(port)
        except (ValueError, TypeError):
            return "127.0.0.1", 0
