"""Виды ошибок провайдера: классификация и таймаут без «провайдер недоступен»."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import pytest

from src.config import Config
from src.errors import ProviderStalled, classify_error
from src.loop.agents import LiveAgents
from src.loop.gates import detect
from src.loop.ledger import Ledger, new_run_dir
from src.loop.phase import run_phase
from src.loop.schemas import ValidateCtx
from src.loop.state import RunState
from tests.loop import fakes as scripted


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


_STALL_PROVIDERS = ("claude", "codex", "opencode", "cursor", "agy")


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
    """Провайдер с `streams = False` не получает `stall_timeout` (F4).

    Проверяется _default_factory путь: он вливает глобальный stall_timeout_s
    в streaming-провайдеры — exercise его с настроенным глобальным значением.
    """
    import src.config as config_mod
    import src.loop.agents as agents_mod
    import src.providers as providers_mod

    class _QuietProvider:
        streams = False

        def __init__(self):
            from types import SimpleNamespace

            self.config = SimpleNamespace()

    _orig_create = providers_mod.create_provider

    def _fake_create(name, cfg=None):
        if name == "quiet":
            return _QuietProvider()
        return _orig_create(name, cfg)

    monkeypatch.setattr(providers_mod, "create_provider", _fake_create)
    monkeypatch.setattr(
        config_mod,
        "load_provider_configs",
        lambda _wd, **_kw: {"quiet": {"command": "quiet"}},
    )
    monkeypatch.setattr(
        config_mod,
        "resolve_config",
        lambda _cli: type("C", (), {"stall_timeout_s": 42})(),
    )
    quiet = agents_mod._default_factory(str(tmp_path))("quiet")
    assert quiet.streams is False
    assert not hasattr(quiet.config, "stall_timeout_s")
    # Контроль: claude остаётся streaming и получает глобальный stall.
    monkeypatch.setattr(
        config_mod,
        "load_provider_configs",
        lambda _wd, **_kw: {"claude": {"command": "claude"}},
    )
    streaming = agents_mod._default_factory(str(tmp_path))("claude")
    assert streaming.streams is True
    assert streaming.config.stall_timeout_s == 42


class _FailThenSucceed:
    """Provider stub: fail N times with `exc`, then yield valid player JSON."""

    def __init__(self, exc_factory, fails: int, payload: str = ""):
        self._exc_factory = exc_factory
        self.calls = 0
        self._fails = fails
        self._payload = payload or (
            '{"status": "done", "changes": [{"file": "a.py", "what": "w", "why": "y"}], '
            '"start_here": [], "blocked_reason": ""}'
        )

    async def run(self, prompt, system_prompt, working_dir, model="", **kwargs):
        self.calls += 1
        if self.calls <= self._fails:
            raise self._exc_factory()
        yield self._payload


@pytest.mark.asyncio
async def test_network_error_backoff_schedule(tmp_path, monkeypatch):
    import src.loop.agents as agents_mod

    monkeypatch.setattr(agents_mod, "RETRY_DELAYS_S", (60, 120, 300))
    sleeps: list[float] = []

    async def _sleep(self, wait_s: float) -> None:
        sleeps.append(wait_s)

    monkeypatch.setattr(LiveAgents, "_retry_sleep", _sleep)
    provider = _FailThenSucceed(lambda: RuntimeError("ECONNRESET"), 3)
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
    assert data["status"] == "done"
    assert provider.calls == 4
    assert sleeps == [60, 120, 300]


@pytest.mark.asyncio
async def test_network_retries_capped(tmp_path, monkeypatch):
    import src.loop.agents as agents_mod

    monkeypatch.setattr(agents_mod, "RETRY_DELAYS_S", (60, 120))
    monkeypatch.setattr(agents_mod, "RETRY_DEADLINE_S", 7200)

    async def _sleep(self, _wait_s: float) -> None:
        pass

    monkeypatch.setattr(LiveAgents, "_retry_sleep", _sleep)
    provider = _FailThenSucceed(lambda: ConnectionError("reset by peer"), 99)
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
    assert data["__unavailable__"] is True
    assert data["kind"] == "network"
    assert provider.calls == 3  # 1 + len(RETRY_DELAYS_S)


@pytest.mark.asyncio
async def test_network_retries_stop_at_deadline(tmp_path, monkeypatch):
    import src.loop.agents as agents_mod

    monkeypatch.setattr(agents_mod, "RETRY_DELAYS_S", (100, 100, 100))
    monkeypatch.setattr(agents_mod, "RETRY_DEADLINE_S", 250)
    clock = {"now": 1000.0}
    monkeypatch.setattr(agents_mod, "_now", lambda: clock["now"])

    async def _sleep(self, wait_s: float) -> None:
        clock["now"] += wait_s

    monkeypatch.setattr(LiveAgents, "_retry_sleep", _sleep)
    provider = _FailThenSucceed(lambda: ConnectionError("reset by peer"), 99)
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
    assert data["__unavailable__"] is True
    assert data["kind"] == "network"
    assert clock["now"] - 1000.0 <= 250
    assert provider.calls == 3  # 1 + 2 повтора: третий упёрся в дедлайн


@pytest.mark.asyncio
async def test_auth_error_stops_without_waiting(tmp_path, monkeypatch):
    import src.loop.agents as agents_mod

    async def _sleep(_wait_s: float) -> None:  # pragma: no cover - не ждём
        raise AssertionError("auth must not sleep")

    monkeypatch.setattr(agents_mod.asyncio, "sleep", _sleep)
    provider = _FailThenSucceed(lambda: RuntimeError("HTTP 401 Unauthorized"), 99)
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
    assert data["kind"] == "auth"

    from src.config import Config
    from src.loop.gates import detect
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.phase import run_phase
    from src.loop.state import RunState
    from tests.loop import fakes as scripted

    repo = scripted.init_repo(
        tmp_path,
        "# Demo\n\nPreamble.\n\n## Add greet\n\n- [ ] greet() returns hello\n",
    )

    class _AuthAgents:
        calls: list[str] = []

        async def run_role(self, role: str, **kwargs):
            type(self).calls.append(role)
            return {"__unavailable__": True, "error": "HTTP 401 Unauthorized", "kind": "auth"}

    from src.loop.plan import load_phases, load_preamble

    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(repo / "plan.md")
    cfg = Config(plan_file="plan.md", max_rounds=8, test_timeout_s=30)
    auth_agents = _AuthAgents()
    outcome = await run_phase(
        phases[0],
        repo=repo,
        plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"),
        cfg=cfg,
        agents=auth_agents,
        test_command=detect(repo),
        ledger=ledger,
    )
    assert outcome.status == "stop"
    assert outcome.reason == "provider_auth"
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    assert state.get("status") == "stopped"
    text = state.get("text") or ""
    assert "нужен вход" in text
    assert state.get("snapshot_ref"), "halt делает снимок"
    assert auth_agents.calls == ["player"]


@pytest.mark.asyncio
async def test_judge_quota_goes_judge_pending_at_once(tmp_path):
    from src.config import Config
    from src.loop.gates import detect
    from src.loop.ledger import Ledger, new_run_dir
    from src.loop.phase import run_phase
    from src.loop.state import RunState
    from tests.loop import fakes as scripted

    repo = scripted.init_repo(
        tmp_path,
        "# Demo\n\nPreamble.\n\n## Add greet\n\n- [ ] greet() returns hello\n",
    )
    player_calls: list[str] = []

    def _player(_role, _kwargs):
        player_calls.append("player")
        scripted.write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        return dict(scripted.PLAYER_DONE)

    class _QuotaJudge:
        calls: list[str] = []

        async def run_role(self, role: str, **kwargs):
            assert role == "judge"
            type(self).calls.append(role)
            return {"__unavailable__": True, "error": "quota exceeded", "kind": "quota"}

    from src.loop.plan import load_phases, load_preamble

    judge_agents = _QuotaJudge()

    class _Both:
        async def run_role(self, role: str, **kwargs):
            if role == "player":
                return _player(role, kwargs)
            return await judge_agents.run_role(role, **kwargs)

    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(repo / "plan.md")
    cfg = Config(plan_file="plan.md", max_rounds=8, test_timeout_s=30)
    outcome = await run_phase(
        phases[0],
        repo=repo,
        plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"),
        cfg=cfg,
        agents=_Both(),
        test_command=detect(repo),
        ledger=ledger,
    )
    assert outcome.status == "judge_pending"
    assert outcome.reason == "judge_quota"
    assert len(player_calls) == 1
    assert len(judge_agents.calls) == 1
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    assert state.get("status") == "judge_pending"


_RESILIENCE_PLAN = """# Demo

