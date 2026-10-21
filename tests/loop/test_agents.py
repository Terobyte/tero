"""JSON extraction from final assistant text (native-schema fallback)."""

from pathlib import Path

import pytest

from src.loop.agents import extract_json


def test_extract_fenced_json():
    text = """notes first
```json
{"status": "done", "changes": [], "start_here": [], "blocked_reason": ""}
```
"""
    data = extract_json(text)
    assert data["status"] == "done"


def test_extract_last_of_two_fences():
    text = """
```json
{"status": "blocked", "changes": [], "start_here": [], "blocked_reason": "old"}
```
then later
```json
{"status": "done", "changes": [], "start_here": [], "blocked_reason": ""}
```
"""
    assert extract_json(text)["status"] == "done"


def test_extract_bare_object():
    text = 'prefix {"status": "done", "changes": [], "start_here": [1], "blocked_reason": ""} tail'
    data = extract_json(text)
    assert data["status"] == "done"
    assert data["start_here"] == [1]


def test_extract_nested_object_in_junk():
    text = 'blah {"verdict": "SHIP", "previous": [], "findings": [{"severity": "advisory"}], "user_question": ""}'
    data = extract_json(text)
    assert data["verdict"] == "SHIP"
    assert data["findings"][0]["severity"] == "advisory"


def test_extract_invalid_returns_none():
    assert extract_json("no json here {") is None


def test_extract_empty_returns_none():
    assert extract_json("") is None


# --- _activity: bare tool name when no recognized argument key (regression) ---

from src.loop.agents import _activity


def test_activity_falls_back_to_bare_tool_name():
    from src.providers.message_adapter import ToolUseBlock

    block = ToolUseBlock(name="TodoWrite", input={"todos": [1]})
    assert _activity(block) == "TodoWrite"


def test_activity_falls_back_to_bare_tool_name_empty_input():
    from src.providers.message_adapter import ToolUseBlock

    block = ToolUseBlock(name="TodoWrite", input={})
    assert _activity(block) == "TodoWrite"


def test_activity_relativizes_each_comma_separated_path():
    from src.providers.message_adapter import ToolUseBlock

    working_dir = "/repo"
    block = ToolUseBlock(
        name="edit",
        input={"path": "/repo/src/a.py, /repo/src/b.py"},
    )
    line = _activity(block, working_dir)
    assert line == "edit src/a.py, src/b.py"


# --- LiveAgents: schema in prompt, invalid answers keep their evidence ---

import json

from src.loop.agents import LiveAgents
from src.loop.schemas import ValidateCtx

_VALID_JUDGE = {"verdict": "SHIP", "previous": [], "findings": [], "user_question": ""}


