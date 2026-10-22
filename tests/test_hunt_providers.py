"""Red tests for hunt bugs B03/B20/B21/B22/B31/B33/B34/B35/B36 (writer 3)."""

import pytest

import src.providers.claude_native as claude_native_mod
import src.providers.codex as codex_mod
import src.providers.cursor as cursor_mod
import src.providers.gemini as gemini_mod
import src.providers.opencode as opencode_mod
from src.errors import ProviderError
from src.providers.claude_native import ClaudeNativeProvider
from src.providers.codex import CodexProvider
from src.providers.cursor import CursorProvider
from src.providers.gemini import GeminiProvider
from src.providers.opencode import OpenCodeConfig, OpenCodeProvider
from src.providers.subprocess_runner import SubprocessExit


def _patch_runner(monkeypatch, module, events, exit_code=0, stderr=b""):
    """Replace provider-module run_subprocess_jsonl; capture call kwargs."""
    seen = {}

    async def _fake(cmd, working_dir, env=None, stdin_data=None, stall_timeout=0):
        seen["cmd"] = list(cmd)
        seen["env"] = env
        seen["stdin_data"] = stdin_data
        seen["stall_timeout"] = stall_timeout
        for ev in events:
            yield ev
        yield SubprocessExit(returncode=exit_code, stderr=stderr)

    monkeypatch.setattr(module, "run_subprocess_jsonl", _fake)
    return seen


async def _drain(provider, *args, **kwargs):
    out = []
    async for m in provider.run(*args, **kwargs):
        out.append(m)
    return out


def _texts(messages):
    out = []
    for m in messages:
        get = getattr(m, "get_text_content", None)
        if callable(get):
            t = get()
            if t:
                out.append(t)
    return out


def _flag_value(cmd, flag):
    try:
        i = cmd.index(flag)
        return cmd[i + 1]
    except (ValueError, IndexError):
        return None


@pytest.mark.asyncio
async def test_B03a_gemini_read_only_uses_plan(monkeypatch, tmp_path):
    seen = _patch_runner(monkeypatch, gemini_mod, [])
    await _drain(GeminiProvider(), "p", "sys", str(tmp_path), read_only=True)
    cmd = seen["cmd"]
    assert _flag_value(cmd, "--approval-mode") == "plan", (
        f"B03a: нет --approval-mode plan при read_only=True: {cmd!r}"
    )
    assert "--yolo" not in cmd, f"B03a: --yolo при read_only=True: {cmd!r}"


@pytest.mark.asyncio
async def test_B03b_cursor_read_only_uses_ask(monkeypatch, tmp_path):
    seen = _patch_runner(monkeypatch, cursor_mod, [])
    await _drain(CursorProvider(), "p", "sys", str(tmp_path), read_only=True)
    cmd = seen["cmd"]
    assert _flag_value(cmd, "--mode") == "ask", (
        f"B03b: нет --mode ask при read_only=True: {cmd!r}"
    )
    assert "--force" not in cmd, f"B03b: --force при read_only=True: {cmd!r}"
    assert "--yolo" not in cmd, f"B03b: --yolo при read_only=True: {cmd!r}"


@pytest.mark.asyncio
async def test_B03c_opencode_read_only_uses_plan(monkeypatch, tmp_path):
    seen = _patch_runner(monkeypatch, opencode_mod, [])
    await _drain(OpenCodeProvider(), "p", "sys", str(tmp_path), read_only=True)
    cmd = seen["cmd"]
    assert _flag_value(cmd, "--agent") == "plan", (
        f"B03c: нет --agent plan при read_only=True: {cmd!r}"
    )
    assert "--dangerously-skip-permissions" not in cmd, (
        f"B03c: --dangerously-skip-permissions при read_only=True: {cmd!r}"
    )
    assert "--auto" not in cmd, f"B03c: --auto при read_only=True: {cmd!r}"


@pytest.mark.asyncio
async def test_B03d_claude_read_only_disallows_bash(monkeypatch, tmp_path):
    seen = _patch_runner(monkeypatch, claude_native_mod, [])
    await _drain(ClaudeNativeProvider(), "p", "sys", str(tmp_path), read_only=True)
    cmd = seen["cmd"]
    assert "Bash" in (_flag_value(cmd, "--disallowedTools") or ""), (
        f"B03d: --disallowedTools без Bash при read_only=True: {cmd!r}"
    )


@pytest.mark.asyncio
async def test_B20_gemini_always_skips_trust(monkeypatch, tmp_path):
    seen = _patch_runner(monkeypatch, gemini_mod, [])
    await _drain(GeminiProvider(), "p", "sys", str(tmp_path), read_only=False)
    assert "--skip-trust" in seen["cmd"], (
        f"B20: нет --skip-trust в команде gemini: {seen['cmd']!r}"
    )


