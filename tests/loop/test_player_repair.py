"""Прогон 223814: игрок 20 минут читал, вызов кончился без JSON, а повтор
«верни только JSON» ушёл в новую сессию opencode без контракта — и вернул
done с пустым changes, дерево осталось пустым."""

import pytest

from src.loop.agents import LiveAgents
from src.loop.prompts import player_prompt
from src.loop.schemas import ValidateCtx, validate

NARRATION = "Test plan is set — drafting the red tests now."
GOOD = (
    '{"status": "done", "changes": [{"file": "src/loop/resume_step.py", '
    '"what": "resume", "why": "контракт"}], "start_here": [], "blocked_reason": ""}'
)
EMPTY_DONE = '{"status":"done","changes":[],"start_here":[],"blocked_reason":""}'


class _Scripted:
    """Без памяти между вызовами, как opencode run: каждый run — новая сессия."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
        self.prompts.append(prompt)
        yield self.replies.pop(0)


async def _player(tmp_path, provider):
    agents = LiveAgents(str(tmp_path), factory=lambda _n: provider, retry_wait_s=0)
    return await agents.run_role(
        "player",
        provider="opencode",
        model="m",
        prompt="## Контракт фазы\nresume со снимка",
        timeout=10,
        ctx=ValidateCtx(),
    )


@pytest.mark.asyncio
async def test_repair_call_carries_contract(tmp_path):
    provider = _Scripted([NARRATION, GOOD])
    data = await _player(tmp_path, provider)
    assert data["changes"]
    repair = provider.prompts[1]
    assert "resume со снимка" in repair
    assert "git diff" in repair
    assert NARRATION in repair


@pytest.mark.asyncio
async def test_empty_done_is_repaired_not_accepted(tmp_path):
    provider = _Scripted([EMPTY_DONE, GOOD])
    data = await _player(tmp_path, provider)
    assert len(provider.prompts) == 2
    assert data["changes"][0]["file"] == "src/loop/resume_step.py"


def test_done_without_changes_is_invalid():
    errors = validate(
        "player",
        {"status": "done", "changes": [], "start_here": [], "blocked_reason": ""},
    )
    assert errors and "changes" in errors[0]


def test_blocked_without_changes_stays_valid():
    data = {"status": "blocked", "changes": [], "start_here": [], "blocked_reason": "x"}
    assert validate("player", data) == []


def test_player_prompt_demands_file_edits():
    text = player_prompt(preamble="", contract="c")
    assert "done без правок" in text


def test_player_prompt_red_test_first_and_priced_blocked():
    text = player_prompt(preamble="", contract="c")
    assert "Можно запускать тесты" not in text
    assert "Первая правка — красный тест" in text
    assert "«нужно ещё почитать» — не причина" in text


def test_blocked_needs_reason():
    data = {"status": "blocked", "changes": [], "start_here": [], "blocked_reason": " "}
    assert validate("player", data)
