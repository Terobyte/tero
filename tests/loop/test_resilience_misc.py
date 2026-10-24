"""Виды ошибок провайдера: классификация и таймаут без «провайдер недоступен»."""

from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

from src.errors import ProviderStalled, classify_error
from src.loop.agents import LiveAgents
from src.loop.schemas import ValidateCtx


@pytest.mark.parametrize(
    ("exc", "text", "expected"),
    [
        (TimeoutError("timed out"), "", "timeout"),
        (asyncio.TimeoutError(), "", "timeout"),
        (ProviderStalled("нет вывода 5 с"), "", "stall"),
        (None, "Unauthorized", "auth"),
        (RuntimeError("HTTP 401"), "HTTP 401", "auth"),
        (None, "please log in to continue", "auth"),
        (None, "quota exceeded", "quota"),
        (None, "insufficient credits", "quota"),
        (ConnectionError("reset by peer"), "", "network"),
        (None, "ECONNRESET", "network"),
        (None, "429 Too Many Requests", "network"),
        (None, "something completely different", "other"),
    ],
)
def test_classify_error_table(exc, text, expected):
    assert classify_error(exc, text) == expected


_STALL_PROVIDERS = ("claude", "codex", "opencode", "cursor")


def _stub_cli(tmp_path, body: str) -> str:
    script = tmp_path / "stub-cli"
    script.write_text(f"#!{sys.executable}\n{body}\n")
    script.chmod(0o755)
    return str(script)


def _pid_alive(pid: int) -> bool:
    """PID-specific liveness check without enumerating processes."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    else:
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", _STALL_PROVIDERS)
async def test_stall_kills_every_streaming_provider(tmp_path, provider_name):
    """Stub печатает одну строку и молчит: провайдер бросает ProviderStalled."""
    from src.providers import create_provider

    pidfile = tmp_path / "stub.pid"
    cmd = _stub_cli(
        tmp_path,
        "import os, sys, time\n"
        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "sys.stdout.write('{\"type\":\"x\"}\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(4)\n",
    )
    provider = create_provider(provider_name, {"command": cmd, "stall_timeout_s": 0.5})
    started = time.monotonic()
    with pytest.raises(ProviderStalled, match=r"нет вывода 0.5 с"):
        async with asyncio.timeout(4):
            async for _ in provider.run("P", "", str(tmp_path)):
                pass
    elapsed = time.monotonic() - started
    assert elapsed < 5, f"stall kill took too long: {elapsed:.1f}s"
    stub_pid = int(pidfile.read_text().strip())
    assert not _pid_alive(stub_pid), f"stub process survived: {stub_pid}"


@pytest.mark.asyncio
async def test_stall_while_writing_stdin(tmp_path):
    """Тихий CLI не читает stdin: большой промпт не должен висеть на drain().

    F1: stall detection начинался только после записи stdin — 4 МиБ промпт
    в переполненный pipe блокировал drain() навсегда, и stalled-события не
    было до внешнего таймаута роли.
    """
    from src.providers import create_provider

    cmd = _stub_cli(tmp_path, "import time\ntime.sleep(4)\n")
    provider = create_provider("claude", {"command": cmd, "stall_timeout_s": 0.5})
    started = time.monotonic()
    with pytest.raises(ProviderStalled, match=r"нет вывода 0.5 с"):
        async with asyncio.timeout(4):
            async for _ in provider.run("x" * (4 * 1024 * 1024), "", str(tmp_path)):
                pass
    assert time.monotonic() - started < 5


@pytest.mark.asyncio
async def test_stall_after_stdout_eof(tmp_path):
    """CLI закрыл stdout, но висит: stall действует и на ожидание выхода.

    F3: _iter_stdout видел EOF и выходил, а неограниченный proc.wait()
    висел до внешнего таймаута роли вместо ProviderStalled.
    """
    from src.providers import create_provider

    cmd = _stub_cli(
        tmp_path,
        "import os, time\n"
        "os.close(1)\n"
        "time.sleep(4)\n",
    )
    provider = create_provider("claude", {"command": cmd, "stall_timeout_s": 0.2})
    started = time.monotonic()
    with pytest.raises(ProviderStalled, match=r"нет вывода 0.2 с"):
        async with asyncio.timeout(4):
            async for _ in provider.run("P", "", str(tmp_path)):
                pass
    assert time.monotonic() - started < 5


@pytest.mark.asyncio
async def test_stall_not_retried(tmp_path):
    """ProviderStalled из провайдера: _call зовёт провайдер один раз, kind=stall."""

    class _StalledProvider:
        calls = 0

        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            type(self).calls += 1
            raise ProviderStalled("нет вывода 0.5 с")
            yield  # pragma: no cover - делает run() асинхронным генератором

    provider = _StalledProvider()
    agents = LiveAgents(
        str(tmp_path), factory=lambda _n: provider, retry_wait_s=0
    )
    data = await agents.run_role(
        "player",
        provider="opencode",
        model="m",
        prompt="P",
        timeout=10,
        ctx=ValidateCtx(),
    )
    assert provider.calls == 1
    assert data["__unavailable__"] is True
    assert data["kind"] == "stall"


def test_non_streaming_provider_gets_no_stall(tmp_path, monkeypatch):
    """Провайдер с streams = False не получает stall_timeout (F4).

    Проверяется _default_factory путь: он вливает глобальный stall_timeout_s
    в streaming-провайдеры — exercise его с настроенным глобальным значением.
    """
    import src.config as config_mod
    import src.loop.agents as agents_mod
    from src.providers.gemini import GeminiProvider

    assert GeminiProvider.streams is False
    monkeypatch.setattr(
        config_mod,
        "load_provider_configs",
        lambda _wd, **_kw: {"gemini": {"command": "gemini"}},
    )
    monkeypatch.setattr(
        config_mod,
        "resolve_config",
        lambda _cli: type("C", (), {"stall_timeout_s": 42})(),
    )
    monkeypatch.setattr(
        config_mod,
        "resolve_config",
        lambda _cli: type("C", (), {"stall_timeout_s": 42})(),
    )
    provider = agents_mod._default_factory(str(tmp_path))("gemini")
    assert isinstance(provider, GeminiProvider)
    assert not hasattr(provider.config, "stall_timeout_s")
    # Контроль: streaming-провайдер через тот же путь получает глобальный stall.
    monkeypatch.setattr(
        config_mod,
        "load_provider_configs",
        lambda _wd, **_kw: {"claude": {"command": "claude"}},
    )
    streaming = agents_mod._default_factory(str(tmp_path))("claude")
    assert streaming.config.stall_timeout_s == 42


@pytest.mark.asyncio
async def test_timeout_is_not_provider_unavailable(tmp_path, capsys):
    class _HangingProvider:
        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            await asyncio.sleep(30)
            yield "{}"  # pragma: no cover - wait_for cancels the sleep first

    agents = LiveAgents(
        str(tmp_path), factory=lambda _n: _HangingProvider(), retry_wait_s=0
    )
    data = await agents.run_role(
        "player",
        provider="opencode",
        model="m",
        prompt="P",
        timeout=1,
        ctx=ValidateCtx(),
    )
    assert data["__unavailable__"] is True
    assert data["kind"] == "timeout"
    out = capsys.readouterr().out
    assert "провайдер недоступен" not in out
    assert "не уложился в таймаут" in out
    assert agents.last_error == data["error"]
    assert agents.last_error
    assert agents.last_error != "TimeoutError"
