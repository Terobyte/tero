"""Фаза 08-2: фильтр секретов и тяжесть находок от jev (красные тесты)."""

import json
import math
import time
from types import SimpleNamespace

import pytest

from src.loop import jev as jev_mod
from src.loop import situation_step
from src.relay.redact import redact


class FakeLedger:
    def __init__(self):
        self.events = []

    def event(self, type, **fields):
        self.events.append({"type": type, **fields})


BENCH_SEV = {
    "blocker": "Breaks the phase contract or the main feature, loses data, hangs, or leaks secrets",
    "major": "Real bug, but only on a rare or error path",
    "minor": "Edge case, weak or missing assertion in a test, small inconsistency",
    "nit": "Wording, formatting, help text, cosmetic or test-style preference",
}


@pytest.fixture(autouse=True)
def _no_secret_paths(monkeypatch):
    import src.loop.git as git_mod

    monkeypatch.setattr(git_mod, "secret_paths", lambda repo: [])


def _ctx(enabled=True):
    emitted = []
    ctx = SimpleNamespace(
        cfg=SimpleNamespace(jev_enabled=enabled),
        phase=SimpleNamespace(title="P1"),
        st=SimpleNamespace(round=3, next_id=1, open_findings=[]),
        repo="",
        ledger=FakeLedger(),
        emit=lambda type, **f: emitted.append({"type": type, **f}),
    )
    return ctx, emitted


def _finding(i, **kw):
    d = {
        "severity": "blocking",
        "route": "player",
        "file": "a.py",
        "line": i,
        "problem": f"problem {i}",
        "expected": f"fix {i}",
    }
    d.update(kw)
    return d


def _post_factory(calls):
    """Ответ в формате бенча: answers[sN]={choice, confidence}, токены в usage."""

    def post(url, headers, body, timeout):
        calls.append((url, headers, body, timeout))
        keys = sorted(body["questions"], key=lambda k: int(k[1:]))
        answers = {k: {"choice": "major", "confidence": 0.1 * i} for i, k in enumerate(keys)}
        answers = dict(reversed(list(answers.items())))
        return 200, {"answers": answers, "usage": {"input_tokens": 5}}

    return post


def test_redact():
    token = "sk-" + "Ab3dEf7h" * 4
    text = "слово тест, падеж! " * 25 + " пароль " + token + " конец " + "хвост дело? " * 25
    out = redact(text, [])
    assert token not in out
    assert len(text) > 500
    assert out.count("слово") == 25 and out.count("хвост") == 25
    assert redact("see src/loop/phase.py and relay/src/worker.js", []) == (
        "see src/loop/phase.py and relay/src/worker.js"
    )
    assert "AKIA" + "A" * 16 not in redact("key " + "AKIA" + "A" * 16 + " end", [])
    assert "BEGIN" not in redact("k -----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY----- e", [])
    no_end = "k -----BEGIN PRIVATE KEY----- tail-material\nmore-material-here e"
    out_no_end = redact(no_end, [])
    assert "BEGIN" not in out_no_end
    assert "tail-material" not in out_no_end
    assert "more-material-here e" in out_no_end
    assert "f" * 64 not in redact("h " + "f" * 64 + " t", [])
    assert "QmFz" + "ZTY0" * 14 not in redact("b " + "QmFz" + "ZTY0" * 14 + " e", [])
    out = redact("leak in config/secrets.yaml here", ["config/secrets.yaml"])
    assert "config/secrets.yaml" not in out
    assert "secrets.yaml" in out


