"""Shared context of one phase: its inputs, IO, state and the step helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

from src.config import Config
from src.errors import scrub_data, scrub_secrets
from src.loop.gates import TestCommand
from src.loop.ledger import Ledger
from src.loop.plan import Phase
from src.loop.state import RunState
from src.loop.stream import Stream
from src.loop.guards.suite import Entry
from src.relay.iface import NoopRelay


class _Continuation(Exception):
    """Путь продолжения: ситуационная эскалация не применяется."""


class _Next(Enum):
    SHIP = auto()
    USER = auto()
    STOP = auto()
    TESTER = auto()
    NEXT = auto()
    JUDGE_PENDING = auto()


_JUDGE_MAX_FAILURES = 3
_NEEDS_USER_MAX_ATTEMPTS = 3

# K_max: столько подряд раундов-продолжений с неуменьшившимися замечаниями
# судьи — и вместо stuck чистая попытка с checkpoint.
K_MAX = 2


@dataclass
class OpenFinding:
    id: str
    severity: str
    route: str
    file: str
    line: int | None
    problem: str
    expected: str
    level: str = ""
    test_paths: list[str] = field(default_factory=list)
    note: str = ""

    def as_prompt(self) -> str:
        extra = f" [test: {', '.join(self.test_paths)}]" if self.test_paths else ""
        note = f" ({self.note})" if self.note else ""
        return f"{self.id}: {self.problem} → {self.expected}{extra}{note}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "route": self.route,
            "level": self.level,
            "file": self.file,
            "line": self.line,
            "problem": self.problem,
            "expected": self.expected,
            "test_paths": self.test_paths,
        }


@dataclass
class PhaseOutcome:
    status: str
    reason: str
    user_question: str = ""
    rounds: int = 0
    commit_sha: str = ""
    escalated: bool = False
    stop_text: str = ""
    decider: Any = None
    answer: str = ""
    extra: Any = None


@dataclass
class ProtectedTest:
    """A tester's red test, restored byte-for-byte if the player touches it."""

    digest: str
    content: str


@dataclass
class PhaseState:
    round: int = 0
    escalated: bool = False
    judge_failures: int = 0
    needs_user_attempts: int = 0
    pending_reason: str = ""
    last_provider: str = ""
    stop_reason: str = ""
    user_question: str = ""
    last_provider_error: str = ""
    last_provider_kind: str = "other"
    player_crashes: int = 0
    resume_ref: str = ""
    resume_base: str = ""
    resume_active: bool = False
    answer: str = ""
    answer_from_phone: bool = False
    had_crash: bool = False
    kmax_flat: int = 0
    kmax_prev: tuple[int, frozenset] | None = None
    open_findings: list[OpenFinding] = field(default_factory=list)
    debt_pending: list[dict[str, Any]] = field(default_factory=list)
    next_id: int = 1
    suite_delta: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    gate_output: str = ""
    last_red_fp: frozenset[str] | None = None
    last_red_tree: str | None = None
    gate_scope: str = ""
    gate_files: list[str] = field(default_factory=list)
    gate_command: str = ""
    gate_exit_code: int = 0
    last_green_full_tree: str | None = None
    protected: dict[str, ProtectedTest] = field(default_factory=dict)
    protected_by_id: dict[str, list[str]] = field(default_factory=dict)
    compact_count: int = 0
    judge_compact_used: bool = False