class _TextProvider:
    """CLI that ignores output_schema and just answers with text."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        yield reply


def _live(tmp_path, provider):
    return LiveAgents(str(tmp_path), factory=lambda _name: provider, retry_wait_s=0)


async def _judge(agents):
    return await agents.run_role(
        "judge", provider="opencode", model="m", prompt="P", timeout=5, ctx=ValidateCtx()
    )


@pytest.mark.asyncio
async def test_prompt_carries_role_schema(tmp_path):
    provider = _TextProvider([json.dumps(_VALID_JUDGE)])
    data = await _judge(_live(tmp_path, provider))
    assert data["verdict"] == "SHIP"
    assert '"user_question"' in provider.prompts[0]
    assert '"NO_SHIP"' in provider.prompts[0]


@pytest.mark.asyncio
async def test_invalid_answer_keeps_errors_and_raw(tmp_path):
    provider = _TextProvider(["looks fine to me", "still prose"])
    data = await _judge(_live(tmp_path, provider))
    assert data["__invalid__"] is True
    assert data["errors"] == ["no JSON object in the final message"]
    assert data["raw"] == "still prose"
    assert '"user_question"' in provider.prompts[1]


@pytest.mark.asyncio
async def test_provider_error_text_is_kept(tmp_path):
    provider = _TextProvider([RuntimeError("quota exceeded"), RuntimeError("quota exceeded")])
    data = await _judge(_live(tmp_path, provider))
    assert data["__unavailable__"] is True
    assert "quota exceeded" in data["error"]


@pytest.mark.asyncio
async def test_run_role_forwards_effort(tmp_path):
    class Rec:
        def __init__(self):
            self.kwargs = None

        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            self.kwargs = {"model": model, **kwargs}
            yield json.dumps(_VALID_JUDGE)

    rec = Rec()
    await _live(tmp_path, rec).run_role(
        "judge",
        provider="codex",
        model="gpt-5.6-terra",
        effort="max",
        prompt="P",
        timeout=5,
        ctx=ValidateCtx(),
    )
    assert rec.kwargs["effort"] == "max"


@pytest.mark.asyncio
async def test_heartbeat_fires_and_is_cancelled(tmp_path, capsys, monkeypatch):
    import asyncio

    from src.loop import agents as agents_mod

    monkeypatch.setattr(agents_mod, "HEARTBEAT_S", 0.05)

    class _SlowProvider:
        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            await asyncio.sleep(0.2)
            yield json.dumps(_VALID_JUDGE)

    data = await _judge(_live(tmp_path, _SlowProvider()))
    assert data["verdict"] == "SHIP"
    out = capsys.readouterr().out
    assert "работает" in out
    assert "judge" in out
    # nothing keeps printing heartbeat lines after the call returned —
    # the heartbeat task was cancelled cleanly.
    await asyncio.sleep(0.15)
    assert capsys.readouterr().out == ""


@pytest.mark.asyncio
async def test_heartbeat_cancelled_on_provider_error(tmp_path, capsys, monkeypatch):
    import asyncio

    from src.loop import agents as agents_mod

    monkeypatch.setattr(agents_mod, "HEARTBEAT_S", 0.05)

    class _SlowFailingProvider:
        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            await asyncio.sleep(0.2)
            raise RuntimeError("down")
            yield  # pragma: no cover - never reached, makes this an async gen

    agents = LiveAgents(str(tmp_path), factory=lambda _n: _SlowFailingProvider(), retry_wait_s=0)
    data = await _judge(agents)
    assert data["__unavailable__"] is True
    await asyncio.sleep(0.15)
    capsys.readouterr()  # drain
    assert capsys.readouterr().out == ""


@pytest.mark.asyncio
async def test_heartbeat_prints_within_one_interval_not_two(tmp_path, monkeypatch):
    """Regression: the heartbeat used to sleep(HEARTBEAT_S) then compare
    idle >= HEARTBEAT_S, so real silence starting just after a poll could
    go unreported for ~2xHEARTBEAT_S. Polling more often and resetting the
    idle marker after printing must bound the first print to ~HEARTBEAT_S."""
    import asyncio
    import time

    from src.loop import agents as agents_mod

    monkeypatch.setattr(agents_mod, "HEARTBEAT_S", 0.3)

    timestamps: list[float] = []

    def _fake_print(*args, **kwargs):
        text = " ".join(str(a) for a in args)
        if "работает" in text:
            timestamps.append(time.monotonic())

    monkeypatch.setattr(agents_mod, "print", _fake_print, raising=False)

    class _SlowProvider:
        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            yield "starting"  # marks real output right at call start
            await asyncio.sleep(0.65)
            yield json.dumps(_VALID_JUDGE)

    start = time.monotonic()
    data = await _judge(_live(tmp_path, _SlowProvider()))
    assert data["verdict"] == "SHIP"
    assert timestamps, "heartbeat never printed during 0.65s of silence"
    assert timestamps[0] - start < 0.5  # well under 2xHEARTBEAT_S (0.6s)


@pytest.mark.asyncio
async def test_verbose_prints_assistant_text(tmp_path, capsys):
    class _TalkyProvider:
        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            from src.providers.message_adapter import AdaptedMessage, TextBlock

            yield AdaptedMessage(
                role="assistant", content=[TextBlock(text="thinking about it")]
            )
            yield AdaptedMessage(role="assistant", content=[TextBlock(json.dumps(_VALID_JUDGE))])

    agents = LiveAgents(
        str(tmp_path), factory=lambda _n: _TalkyProvider(), retry_wait_s=0, verbose=True
    )
    data = await _judge(agents)
    assert data["verdict"] == "SHIP"
    out = capsys.readouterr().out
    assert "✎ thinking about it" in out


@pytest.mark.asyncio
async def test_not_verbose_hides_assistant_text(tmp_path, capsys):
    class _TalkyProvider:
        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            from src.providers.message_adapter import AdaptedMessage, TextBlock

            yield AdaptedMessage(
                role="assistant", content=[TextBlock(text="thinking about it")]
            )
            yield AdaptedMessage(role="assistant", content=[TextBlock(json.dumps(_VALID_JUDGE))])

    data = await _judge(_live(tmp_path, _TalkyProvider()))
    assert data["verdict"] == "SHIP"
    out = capsys.readouterr().out
    assert "✎" not in out


@pytest.mark.asyncio
async def test_activity_path_made_relative_to_working_dir(tmp_path, capsys):
    from src.providers.message_adapter import AdaptedMessage, TextBlock, ToolUseBlock

    nested = tmp_path / "src" / "loop"
    nested.mkdir(parents=True)
    abs_path = str(nested / "agents.py")

    class _ReaderProvider:
        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            yield AdaptedMessage(
                role="assistant",
                content=[ToolUseBlock(name="Read", input={"file_path": abs_path})],
            )
            yield AdaptedMessage(role="assistant", content=[TextBlock(json.dumps(_VALID_JUDGE))])

    data = await _judge(_live(tmp_path, _ReaderProvider()))
    assert data["verdict"] == "SHIP"
    out = capsys.readouterr().out
    assert "Read src/loop/agents.py" in out
    assert str(tmp_path) not in out


@pytest.mark.asyncio
async def test_activity_truncates_from_the_left_keeping_tail(tmp_path, capsys):
    """A long relative path keeps the file name (the tail), not the prefix."""
    from src.providers.message_adapter import AdaptedMessage, TextBlock, ToolUseBlock

    nested = tmp_path / "deeply" / "nested" / "package" / "of" / "many" / "folders"
    nested.mkdir(parents=True)
    long_name = "a" * 60 + ".py"
    abs_path = str(nested / long_name)
    rel_path = str(Path(abs_path).relative_to(tmp_path))
    assert len(rel_path) > 100  # otherwise this test doesn't exercise truncation

    class _ReaderProvider:
        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            yield AdaptedMessage(
                role="assistant",
                content=[ToolUseBlock(name="Read", input={"file_path": abs_path})],
            )
            yield AdaptedMessage(role="assistant", content=[TextBlock(json.dumps(_VALID_JUDGE))])

    data = await _judge(_live(tmp_path, _ReaderProvider()))
    assert data["verdict"] == "SHIP"
    out = capsys.readouterr().out
    assert long_name in out  # file name (the tail) survives
    assert "..." in out
    assert "deeply/nested" not in out  # the head was cut


@pytest.mark.asyncio
async def test_activity_new_keys_filepath_and_query(tmp_path, capsys):
    from src.providers.message_adapter import AdaptedMessage, TextBlock, ToolUseBlock

    class _MixedProvider:
        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            yield AdaptedMessage(
                role="assistant",
                content=[
                    ToolUseBlock(name="read", input={"filePath": "src/foo.py"}),
                    ToolUseBlock(name="webfetch", input={"query": "tero v4 spec"}),
                ],
            )
            yield AdaptedMessage(role="assistant", content=[TextBlock(json.dumps(_VALID_JUDGE))])

    data = await _judge(_live(tmp_path, _MixedProvider()))
    assert data["verdict"] == "SHIP"
    out = capsys.readouterr().out
    assert "read src/foo.py" in out
    assert "webfetch tero v4 spec" in out


@pytest.mark.asyncio
async def test_tool_activity_is_printed_while_role_runs(tmp_path, capsys):
    from src.providers.message_adapter import AdaptedMessage, TextBlock, ToolUseBlock

    class _ToolProvider:
        async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
            yield AdaptedMessage(
                role="assistant",
                content=[
                    ToolUseBlock(name="Read", input={"file_path": "game/core/board.gd"}),
                    ToolUseBlock(name="Bash", input={"command": "./scripts/test.sh"}),
                    ToolUseBlock(name="StructuredOutput", input=_VALID_JUDGE),
                ],
            )
            yield AdaptedMessage(role="assistant", content=[TextBlock(json.dumps(_VALID_JUDGE))])

    data = await _judge(_live(tmp_path, _ToolProvider()))
    assert data["verdict"] == "SHIP"
    out = capsys.readouterr().out
    assert "Read game/core/board.gd" in out
    assert "Bash ./scripts/test.sh" in out
    assert "StructuredOutput" not in out