def test_redact_pem_end_label_must_match():
    # F9: END чужой метки не закрывает блок; маскируем до своего END.
    mixed = (
        "k -----BEGIN PRIVATE KEY-----\nabc\n-----END PUBLIC KEY-----\n"
        "after-mismatch-material\n-----END PRIVATE KEY----- e"
    )
    out = redact(mixed, [])
    assert "BEGIN" not in out
    assert "after-mismatch-material" not in out
    assert out == "k [redacted] e"
    # F4/F9: без совпадающего END — маска только до конца строки.
    no_match = "k -----BEGIN PRIVATE KEY----- tail-material\nbenign words here"
    out_nm = redact(no_match, [])
    assert "tail-material" not in out_nm
    assert "benign words here" in out_nm


def test_jev_body_matches_bench():
    calls = []
    problem = "desc " + "x" * 650 + " sk-" + "Ab3dEf7h" * 4
    res = jev_mod.request_sev(
        [_finding(0, problem=problem)],
        phase="P1",
        round_no=3,
        rounds_spent=3,
        n_closed=1,
        secret_paths=[],
        api_key="k",
        post=_post_factory(calls),
    )
    assert res == {"sev": [["major", 0.0]], "tok": 5}
    assert len(calls) == 1
    url, headers, body, _timeout = calls[0]
    assert url == "https://api.typesafe.ai/v1/systemone"
    assert body["model"] == "jev-latest"
    assert "polishing" not in json.dumps(body)
    assert set(body["questions"]) == {"s0"}
    q = body["questions"]["s0"]
    assert q["type"] == "choice"
    assert "problem" not in q and "expected" not in q
    instr = q["instructions"]
    assert "sk-" not in instr
    assert q["criteria"] == BENCH_SEV
    st = body["state"]
    assert st["phase"] == "P1" and st["round"] == 3
    assert st["rounds_already_spent"] == 3
    assert st["findings_fixed_in_earlier_rounds"] == 1
    assert "rounds_spent" not in st and "n_closed" not in st
    assert len(st["open_findings"]) == 1
    assert len(st["open_findings"][0]) <= 600 and "sk-" not in st["open_findings"][0]
    assert headers["Authorization"] == "Bearer k"


def test_jev_splits_over_8_and_sorts_numerically():
    calls = []
    res = jev_mod.request_sev(
        [_finding(i) for i in range(11)],
        phase="P1",
        round_no=1,
        rounds_spent=1,
        n_closed=0,
        secret_paths=[],
        api_key="k",
        post=_post_factory(calls),
    )
    assert len(calls) == 2
    assert sorted(calls[0][2]["questions"], key=lambda k: int(k[1:])) == [
        f"s{i}" for i in range(8)
    ]
    assert sorted(calls[1][2]["questions"], key=lambda k: int(k[1:])) == [
        "s8",
        "s9",
        "s10",
    ]
    assert len(calls[0][2]["state"]["open_findings"]) == 8
    assert len(calls[1][2]["state"]["open_findings"]) == 3
    assert len(res["sev"]) == 11
    assert res["sev"][10] == ["major", 0.2]
    assert res["sev"][8] == ["major", 0.0]
    assert res["sev"][0] == ["major", 0.0]


def test_jev_second_batch_uses_global_indices():
    calls = []

    def post(url, headers, body, timeout):
        calls.append((url, headers, body, timeout))
        answers = {
            k: {"choice": "minor", "confidence": 0.5} for k in body["questions"]
        }
        return 200, {"answers": answers, "usage": {"input_tokens": 5}}

    res = jev_mod.request_sev(
        [_finding(i) for i in range(9)],
        phase="P1",
        round_no=1,
        rounds_spent=1,
        n_closed=0,
        secret_paths=[],
        api_key="k",
        post=post,
    )
    assert len(calls) == 2
    assert set(calls[1][2]["questions"]) == {"s8"}
    assert len(res["sev"]) == 9


def test_jev_skips_discard_test():
    calls = []
    findings = [_finding(i) for i in range(5)]
    findings[2]["route"] = "discard_test"
    res = jev_mod.request_sev(
        findings,
        phase="P1",
        round_no=1,
        rounds_spent=1,
        n_closed=0,
        secret_paths=[],
        api_key="k",
        post=_post_factory(calls),
    )
    assert set(calls[0][2]["questions"]) == {"s0", "s1", "s2", "s3"}
    assert len(res["sev"]) == 4


