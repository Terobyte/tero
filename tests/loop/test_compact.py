"""Фаза 1: счётчик контекста (compact). Фаза 2: мягкая смена сессии."""

import json

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


PLAN = """# Demo

Preamble.

## Add greet

- [ ] greet() returns hello
"""


def _repo(tmp_path):
    from tests.loop import fakes as scripted

    return scripted.init_repo(tmp_path, PLAN)


def _cfg(**overrides):
    from src.config import Config

    data = {
        "plan_file": "plan.md",
        "max_rounds": 8,
        "test_timeout_s": 30,
        "escalation_provider": "",
        "escalation_model": "",
        "compact_player_at_tokens": 100,
        "compact_judge_at_tokens": 100,
    }
    data.update(overrides)
    return Config(**data)


async def _run(repo, agents, cfg=None):
    from src.loop.gates import detect
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.phase import run_phase
    from src.loop.plan import load_phases, load_preamble

    cfg = cfg or _cfg()
    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(repo / "plan.md")
    outcome = await run_phase(
        phases[0],
        repo=repo,
        plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"),
        cfg=cfg,
        agents=agents,
        test_command=detect(repo),
        ledger=ledger,
    )
    return outcome, ledger


def _compact_step(tokens=150):
    def step(_role, kwargs):
        kwargs.get("compact_check", lambda _t: False)(tokens)
        return {"__compacted__": True, "tokens": tokens}

    return step