Preamble.

## Add greet

- [ ] greet() returns hello
"""


def _repo(tmp_path):
    return scripted.init_repo(tmp_path, _RESILIENCE_PLAN)


def _cfg(**overrides):
    data = {"plan_file": "plan.md", "max_rounds": 8, "test_timeout_s": 30}
    data.update(overrides)
    return Config(**data)


async def _run_phase(repo, agents, cfg=None):
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


def _needs_user(question="which greeting?"):
    return {
        "verdict": "NEEDS_USER",
        "previous": [],
        "findings": [],
        "user_question": question,
    }


def _ledger_rows(ledger):
    return [json.loads(line) for line in ledger.path.read_text().splitlines()]


def _stream_rows(ledger):
    return [
        json.loads(line)
        for line in (ledger.run_dir / "stream.jsonl").read_text().splitlines()
    ]


@pytest.mark.asyncio
async def test_needs_user_reask_then_fresh_then_pending(tmp_path):
    repo = _repo(tmp_path)
    prompts: list[str] = []

    def judge_nu(role, kwargs):
        assert role == "judge"
        prompts.append(kwargs.get("prompt") or "")
        return dict(_needs_user())

    agents = scripted.FakeAgents(
        repo,
        [
            scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_nu,
            judge_nu,
            judge_nu,
        ],
    )
    outcome, ledger = await _run_phase(
        repo, agents, _cfg(escalation_provider="", escalation_model="")
    )
    assert outcome.status == "judge_pending"
    assert outcome.reason == "needs_user"
    assert agents.calls == ["player", "judge", "judge", "judge"]
    assert len(prompts) == 3
    q = "which greeting?"
    assert q in prompts[1]
    assert "Человека нет" in prompts[1]
    assert "NEEDS_USER" in prompts[1]
    assert q not in prompts[0]
    assert "Человека нет" not in prompts[2]
    assert (repo / "src/greet.py").exists()
    state = RunState.read(ledger.run_dir / "state.json")
    assert state is not None
    assert state.get("status") == "judge_pending"
    assert state.get("reason") == "needs_user"
    assert q in (state.get("text") or "")
    assert "tero review" in (state.get("text") or "")
    assert "tero resume" in (state.get("text") or "")
    kinds = [row.get("kind") for row in _ledger_rows(ledger)]
    assert "rollback" not in kinds
    assert "escalate" not in kinds
    needs = [row for row in _ledger_rows(ledger) if row.get("kind") == "needs_user"]
    assert [row.get("attempt") for row in needs] == [1, 2, 3]
    assert all(row.get("question") == q for row in needs)
    stream_needs = [row for row in _stream_rows(ledger) if row.get("type") == "needs_user"]
    assert len(stream_needs) == 3
    for row in stream_needs:
        for key in ("phase", "round", "role", "text"):
            assert row.get(key), key


@pytest.mark.asyncio
async def test_needs_user_then_ship(tmp_path):
    repo = _repo(tmp_path)
    agents = scripted.FakeAgents(
        repo,
        [
            scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            scripted.judge_returns(_needs_user()),
            scripted.judge_returns(scripted.ship()),
        ],
    )
    outcome, _ = await _run_phase(
        repo, agents, _cfg(escalation_provider="", escalation_model="")
    )
    assert outcome.status == "ship"
    assert agents.calls == ["player", "judge", "judge"]


@pytest.mark.asyncio
async def test_needs_user_with_escalation_unchanged(tmp_path):
    repo = _repo(tmp_path)
    agents = scripted.FakeAgents(
        repo,
        [
            scripted.player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            scripted.judge_returns(_needs_user()),
        ],
    )
    outcome, _ = await _run_phase(
        repo, agents, _cfg(escalation_provider="codex", escalation_model="")
    )
    assert outcome.status == "stop"
    assert outcome.reason == "user"
    assert outcome.user_question == "which greeting?"


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