def test_jev_failure_does_not_block(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")

    def boom(url, headers, body, timeout):
        raise RuntimeError("boom")

    def forbidden(url, headers, body, timeout):
        return 402, {"error": {"message": "nope"}}

    def slow(url, headers, body, timeout):
        time.sleep(0.3)
        return 200, {"answers": {"s0": {"choice": "major", "confidence": 1.0}}, "tok": 1}

    def garbage(url, headers, body, timeout):
        return 200, {"nope": 1}

    ctx, emitted = _ctx()
    res = situation_step.maybe_query_jev(ctx, [_finding(0)], post=boom)
    assert "error" in res
    res = situation_step.maybe_query_jev(ctx, [_finding(0)], post=forbidden)
    assert "error" in res
    res = situation_step.maybe_query_jev(
        ctx, [_finding(0)], post=slow, timeout_s=0.05
    )
    assert "error" in res
    res = situation_step.maybe_query_jev(ctx, [_finding(0)], post=garbage)
    assert "error" in res
    res = jev_mod.request_sev([_finding(0)], api_key="", post=boom)
    assert "error" in res
    for ev in ctx.ledger.events:
        assert ev["type"] == "jev_sev" and "error" in ev
        assert "TYPESAFE" not in json.dumps(ev) and "boom" not in json.dumps(ev)
        assert "nope" not in json.dumps(ev)
    assert emitted and all(e["type"] == "jev_sev" for e in emitted)


def test_jev_rejects_bad_confidence():
    for conf in (float("nan"), float("inf"), -0.1, 1.5, "0.5", None, True, 10**1000):
        def post(url, headers, body, timeout, _c=conf):
            return (
                200,
                {
                    "answers": {"s0": {"choice": "major", "confidence": _c}},
                    "usage": {"input_tokens": 1},
                },
            )

        res = jev_mod.request_sev([_finding(0)], api_key="k", post=post)
        assert "error" in res, conf

    def post_level(url, headers, body, timeout):
        return (
            200,
            {
                "answers": {"s0": {"choice": "critical", "confidence": 0.5}},
                "usage": {"input_tokens": 1},
            },
        )

    assert "error" in jev_mod.request_sev([_finding(0)], api_key="k", post=post_level)


def test_jev_phase_redacted():
    calls = []
    token = "sk-" + "Ab3dEf7h" * 4
    jev_mod.request_sev(
        [_finding(0)],
        phase=f"P1 {token}",
        round_no=1,
        rounds_spent=1,
        n_closed=0,
        secret_paths=[],
        api_key="k",
        post=_post_factory(calls),
    )
    assert token not in json.dumps(calls[0][2])


def test_jev_counts_history_not_only_current_round(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    calls = []
    ctx, _emitted = _ctx()
    ctx.st.next_id = 6
    ctx.st.open_findings = [_finding(0), _finding(1)]
    situation_step.maybe_query_jev(ctx, [_finding(0)], post=_post_factory(calls), n_closed=1)
    st = calls[0][2]["state"]
    assert st["findings_fixed_in_earlier_rounds"] == 4


def test_jev_secret_paths_failure_sends_nothing(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    import src.loop.git as git_mod

    calls = []
    ctx, emitted = _ctx()

    def _raise(repo):
        raise RuntimeError("git down")

    monkeypatch.setattr(git_mod, "secret_paths", _raise)
    res = situation_step.maybe_query_jev(ctx, [_finding(0)], post=_post_factory(calls))
    assert "error" in res and calls == []
    assert len(ctx.ledger.events) == 1
    assert "git down" not in json.dumps(ctx.ledger.events)
    assert emitted and emitted[0]["type"] == "jev_sev"


def test_jev_stream_stays_short(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    calls = []
    ctx, emitted = _ctx()
    res = situation_step.maybe_query_jev(ctx, [_finding(0)], post=_post_factory(calls))
    assert "sev" in res
    assert emitted and all(e["type"] == "jev_sev" for e in emitted)
    for e in emitted:
        assert "sev" not in e and "tok" not in e
        assert e["text"] and e["role"] == "judge"
    led = ctx.ledger.events[-1]
    assert led["sev"] == res["sev"] and led["tok"] == 5


def test_jev_off_by_default():
    from src.config import Config

    assert Config().jev_enabled is False
    calls = []
    ctx, emitted = _ctx(enabled=False)
    assert situation_step.maybe_query_jev(ctx, [_finding(0)], post=_post_factory(calls)) is None
    assert calls == [] and emitted == [] and ctx.ledger.events == []


def test_redact_underscore_and_padding():
    hex_tok = "f" * 64
    assert hex_tok not in redact(f"a_{hex_tok}_b", [])
    b64 = "QmFz" + "ZTY0" * 14
    assert b64 not in redact(f"a_{b64}_b", [])
    padded = "A" * 40 + "=="
    out = redact(f"b {padded} e", [])
    assert padded not in out and "==" not in out


def test_jev_tokens_strict():
    for usage in (
        {},
        {"input_tokens": 3.7},
        {"input_tokens": 1e400},
        {"input_tokens": -1},
        {"input_tokens": "5"},
        {"input_tokens": True},
        {"input_tokens": None},
    ):

        def post(url, headers, body, timeout, _u=usage):
            return (
                200,
                {
                    "answers": {"s0": {"choice": "major", "confidence": 0.5}},
                    "usage": _u,
                },
            )

        res = jev_mod.request_sev([_finding(0)], api_key="k", post=post)
        assert res.get("error") == "bad payload", usage

    def ok(url, headers, body, timeout):
        return (
            200,
            {
                "answers": {"s0": {"choice": "major", "confidence": 0.5}},
                "usage": {"input_tokens": 7},
            },
        )

    assert jev_mod.request_sev([_finding(0)], api_key="k", post=ok) == {
        "sev": [["major", 0.5]],
        "tok": 7,
    }


def test_moves():
    from src import config as cfg_mod
    from src import config_env
    from src.loop.judge_step import JudgeStep

    assert cfg_mod._ENV_MAP is config_env._ENV_MAP
    assert hasattr(JudgeStep, "_stop_judge_pending")


def test_jev_stream_carries_phase_round(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    calls = []
    ctx, emitted = _ctx()
    res = situation_step.maybe_query_jev(ctx, [_finding(0)], post=_post_factory(calls))
    assert "sev" in res
    assert emitted and emitted[0]["type"] == "jev_sev"
    assert emitted[0].get("phase") == "P1" and emitted[0].get("round") == 3


def test_jev_rounds_spent_like_bench(monkeypatch):
    # бенч v2_draft: rounds_already_spent = round - 1
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    calls = []
    ctx, _emitted = _ctx()
    situation_step.maybe_query_jev(ctx, [_finding(0)], post=_post_factory(calls))
    st = calls[0][2]["state"]
    assert st["round"] == 3 and st["rounds_already_spent"] == 2


def test_redact_nested_pem_and_root_secret_path():
    inner = "-----BEGIN PUBLIC KEY-----\nPUB\n-----END PUBLIC KEY-----"
    text = f"a -----BEGIN PRIVATE KEY-----\nX\n{inner}\nSECRET/AFTER\n-----END PRIVATE KEY----- b"
    assert redact(text, []) == "a [redacted] b"
    assert redact("bad /work/repo/.env here", [".env"]) == "bad .env here"
    assert redact("in /workspace/config/secrets.yaml", ["config/secrets.yaml"]) == "in secrets.yaml"
    assert redact("my.env stays", [".env"]) == "my.env stays"