@pytest.mark.asyncio
async def test_B21_gemini_delta_concat_no_newline(monkeypatch, tmp_path):
    from src.loop.agents import _collect_text

    events = [
        {"type": "message", "role": "assistant",
         "content": '{"verdict": "SH', "delta": True},
        {"type": "message", "role": "assistant",
         "content": 'IP"}', "delta": True},
    ]
    _patch_runner(monkeypatch, gemini_mod, events)
    text = await _collect_text(
        GeminiProvider(), prompt="p", working_dir=str(tmp_path),
        model="", output_schema={}, read_only=False,
    )
    assert text == '{"verdict": "SHIP"}', f"B21: дельты склеились с мусором: {text!r}"


@pytest.mark.asyncio
async def test_B22_opencode_stall_timeout_from_config(monkeypatch, tmp_path):
    seen = _patch_runner(monkeypatch, opencode_mod, [])
    await _drain(OpenCodeProvider(), "p", "sys", str(tmp_path))
    assert seen.get("stall_timeout") == 0, (
        f"B22: stall_timeout не из конфига (ожидался 0): {seen.get('stall_timeout')!r}"
    )


def test_B31a_opencode_default_model():
    # a pinned free model goes stale; the user picks one from `opencode models`
    assert OpenCodeConfig().default_model == "", (
        f"B31a: дефолтная модель opencode: {OpenCodeConfig().default_model!r}"
    )


@pytest.mark.asyncio
async def test_B31b_opencode_stdout_error_text_in_exception(monkeypatch, tmp_path):
    events = [
        {"type": "error",
         "error": {"name": "E", "data": {"message": "boom-stdout-marker"}}},
    ]
    _patch_runner(monkeypatch, opencode_mod, events, exit_code=1, stderr=b"")
    try:
        await _drain(OpenCodeProvider(), "p", "", str(tmp_path))
    except ProviderError as exc:
        assert "boom-stdout-marker" in str(exc), (
            f"B31b: текст ошибки из stdout потерян: {exc}"
        )
    else:
        pytest.fail("B31b: провайдер не бросил исключение на error+exit 1")


@pytest.mark.asyncio
async def test_B33a_codex_stderr_not_in_text(monkeypatch, tmp_path):
    events = [
        {"type": "item.completed",
         "item": {"type": "agent_message", "text": "all good"}},
    ]
    _patch_runner(
        monkeypatch, codex_mod, events,
        exit_code=0, stderr=b'{"verdict": "WRONG"}',
    )
    messages = await _drain(CodexProvider(), "p", "", str(tmp_path))
    for t in _texts(messages):
        assert "WRONG" not in t, f"B33a: stderr попал в текст ответа: {t!r}"


@pytest.mark.asyncio
async def test_B33b_opencode_stderr_not_in_text(monkeypatch, tmp_path):
    events = [{"type": "text", "part": {"text": "all good"}}]
    _patch_runner(
        monkeypatch, opencode_mod, events,
        exit_code=0, stderr=b'{"verdict": "WRONG"}',
    )
    messages = await _drain(OpenCodeProvider(), "p", "", str(tmp_path))
    for t in _texts(messages):
        assert "WRONG" not in t, f"B33b: stderr попал в текст ответа: {t!r}"


@pytest.mark.asyncio
async def test_B34_gemini_prompt_via_stdin(monkeypatch, tmp_path):
    prompt = "X" * 200000
    seen = _patch_runner(monkeypatch, gemini_mod, [])
    await _drain(GeminiProvider(), prompt, "", str(tmp_path))
    for part in seen["cmd"]:
        assert len(part) <= 10000, (
            f"B34: промпт в argv (элемент длиной {len(part)}): {str(part)[:80]!r}"
        )
    stdin_data = seen.get("stdin_data") or b""
    assert prompt in stdin_data.decode("utf-8", errors="replace"), (
        "B34: промпт не передан через stdin"
    )


def test_B35_native_claude_env_hides_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-123")
    env = ClaudeNativeProvider()._clean_env()
    assert "ANTHROPIC_API_KEY" not in env, (
        "B35: ANTHROPIC_API_KEY просочился в env подпроцесса"
    )


@pytest.mark.asyncio
async def test_B36_codex_read_only_keeps_shell(monkeypatch, tmp_path):
    seen = _patch_runner(monkeypatch, codex_mod, [])
    await _drain(CodexProvider(), "p", "sys", str(tmp_path), read_only=True)
    cmd = seen["cmd"]
    assert "-s" in cmd and "read-only" in cmd, (
        f"B36: нет -s read-only при read_only=True: {cmd!r}"
    )
    assert "--disable" not in cmd, (
        f"B36: read_only отключает shell через --disable: {cmd!r}"
    )
