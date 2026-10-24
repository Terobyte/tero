"""Фаза 1: счётчик контекста (compact)."""

import pytest

from src.loop.compact import should_compact


def test_should_compact():
    assert should_compact(tokens=150, at_tokens=100, requested=False) is True
    assert should_compact(tokens=100, at_tokens=100, requested=False) is True
    assert should_compact(tokens=50, at_tokens=100, requested=False) is False
    assert should_compact(tokens=10**9, at_tokens=0, requested=False) is False
    assert should_compact(tokens=0, at_tokens=100, requested=True) is True
    assert should_compact(tokens=0, at_tokens=0, requested=True) is True


@pytest.mark.asyncio
async def test_run_role_stops_at_threshold(tmp_path):
    from src.loop.agents import LiveAgents
    from src.providers.message_adapter import AdaptedMessage, TextBlock

    class Stub:
        def __init__(self):
            self.last_context_tokens = 0
            self.pulled = 0

        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            for t in (10, 50, 120, 200, 300):
                self.last_context_tokens = t
                self.pulled += 1
                yield AdaptedMessage(
                    role="assistant", content=[TextBlock(text=f"step {t}")]
                )

    stub = Stub()
    agents = LiveAgents(str(tmp_path), factory=lambda _n: stub, retry_wait_s=0)
    seen: list[int] = []

    def check(tokens: int) -> bool:
        seen.append(tokens)
        return tokens >= 100

    data = await agents.run_role(
        "player",
        provider="x",
        model="m",
        prompt="P",
        timeout=10,
        compact_check=check,
    )
    assert data == {"__compacted__": True, "tokens": 120}
    assert stub.pulled == 3
    assert seen == [10, 50, 120]


@pytest.mark.asyncio
async def test_opencode_and_agy_report_tokens(tmp_path):
    import asyncio
    import json
    from unittest.mock import patch

    from src.providers.agy import AgyConfig, AgyProvider
    from src.providers.opencode import OpenCodeConfig, OpenCodeProvider

    oc_events = [
        {"type": "text", "part": {"type": "text", "text": "working"}},
        {"type": "step_finish",
         "part": {"tokens": {"input": 1000, "output": 10}}},
        {"type": "text", "part": {"type": "text", "text": "done"}},
        {"type": "step_finish",
         "part": {"tokens": {"input": 2500, "output": 20}}},
    ]
    oc = OpenCodeProvider(OpenCodeConfig())
    assert oc.last_context_tokens == 0
    with patch(
        "asyncio.create_subprocess_exec",
        _mock_subprocess([_jsonl(*oc_events)]),
    ):
        async for _ in oc.run("P", "", str(tmp_path)):
            pass
    assert oc.last_context_tokens == 2500

    ag_stream = [
        {"event": "step_update", "step_update": {
            "state": "DONE", "step_type": "tool",
            "usage": {"input_tokens": 33, "output_tokens": 5}}},
        {"event": "step_update", "step_update": {
            "state": "DONE", "step_type": "tool",
            "usage": {"input_tokens": 100, "output_tokens": 9}}},
        {"event": "result", "result": {
            "status": "SUCCESS", "response": "done",
            "usage": {"input_tokens": 100, "output_tokens": 9}}},
    ]
    ag = AgyProvider(AgyConfig())
    assert ag.last_context_tokens == 0
    with patch(
        "asyncio.create_subprocess_exec",
        _mock_subprocess([_jsonl(*ag_stream)]),
    ):
        async for _ in ag.run("P", "", str(tmp_path)):
            pass
    assert ag.last_context_tokens == 100

    # F4: явный input_tokens=0 обновляет счётчик, а не оставляет старое.
    ag2 = AgyProvider(AgyConfig())
    zero_stream = [
        {"event": "step_update", "step_update": {
            "state": "DONE", "step_type": "tool",
            "usage": {"input_tokens": 33, "output_tokens": 5}}},
        {"event": "step_update", "step_update": {
            "state": "DONE", "step_type": "tool",
            "usage": {"input_tokens": 0, "output_tokens": 0}}},
        {"event": "result", "result": {
            "status": "SUCCESS", "response": "done",
            "usage": {"input_tokens": 0, "output_tokens": 0}}},
    ]
    with patch(
        "asyncio.create_subprocess_exec",
        _mock_subprocess([_jsonl(*zero_stream)]),
    ):
        async for _ in ag2.run("P", "", str(tmp_path)):
            pass
    assert ag2.last_context_tokens == 0


def _jsonl(*events) -> bytes:
    import json

    return b"".join((json.dumps(e) + "\n").encode() for e in events)


def _mock_subprocess(processes):
    import asyncio as _asyncio

    queue = list(processes)

    class _Stdout:
        def __init__(self, payload: bytes):
            self._payload = payload

        async def read(self, _n: int = -1) -> bytes:
            data, self._payload = self._payload, b""
            return data

    class _Stderr:
        async def read(self) -> bytes:
            return b""

    class _Stdin:
        def write(self, _data: bytes) -> None:
            pass

        async def drain(self) -> None:
            pass

        def close(self) -> None:
            pass

    class _Proc:
        def __init__(self, payload: bytes):
            self.stdout = _Stdout(payload)
            self.stderr = _Stderr()
            self.stdin = _Stdin()
            self.returncode: int | None = None

        async def wait(self) -> None:
            self.returncode = 0

        def kill(self) -> None:
            self.returncode = -9

    async def _create(*_args, **_kwargs):
        return _Proc(queue.pop(0))

    return _create