@pytest.mark.asyncio
async def test_compact_player_gets_brief_with_diff(tmp_path, monkeypatch):
    """Stub №1 пишет файл и превышает порог → stub №2 получил промпт с diff."""
    import src.loop.phase as phase_module
    from tests.loop import fakes as scripted

    repo = _repo(tmp_path)
    prompts: list[str] = []
    gate_calls: list = []
    real_run_all = phase_module.run_all

    def counting_run_all(command, repo, timeout_s=900):
        gate_calls.append(1)
        return real_run_all(command, repo=repo, timeout_s=timeout_s)

    monkeypatch.setattr(phase_module, "run_all", counting_run_all)

    def first_player(_role, _kwargs):
        scripted.write_file(repo, "src/greet.py", "def greet():\n    return 'half'\n")
        return {"__compacted__": True, "tokens": 150}

    def second_player(_role, kwargs):
        prompts.append(kwargs.get("prompt") or "")
        scripted.write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        return dict(scripted.PLAYER_DONE)

    agents = scripted.FakeAgents(
        repo,
        [first_player, second_player, scripted.judge_returns(scripted.ship())],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    # Один раунд, гейт — только после второго (дожившего) исполнителя.
    assert outcome.rounds == 1
    assert len(gate_calls) == 1
    assert len(prompts) == 1
    assert "return 'half'" in prompts[0]
    assert "Продолжай, не переписывай готовое" in prompts[0]
    kinds = [
        json.loads(line).get("kind")
        for line in ledger.path.read_text().splitlines()
    ]
    assert "compact" in kinds
    assert kinds.index("compact") < kinds.index("player")
    run_id = ledger.run_dir.name
    ref = f"refs/tero/{run_id}/phase-00-round01-compact1"
    assert "half" in scripted.git(repo, "cat-file", "-p", f"{ref}:src/greet.py")
    # F10: компакт-событие в stream.jsonl несёт проверенный ref — частичная
    # работа видна HUD и командам до дожившего вызова.
    stream_rows = [
        json.loads(line)
        for line in (ledger.run_dir / "stream.jsonl").read_text().splitlines()
    ]
    compacts = [row for row in stream_rows if row.get("type") == "compact"]
    assert len(compacts) == 1
    assert compacts[0]["role"] == "player"
    assert compacts[0]["ref"] == ref
    assert compacts[0]["round"] == 1
    assert "half" in scripted.git(
        repo, "cat-file", "-p", f"{compacts[0]['ref']}:src/greet.py"
    )
    assert compacts[0]["text"]
    assert ref in compacts[0]["text"]
    assert "half" in scripted.git(repo, "cat-file", "-p", f"{ref}:src/greet.py")


def _findings_section(prompt: str) -> str:
    start = prompt.index("## Блокирующие находки\n") + len("## Блокирующие находки\n")
    end = prompt.index("## suite_delta")
    return prompt[start:end]


@pytest.mark.asyncio
async def test_compact_keeps_findings_verbatim(tmp_path):
    """Раунд 2 с открытым замечанием → в промпте после компакта текст дословно."""
    from tests.loop import fakes as scripted

    repo = _repo(tmp_path)
    prompts: list[str] = []

    def compacting_player(_role, kwargs):
        prompts.append(kwargs.get("prompt") or "")
        scripted.write_file(repo, "src/greet.py", "def greet():\n    return 'half'\n")
        return {"__compacted__": True, "tokens": 150}

    def ok_player(_role, kwargs):
        prompts.append(kwargs.get("prompt") or "")
        scripted.write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        return dict(scripted.PLAYER_DONE)

    verdict = scripted.no_ship([scripted.finding(problem="верни ровно hello")])
    agents = scripted.FakeAgents(
        repo,
        [
            scripted.player_writes(
                repo, "src/greet.py", "def greet():\n    return 'a'\n"
            ),
            scripted.judge_returns(verdict),
            compacting_player,
            ok_player,
            scripted.judge_returns(scripted.ship()),
        ],
    )
    outcome, _ = await _run(repo, agents)
    assert outcome.status == "ship"
    assert len(prompts) == 2
    # F3: полное представление находки — до и после компакта один в один.
    finding_line = "F1: верни ровно hello → returns hello"
    assert finding_line in prompts[0]
    assert _findings_section(prompts[0]) == _findings_section(prompts[1])


@pytest.mark.asyncio
async def test_compact_does_not_grow_round_or_kmax(tmp_path):
    """Компакт → round тот же, счётчик падений и K_max не изменились.

    F2: как `test_kmax_two_flat_rounds_clean_retry`, но в раунд 2 вшит
    компакт. Если бы компакт считался шагом K_max, чистая попытка
    случилась бы уже после вердикта раунда 2; по контракту она —
    после двух NO_SHIP подряд (вердикт раунда 3), компакт в счёт не идёт.
    """
    from src.loop.state import RunState
    from tests.loop import fakes as scripted

    repo = _repo(tmp_path)
    prompts: list[str] = []
    prewrite: list = []

    def crash_step(_role, kwargs):
        prompts.append(kwargs.get("prompt") or "")
        scripted.write_file(repo, "src/greet.py", "v1-crash\n")
        return {"__unavailable__": True, "error": "down"}

    def compacting_player(_role, kwargs):
        prompts.append(kwargs.get("prompt") or "")
        target = repo / "src/greet.py"
        prewrite.append(target.read_text() if target.exists() else None)
        scripted.write_file(repo, "src/greet.py", "v2\n")
        return {"__compacted__": True, "tokens": 150}

    def player_step(content):
        def step(_role, kwargs):
            prompts.append(kwargs.get("prompt") or "")
            target = repo / "src/greet.py"
            prewrite.append(target.read_text() if target.exists() else None)
            scripted.write_file(repo, "src/greet.py", content)
            return dict(scripted.PLAYER_DONE)

        return step

    same_bad = lambda problem: scripted.no_ship(
        [scripted.finding(file="src/greet.py", line=7, problem=problem)]
    )
    agents = scripted.FakeAgents(
        repo,
        [
            crash_step,
            compacting_player,
            player_step("v2\n"),
            scripted.judge_returns(same_bad("round2 bad")),
            player_step("v3\n"),
            scripted.judge_returns(same_bad("round3 bad still")),
            player_step("v4\n"),
            scripted.judge_returns(scripted.ship()),
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    # Компакт — не раунд: раундов 4, вызовов player 5 (компакт + доживший).
    assert outcome.rounds == 4
    assert agents.calls == ["player", "player", "player", "judge",
                            "player", "judge", "player", "judge"]
    # Чистая попытка после двух плоских NO_SHIP (вердикт раунда 3):
    # дерево откачено, блок продолжения из раунда 4 убран.
    assert "Продолжай, не переписывай готовое" in prompts[1]
    assert "Продолжай, не переписывай готовое" in prompts[2]
    assert "Продолжай, не переписывай готовое" in prompts[3]
    assert "Продолжай, не переписывай готовое" not in prompts[4]
    # F2/F8: компакт — не падение (player_crashes сброшен дожившим
    # вызовом) и не шаг K_max: чистая попытка только после двух NO_SHIP
    # подряд (вердикты раундов 2 и 3), а не после вердикта раунда 2
    # + компакта. K_max меняется только вердиктом NO_SHIP в продолжении.
    assert prewrite == ["v1-crash\n", "v2\n", "v2\n", None]
    stream_kinds = [
        json.loads(line).get("kind")
        for line in ledger.path.read_text().splitlines()
    ]
    assert "clean_retry" in stream_kinds
    assert outcome.rounds == 4
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    stream_rows = [
        json.loads(line)
        for line in (ledger.run_dir / "stream.jsonl").read_text().splitlines()
    ]
    compacts = [row for row in stream_rows if row.get("type") == "compact"]
    assert len(compacts) == 1
    assert compacts[0]["round"] == 2
    assert compacts[0]["role"] == "player"
    # F2/F8/F10: компакт — не падение и не шаг K_max: счётчики на момент
    # события не тронуты (падение раунда 1 уже сброшено дожившим вызовом
    # раунда 2, K_max-шага ещё не было — первый NO_SHIP только в раунде 2).
    ledger_rows = [
        json.loads(line)
        for line in ledger.path.read_text().splitlines()
        if json.loads(line).get("kind") == "compact"
    ]
    assert len(ledger_rows) == 1
    # F2/F8: на момент компакта в раунде 2 K_max-шага ещё не было (первый
    # NO_SHIP только в вердикте этого раунда); crashes==1 — это падение
    # раунда 1, компакт счётчик не растил (второй вызов до сброса идёт с
    # тем же значением, без +1 за компакт).
    assert ledger_rows[0]["crashes"] == 1
    assert ledger_rows[0]["kmax_flat"] == 0
    # F10: компакт-событие несёт проверенный ref — второй вызов идёт с
    # diff именно этого ref, частичная работа видна до дожившего вызова.
    compact_ref = compacts[0]["ref"]
    assert compact_ref.endswith("-compact1")
    assert "v2" in scripted.git(repo, "cat-file", "-p", f"{compact_ref}:src/greet.py")


@pytest.mark.asyncio
async def test_compact_snapshot_failure_no_bare_retry(tmp_path, monkeypatch):
    """F1: снимок компакта не записался → нового вызова без блока работы нет."""
    import src.loop.snapshot as snapshot_module
    from tests.loop import fakes as scripted

    repo = _repo(tmp_path)
    real_take = snapshot_module.take_snapshot
    calls = {"n": 0}

    def flaky(repo_path, base, ref, deny=None):
        calls["n"] += 1
        if calls["n"] == 1:
            # Роняем именно компактный снимок (первый take в раунде).
            raise RuntimeError("snapshot exploded")
        return real_take(repo_path, base, ref, deny=deny)

    monkeypatch.setattr(snapshot_module, "take_snapshot", flaky)

    prompts: list[str] = []

    def compacting_player(_role, _kwargs):
        scripted.write_file(repo, "src/greet.py", "def greet():\n    return 'half'\n")
        return {"__compacted__": True, "tokens": 150}

    def ok_player(_role, kwargs):
        prompts.append(kwargs.get("prompt") or "")
        scripted.write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        return dict(scripted.PLAYER_DONE)

    agents = scripted.FakeAgents(
        repo,
        [
            compacting_player,
            ok_player,
            scripted.judge_returns(scripted.ship()),
        ],
    )
    outcome, ledger = await _run(repo, agents)
    # F5/F9: без проверенного ref новый вызов невозможен — ни в том же
    # раунде, ни в следующем. Гейт и судья не запускаются, компакт-события
    # нет, игрок не считается упавшим (player_crashes не растёт):
    # останавливаемся вместо повтора без снимка.
    assert outcome.status == "stop"
    assert outcome.reason == "snapshot_failed"
    assert agents.calls == ["player"]
    assert outcome.rounds == 1
    kinds = [
        json.loads(line).get("kind")
        for line in ledger.path.read_text().splitlines()
    ]
    assert "compact" not in kinds
    assert "snapshot_failed" in kinds
    assert "player" not in kinds
    assert len(prompts) == 0
    state = __import__("src.loop.state", fromlist=["RunState"]).RunState.read(
        ledger.run_dir / "state.json"
    )
    assert state is not None
    assert state.get("status") == "stopped"
    assert state.get("reason") == "snapshot_failed"


@pytest.mark.asyncio
async def test_compact_judge_snapshot_failure_counts_as_failure(tmp_path, monkeypatch):
    import src.loop.snapshot as snapshot_module
    from tests.loop import fakes as scripted

    repo = _repo(tmp_path)
    real_take = snapshot_module.take_snapshot
    calls = {"n": 0}

    def flaky(repo_path, base, ref, deny=None):
        calls["n"] += 1
        if "judge-compact" in ref:
            raise RuntimeError("snapshot exploded")
        return real_take(repo_path, base, ref, deny=deny)

    monkeypatch.setattr(snapshot_module, "take_snapshot", flaky)

    agents = scripted.FakeAgents(
        repo,
        [
            scripted.player_writes(
                repo, "src/greet.py", "def greet():\n    return 'hello'\n"
            ),
            lambda _r, _k: {"__compacted__": True, "tokens": 200},
            scripted.judge_returns(scripted.ship()),
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    # F12: проверенный ref раунда уже есть — дополнительный обязательный
    # снимок не нужен: повтор вызова на том же ref, без сбоя судьи.
    assert agents.calls == ["player", "judge", "judge"]
    kinds = [
        json.loads(line).get("kind")
        for line in ledger.path.read_text().splitlines()
    ]
    assert "compact" in kinds
    assert "judge_failed" not in kinds


@pytest.mark.asyncio
async def test_compact_judge_second_compact_is_single_event(tmp_path):
    """F7: второй __compacted__ судьи в том же раунде → одно compact-событие."""
    from tests.loop import fakes as scripted

    repo = _repo(tmp_path)
    agents = scripted.FakeAgents(
        repo,
        [
            scripted.player_writes(
                repo, "src/greet.py", "def greet():\n    return 'hello'\n"
            ),
            lambda _r, _k: {"__compacted__": True, "tokens": 200},
            lambda _r, _k: {"__compacted__": True, "tokens": 250},
            scripted.judge_returns(scripted.ship()),
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    # Первый компакт — повторный вызов; второй — уже сбой судьи
    # (judge_failed), дальше обычный повтор после сбоя.
    assert agents.calls == ["player", "judge", "judge", "judge"]
    rows = [
        json.loads(line) for line in ledger.path.read_text().splitlines()
    ]
    kinds = [row.get("kind") for row in rows]
    assert kinds.count("compact") == 1
    assert "judge_failed" in kinds
    stream_rows = [
        json.loads(line)
        for line in (ledger.run_dir / "stream.jsonl").read_text().splitlines()
    ]
    compacts = [row for row in stream_rows if row.get("type") == "compact"]
    assert len(compacts) == 1
    assert compacts[0]["role"] == "judge"
    assert compacts[0]["round"] == 1
    assert compacts[0]["text"]
    assert compacts[0]["ref"] in compacts[0]["text"]


@pytest.mark.asyncio
async def test_compact_capped_per_round(tmp_path):
    """Исполнитель превышает порог каждый вызов → 3 компакта, 4-й доживает."""
    from tests.loop import fakes as scripted

    repo = _repo(tmp_path)
    checks: list = []

    def always_compact(_role, kwargs):
        check = kwargs.get("compact_check")
        checks.append(check)
        if check is None:
            scripted.write_file(
                repo, "src/greet.py", "def greet():\n    return 'hello'\n"
            )
            return dict(scripted.PLAYER_DONE)
        return {"__compacted__": True, "tokens": 150}

    agents = scripted.FakeAgents(
        repo,
        [
            always_compact,
            always_compact,
            always_compact,
            always_compact,
            scripted.judge_returns(scripted.ship()),
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    assert outcome.rounds == 1
    kinds = [
        json.loads(line).get("kind")
        for line in ledger.path.read_text().splitlines()
    ]
    assert kinds.count("compact") == 3
    # Первые три вызова — с compact_check, четвёртый — без (доживает).
    assert checks[:3] != [None, None, None]
    assert all(callable(check) for check in checks[:3])
    assert checks[3] is None


@pytest.mark.asyncio
async def test_compact_judge_restarts_on_same_ref(tmp_path):
    """Судья вернул __compacted__ → второй вызов судьи, исполнитель не вызван.

    F4: compact_ref из события совпадает с ref раунда в state.json
    (судья перезапущен на том же ref), дерево в ref — работа исполнителя
    без подмены, и второй вызов судьи видит то же дерево.
    """
    from src.loop import snapshot as snapshot_module
    from src.loop.state import RunState
    from tests.loop import fakes as scripted

    repo = _repo(tmp_path)
    seen_trees: list[str] = []

    def judge_step(role, kwargs):
        assert role == "judge"
        seen_trees.append(
            snapshot_module.tree_of(repo, scripted.git(repo, "rev-parse", "HEAD").strip())
        )
        return scripted.ship()

    agents = scripted.FakeAgents(
        repo,
        [
            scripted.player_writes(
                repo, "src/greet.py", "def greet():\n    return 'hello'\n"
            ),
            lambda _r, _k: {"__compacted__": True, "tokens": 200},
            judge_step,
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship"
    assert agents.calls == ["player", "judge", "judge"]
    rows = [
        json.loads(line) for line in ledger.path.read_text().splitlines()
    ]
    kinds = [row.get("kind") for row in rows]
    assert kinds.count("compact") == 1
    assert "judge_failed" not in kinds
    compact_row = next(row for row in rows if row.get("kind") == "compact")
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    assert compact_row.get("role") == "judge"
    assert compact_row.get("round") == 1
    # Перезапуск — на том же дереве: compact-снимок и ref раунда —
    # одно дерево, работа исполнителя без подмены.
    round_ref = state.get("snapshot_ref")
    assert round_ref
    compact_tree = snapshot_module.ref_tree(repo, compact_row["ref"])
    round_tree = snapshot_module.ref_tree(repo, round_ref)
    assert compact_tree == round_tree
    assert "hello" in scripted.git(repo, "cat-file", "-p", f"{round_tree}:src/greet.py")
    assert len(seen_trees) == 1
    assert seen_trees[0] == round_tree
    stream_rows = [
        json.loads(line)
        for line in (ledger.run_dir / "stream.jsonl").read_text().splitlines()
    ]
    compacts = [row for row in stream_rows if row.get("type") == "compact"]
    assert len(compacts) == 1
    assert compacts[0]["text"]
    assert compacts[0]["ref"] in compacts[0]["text"]
