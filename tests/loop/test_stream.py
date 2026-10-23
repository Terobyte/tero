"""Stream.emit: whole newline-terminated JSON lines, offset reader safe."""

import json
import threading
from pathlib import Path

import pytest

from src.loop.stream import Stream

EVENT_TYPES = {
    "tool",
    "text",
    "gate_start",
    "gate",
    "verdict",
    "tester",
    "ship",
    "stop",
    "start_step",
}


def test_emit_appends_newline_terminated_json_lines(tmp_path: Path):
    path = tmp_path / "run" / "stream.jsonl"
    stream = Stream(path)
    stream.emit("start_step", phase=2, round=1)
    stream.emit("tool", text="Read src/parse.py")
    stream.emit("text", role="player", text="делаю контракт")

    raw = path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    lines = raw.splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["type"] == "start_step"
    assert first["phase"] == 2
    assert "ts" in first
    second = json.loads(lines[1])
    assert second["type"] == "tool"
    third = json.loads(lines[2])
    assert third["type"] == "text"


def test_emit_never_raises(tmp_path: Path, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    stream = Stream(blocker / "stream.jsonl")
    stream.emit("text", text="no crash")
    stream.emit("text", text="still no crash")
    capsys.readouterr()  # warning allowed, exception is not


def read_new(path: Path, offset: int):
    """Offset reader: returns (events, new_offset), defers partial tail."""
    with open(path, "rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    text = chunk.decode("utf-8", errors="replace")
    *complete, tail = text.split("\n")
    if tail != "":
        # last fragment has no trailing \n: defer it
        events = [json.loads(line) for line in complete if line]
        new_offset = offset + len("\n".join(complete).encode("utf-8")) + (1 if complete else 0)
        # recompute: offset + bytes of complete lines including their \n
        consumed = sum(len(line.encode("utf-8")) + 1 for line in complete)
        new_offset = offset + consumed
        return events, new_offset
    events = [json.loads(line) for line in complete if line]
    return events, offset + len(chunk)


def test_offset_reader_defers_partial_line(tmp_path: Path):
    path = tmp_path / "stream.jsonl"
    stream = Stream(path)
    stream.emit("text", text="first")

    events, offset = read_new(path, 0)
    assert [e["text"] for e in events] == ["first"]

    # simulate a torn write: fragment without trailing newline
    with open(path, "ab") as fh:
        fh.write(b'{"type": "text", "text": "par')
    events, offset2 = read_new(path, offset)
    assert events == []
    assert offset2 == offset

    # writer finishes the line
    with open(path, "ab") as fh:
        fh.write(b'tial"}\n')
    events, offset3 = read_new(path, offset)
    assert [e["text"] for e in events] == ["partial"]
    assert offset3 > offset


def test_concurrent_emits_keep_lines_whole(tmp_path: Path):
    path = tmp_path / "stream.jsonl"
    stream = Stream(path)
    n_threads, n_each = 8, 25

    def worker(i: int):
        for j in range(n_each):
            stream.emit("tool", worker=i, seq=j)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == n_threads * n_each
    seen = set()
    for line in lines:
        obj = json.loads(line)  # must never raise on torn line
        assert obj["type"] == "tool"
        seen.add((obj["worker"], obj["seq"]))
    assert len(seen) == n_threads * n_each


def test_emit_accepts_all_contract_types(tmp_path: Path):
    path = tmp_path / "stream.jsonl"
    stream = Stream(path)
    for t in sorted(EVENT_TYPES):
        stream.emit(t, probe=True)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert {json.loads(line)["type"] for line in lines} == EVENT_TYPES


def _strict_loads(line: str):
    def _reject(value: str):
        raise ValueError(f"non-finite constant: {value}")

    return json.loads(line, parse_constant=_reject)


def test_emit_rejects_nonfinite_floats_without_raising(tmp_path: Path):
    path = tmp_path / "stream.jsonl"
    stream = Stream(path)
    stream.emit("text", text="before")
    # must not raise; invalid-JSON payloads are dropped
    stream.emit("tool", value=float("nan"))
    stream.emit("tool", value=float("inf"))
    stream.emit("tool", value=float("-inf"))
    stream.emit("text", text="after")

    raw = path.read_text(encoding="utf-8")
    assert "NaN" not in raw
    assert "Infinity" not in raw
    lines = raw.splitlines()
    assert len(lines) == 2
    for line in lines:
        _strict_loads(line)
    assert [_strict_loads(line)["text"] for line in lines] == ["before", "after"]


def test_offset_reader_polls_while_emitting(tmp_path: Path):
    import time

    path = tmp_path / "stream.jsonl"
    stream = Stream(path)
    n_threads, n_each = 4, 50
    total = n_threads * n_each

    def worker(i: int):
        for j in range(n_each):
            stream.emit("tool", worker=i, seq=j)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()

    offset = 0
    seen: list[dict] = []
    deadline = time.monotonic() + 15
    try:
        while len(seen) < total:
            assert time.monotonic() < deadline, "reader starved by writers"
            try:
                events, new_offset = read_new(path, offset)
            except FileNotFoundError:
                time.sleep(0.001)
                continue
            assert new_offset >= offset
            seen.extend(events)
            offset = new_offset
            if len(seen) < total and not events:
                time.sleep(0.001)
    finally:
        for t in threads:
            t.join()

    events, offset = read_new(path, offset)
    seen.extend(events)
    assert len(seen) == total
    assert {(e["worker"], e["seq"]) for e in seen} == {
        (i, j) for i in range(n_threads) for j in range(n_each)
    }