def test_claude_counts_cache_read_and_muse_inherits():
    from src.providers.claude_native import ClaudeNativeConfig, ClaudeNativeProvider
    from src.providers.muse import MuseConfig, MuseProvider

    cl = ClaudeNativeProvider(ClaudeNativeConfig())
    cl._capture_usage(
        {"type": "result", "usage": {"input_tokens": 100, "cache_read_input_tokens": 50}}
    )
    assert cl.last_context_tokens == 150

    mu = MuseProvider(MuseConfig())
    assert mu.last_context_tokens == 0
    assert mu._opencode.last_context_tokens == 0
    mu._capture_usage(
        {"type": "result", "usage": {"input_tokens": 100, "cache_read_input_tokens": 50}}
    )
    assert mu.last_context_tokens == 150
    assert mu.last_context_tokens == mu._opencode.last_context_tokens


@pytest.mark.asyncio
async def test_compact_check_at_suppressed_step_finish(tmp_path):
    """F1: step_finish без сообщения всё равно останавливает поток на границе."""
    import asyncio

    from src.loop.agents import LiveAgents
    from src.loop.compact import Compacted
    from src.providers.message_adapter import AdaptedMessage, TextBlock
    from src.providers.opencode import OpenCodeConfig, OpenCodeProvider

    class SuppressedStepFinish(OpenCodeProvider):
        def __init__(self):
            super().__init__(OpenCodeConfig())
            self.pulled = 0

        async def run(self, prompt, system_prompt, working_dir, model="",
                      compact_check=None, **kwargs):
            events = [
                {"type": "text", "part": {"type": "text", "text": "working"}} ,
                {"type": "step_finish",
                 "part": {"tokens": {"input": 100, "output": 5}}},
                {"type": "text", "part": {"type": "text", "text": "never seen"}},
            ]
            for event in events:
                if event["type"] == "step_finish":
                    self._adapt_opencode_event(event)
                    if compact_check is not None and compact_check(
                        self.last_context_tokens
                    ):
                        raise Compacted(self.last_context_tokens)
                    continue
                self.pulled += 1
                yield AdaptedMessage(
                    role="assistant", content=[TextBlock(text="working")]
                )

    provider = SuppressedStepFinish()
    agents = LiveAgents(str(tmp_path), factory=lambda _n: provider, retry_wait_s=0)
    seen: list[int] = []

    def check(tokens: int) -> bool:
        seen.append(tokens)
        return tokens >= 100

    data = await agents.run_role(
        "player", provider="x", model="m", prompt="P", timeout=10,
        compact_check=check,
    )
    assert data == {"__compacted__": True, "tokens": 100}
    assert provider.pulled == 1
    assert 100 in seen


@pytest.mark.asyncio
async def test_compact_check_at_suppressed_agy_done(tmp_path):
    """F1: DONE-шаг agy без сообщения останавливает поток до чтения дальше."""
    from src.loop.agents import LiveAgents
    from src.loop.compact import Compacted
    from src.providers.message_adapter import AdaptedMessage, TextBlock
    from src.providers.agy import AgyConfig, AgyProvider

    class SuppressedDone(AgyProvider):
        def __init__(self):
            super().__init__(AgyConfig())
            self.pulled = 0

        async def run(self, prompt, system_prompt, working_dir, model="",
                      compact_check=None, **kwargs):
            self._capture_usage({"input_tokens": 100, "output_tokens": 5})
            if compact_check is not None and compact_check(
                self.last_context_tokens
            ):
                raise Compacted(self.last_context_tokens)
            self.pulled += 1
            yield AdaptedMessage(
                role="assistant", content=[TextBlock(text="never seen")]
            )

    provider = SuppressedDone()
    agents = LiveAgents(str(tmp_path), factory=lambda _n: provider, retry_wait_s=0)

    data = await agents.run_role(
        "player", provider="x", model="m", prompt="P", timeout=10,
        compact_check=lambda tokens: tokens >= 100,
    )
    assert data == {"__compacted__": True, "tokens": 100}
    assert provider.pulled == 0


@pytest.mark.asyncio
async def test_repair_call_checks_compact(tmp_path):
    """F3: repair-вызов тоже останавливается на пороге, а не доживает поток."""
    from src.loop.agents import LiveAgents
    from src.providers.message_adapter import AdaptedMessage, TextBlock

    class RepairGrows:
        def __init__(self):
            self.last_context_tokens = 0
            self.calls = 0
            self.pulled = 0

        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            self.calls += 1
            if self.calls == 1:
                self.last_context_tokens = 10
                yield AdaptedMessage(
                    role="assistant", content=[TextBlock(text="not json at all")]
                )
                return
            for t in (50, 120, 300):
                self.last_context_tokens = t
                self.pulled += 1
                yield AdaptedMessage(
                    role="assistant", content=[TextBlock(text=f"repair {t}")]
                )

    stub = RepairGrows()
    agents = LiveAgents(str(tmp_path), factory=lambda _n: stub, retry_wait_s=0)

    data = await agents.run_role(
        "player", provider="x", model="m", prompt="P", timeout=10,
        compact_check=lambda tokens: tokens >= 100,
    )
    assert data == {"__compacted__": True, "tokens": 120}
    assert stub.calls == 2
    assert stub.pulled == 2