@dataclass
class PhaseContext:
    """What every step of one phase shares: inputs, IO and the mutable state."""

    phase: Phase
    total_phases: int
    repo: Path
    plan_path: Path
    preamble: str
    cfg: Config
    agents: Any
    test_command: TestCommand | None
    tester_enabled: bool
    ledger: Ledger
    run_dir: Path
    state_io: RunState
    stream: Stream
    pulse: Any
    checkpoint: str
    contract: str
    suite_hashes: dict[str, str]
    guarded: dict[str, Entry]
    link_targets: set[str]
    st: PhaseState
    # Ретранслятор прогона (план 08-6): один на прогон, создаётся в phase.py.
    relay: Any = None
    # the role of the last step: emit() fills it into events without one
    role: str = ""

    def __post_init__(self) -> None:
        # start_answer несёт только текст: источник (телефон) — из state.json,
        # чтобы телефонный текст не попал к судье и после перезапуска раунда.
        if self.st.answer and not self.st.answer_from_phone:
            saved = RunState.read(Path(self.run_dir) / "state.json") or {}
            self.st.answer_from_phone = bool(
                saved.get("answer_from_phone") and saved.get("answer") == self.st.answer)
        if self.relay is None:
            try:
                from src.relay.iface import make_relay

                made = make_relay(self.cfg, run_id=Path(self.run_dir).name,
                                  run_dir=self.run_dir)
            except Exception:
                made = None
            self.relay = made if made is not None else NoopRelay()
        try:
            from src.relay.pusher import ensure_pusher

            ensure_pusher(self.run_dir, self.relay)
        except Exception:
            pass
        try:
            from src.relay.poller import ensure_poller

            ensure_poller(self.run_dir, self.relay)
        except Exception:
            pass

    def emit(self, type: str, **fields: Any) -> None:
        fields.setdefault("phase", self.phase.title)
        fields.setdefault("round", self.st.round)
        if fields.get("role"):
            self.role = fields["role"]
        else:
            fields["role"] = self.role
        if "text" not in fields:
            fields["text"] = _stream_text(type, fields)
        clean = {k: scrub_data(v) for k, v in fields.items()}
        self.stream.emit(type, **clean)

    def update_state(self, **fields: Any) -> None:
        if fields.get("role"):
            self.role = fields["role"]
        clean = {k: scrub_data(v) for k, v in fields.items()}
        self.state_io.update(**clean)

    def progress(self) -> None:
        pulse = self.pulse
        if pulse is None:
            return
        try:
            pulse.progress()
        except Exception:
            pass

    def tag(self) -> str:
        return f"[фаза {self.phase.index + 1}/{self.total_phases} · раунд {self.st.round}]"

    def round_file(self, name: str) -> str:
        return f"phase-{self.phase.index:02d}/round-{self.st.round:02d}/{name}"

    def check_reply(
        self, role: str, data: Any, provider: str = ""
    ) -> dict[str, Any] | None:
        """None when the provider is unavailable. An invalid reply is logged and still returned."""
        if not isinstance(data, dict) or data.get("__unavailable__"):
            self.provider_error(role, data, provider)
            return None
        if data.get("__invalid__"):
            self.invalid(role, data)
        return data

    def provider_text(self, role: str, provider: str) -> str:
        kind = self.st.last_provider_kind
        error = self.st.last_provider_error
        if kind == "auth":
            return f"`{provider}`: нужен вход (`{error}`)"
        if kind == "quota":
            return f"`{provider}`: квота или лимит (`{error}`)"
        return error

    def invalid(self, role: str, data: dict[str, Any]) -> None:
        errors = [str(item) for item in data.get("errors") or []]
        raw = data.get("raw") or ""
        self.ledger.event("invalid", role=role, round=self.st.round, errors=errors)
        self.ledger.artifact(
            self.round_file(f"{role}.invalid.txt"),
            "ошибки:\n" + "\n".join(errors) + "\n\nответ:\n" + (raw or "(пусто)") + "\n",
        )
        print(f"  {role}: ответ не по схеме — {'; '.join(errors[:3])}", flush=True)

    def provider_error(
        self, role: str, data: Any, provider: str = ""
    ) -> None:
        error = scrub_secrets(data.get("error", "") if isinstance(data, dict) else "")
        kind = data.get("kind", "other") if isinstance(data, dict) else "other"
        if kind not in ("auth", "quota", "network", "timeout", "stall", "other"):
            kind = "other"
        self.st.last_provider_error = error
        self.st.last_provider_kind = kind
        self.st.last_provider = provider
        self.ledger.event("provider_error", role=role, round=self.st.round, error=error)
        if kind in ("network", "other"):
            print(f"  {role}: провайдер недоступен — {error}", flush=True)
        else:
            print(f"  {role}: {error}", flush=True)

    def player_identity(self) -> tuple[str, str, str]:
        # player_schedule: игрок раунда — по расписанию, без эскалаций.
        schedule = str(getattr(self.cfg, "player_schedule", "") or "")
        if schedule:
            from src.schedule import schedule_player as _sched

            got = _sched(schedule, self.st.round)
            if got is not None:
                return got[1], got[0], ""
        # situational: игрок — по diagnose прошлого раунда (situation_step.next_player).
        try:
            mode = str(getattr(self.cfg, "escalation", "round") or "round")
        except Exception:
            mode = "round"
        if mode == "situational":
            try:
                from src.loop.findings import in_continuation as _in_cont

                if _in_cont(self.st):
                    raise _Continuation()
            except _Continuation:
                return self.cfg.player_model, self.cfg.player_provider, self.cfg.player_effort
            except Exception:
                pass
            else:
                try:
                    from src.loop.situation_step import next_player as _next_player

                    got = _next_player(self)
                    if got[0] and got[1]:
                        return got
                except Exception:
                    pass
            return self.cfg.player_model, self.cfg.player_provider, self.cfg.player_effort
        # escalate_at_round: эскалация ровно на этом раунде, до и после — игрок.
        at = self.cfg.escalate_at_round
        if self.cfg.escalation_provider and (self.st.round == at if at else self.st.escalated):
            return (
                self.cfg.escalation_model,
                self.cfg.escalation_provider,
                self.cfg.escalation_effort,
            )
        return self.cfg.player_model, self.cfg.player_provider, self.cfg.player_effort


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _tail(text: str, limit: int = 200) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-limit:])


def _stream_text(type: str, fields: dict[str, Any]) -> str:
    if type == "tool":
        return str(fields.get("text", ""))
    if type == "text":
        return str(fields.get("text", ""))
    if type == "gate":
        if fields.get("result") == "skipped":
            return "тесты пропущены (нет команды)"
        tail = str(fields.get("tail", ""))
        head = "тесты: зелёные" if fields.get("ok") else "тесты: красные"
        code = fields.get("exit_code")
        text = f"{head} (код {code})" if code is not None else head
        return f"{text}\n{tail}" if tail else text
    if type == "gate_start":
        return "тесты идут …"
    if type == "verdict":
        return str(fields.get("verdict", "") or "")
    if type == "tester":
        return f"тестов написано {len(fields.get('tests', []) or [])}"
    if type == "ship":
        return f"ship {fields.get('commit', '')}"
    if type == "stop":
        return str(fields.get("text", "") or fields.get("reason", "") or "")
    if type == "start_step":
        return f"раунд {fields.get('round', '')} начат"
    if type == "compact":
        return (
            f"компакт {fields.get('role', '')} "
            f"({fields.get('tokens', 0)} токенов) — "
            f"новый вызов, снимок `{fields.get('ref', '')}`"
        )
    return ""
