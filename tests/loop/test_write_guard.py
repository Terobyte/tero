"""Прогоны 223814 и 231742: игрок 25+ минут только читал (52 read, 9 grep,
0 write/edit во всех сессиях) и кончил ход пустым stop. Контроль по ходу
вызова: петля чтений → пинок в ту же сессию → снова без записи → blocked
read_only_loop."""

import subprocess

import pytest

from src.loop.agents import LiveAgents
from src.loop.schemas import ValidateCtx
from src.loop.guards.write import KICK_LIMIT, READ_ONLY_LIMIT
from src.providers.message_adapter import AdaptedMessage, ToolUseBlock

GOOD = (
    '{"status": "done", "changes": [{"file": "a.py", "what": "w", "why": "y"}], '
    '"start_here": [], "blocked_reason": ""}'
)


def _tool(name: str, **inp) -> AdaptedMessage:
    return AdaptedMessage(
        role="assistant",
        content=[ToolUseBlock(id="t", name=name, input=inp)],
        type="tool_use",
    )


def reads(n: int) -> list:
    return [_tool("read", filePath=f"src/f{i}.py") for i in range(n)]


class _Scripted:
    """Как opencode: каждый run — сессия; session-холдер получает её id."""

    def __init__(self, runs: list[list]):
        self.runs = [list(r) for r in runs]
        self.prompts: list[str] = []
        self.yielded: list[int] = []
        self.resumed: list[str] = []

    async def run(self, prompt, system_prompt, working_dir, model="", session=None, **kwargs):
        self.prompts.append(prompt)
        self.yielded.append(0)
        if session is not None:
            self.resumed.append(session["id"] if session.get("resume") else "")
            session["id"] = session["id"] or f"ses_{len(self.prompts)}"
        for item in self.runs.pop(0):
            if callable(item):
                item = item()
            self.yielded[-1] += 1
            yield item


async def _player(tmp_path, provider, events):
    agents = LiveAgents(str(tmp_path), factory=lambda _n: provider, retry_wait_s=0)
    return await agents.run_role(
        "player",
        provider="opencode",
        model="m",
        prompt="## Контракт фазы\nresume со снимка",
        timeout=10,
        ctx=ValidateCtx(),
        emit=lambda kind, **kw: events.append((kind, kw)),
    )


def _kinds(events) -> list[str]:
    return [kind for kind, _ in events]


def test_limits():
    assert READ_ONLY_LIMIT == 30
    assert KICK_LIMIT == 3


@pytest.mark.asyncio
async def test_silent_player_kicked_then_read_only_loop(tmp_path):
    provider = _Scripted([reads(40), reads(40)])
    events: list = []
    data = await _player(tmp_path, provider, events)
    # вызов оборван на пороге, а не дочитан до конца
    assert provider.yielded == [READ_ONLY_LIMIT, KICK_LIMIT]
    kick = provider.prompts[1]
    assert "resume со снимка" in kick and "Пинок" in kick
    assert _kinds(events).count("kick") == 1
    assert data["status"] == "blocked"
    assert data["blocked_reason"].startswith("read_only_loop")
    assert "read_only_loop" in _kinds(events)


@pytest.mark.asyncio
async def test_kick_and_repair_continue_the_same_session(tmp_path):
    provider = _Scripted([reads(40), [_tool("write", filePath="a.py"), "no json"], [GOOD]])
    events: list = []
    data = await _player(tmp_path, provider, events)
    assert data["status"] == "done"
    assert provider.resumed == ["", "ses_1", "ses_1"]


@pytest.mark.asyncio
async def test_kicked_player_that_writes_finishes(tmp_path):
    provider = _Scripted([reads(40), [_tool("read", filePath="a.py"), _tool("write", filePath="a.py"), GOOD]])
    events: list = []
    data = await _player(tmp_path, provider, events)
    assert _kinds(events).count("kick") == 1
    assert data["status"] == "done"


@pytest.mark.asyncio
async def test_kicked_player_that_writes_may_verify(tmp_path):
    # 06-pult-decisions фаза 2: после пинка игрок правил, запускал pytest —
    # 3 вызова без записи, и ход обрывался посреди проверки (11 раундов из 12)
    verify = reads(READ_ONLY_LIMIT - 1)
    provider = _Scripted([reads(40), [_tool("edit", filePath="a.py")] + verify + [GOOD]])
    events: list = []
    data = await _player(tmp_path, provider, events)
    assert data["status"] == "done"
    assert "read_only_loop" not in _kinds(events)


@pytest.mark.asyncio
async def test_read_only_loop_after_writes_does_not_claim_nothing_written(tmp_path):
    provider = _Scripted([[_tool("write", filePath="a.py")] + reads(40), reads(40)])
    events: list = []
    data = await _player(tmp_path, provider, events)
    assert data["status"] == "blocked"
    assert data["blocked_reason"].startswith("read_only_loop")
    assert "ничего не записано" not in data["blocked_reason"]


@pytest.mark.asyncio
async def test_normal_player_gets_no_kick(tmp_path):
    run = reads(READ_ONLY_LIMIT - 1) + [_tool("edit", filePath="a.py")] + reads(READ_ONLY_LIMIT - 1) + [GOOD]
    provider = _Scripted([run])
    events: list = []
    data = await _player(tmp_path, provider, events)
    assert data["status"] == "done"
    assert "kick" not in _kinds(events)
    assert len(provider.prompts) == 1


@pytest.mark.asyncio
async def test_bash_write_seen_through_tree(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    def bash_write():
        (tmp_path / "new.py").write_text("x = 1\n")
        return _tool("bash", command="cat > new.py")

    run = reads(3) + [bash_write] + reads(READ_ONLY_LIMIT) + [GOOD]
    provider = _Scripted([run])
    events: list = []
    data = await _player(tmp_path, provider, events)
    assert data["status"] == "done"
    assert "kick" not in _kinds(events)


@pytest.mark.asyncio
async def test_empty_stop_without_writes_is_read_only_loop(tmp_path):
    # 231742: оба вызова — чтения и пустой stop, финального текста нет вовсе
    provider = _Scripted([reads(5), reads(5)])
    events: list = []
    data = await _player(tmp_path, provider, events)
    assert data["status"] == "blocked"
    assert data["blocked_reason"].startswith("read_only_loop")


@pytest.mark.asyncio
async def test_judge_is_not_guarded(tmp_path):
    verdict = '{"verdict": "SHIP", "previous": [], "findings": [], "user_question": ""}'
    provider = _Scripted([reads(40) + [verdict]])
    agents = LiveAgents(str(tmp_path), factory=lambda _n: provider, retry_wait_s=0)
    data = await agents.run_role(
        "judge", provider="codex", model="m", prompt="p", timeout=10, ctx=ValidateCtx()
    )
    assert data["verdict"] == "SHIP"
