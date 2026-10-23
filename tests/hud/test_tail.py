"""Tail.read_new: offset reads, defers partial line, skips broken, restarts on truncate."""

import os

from src.hud.tail import Tail


def test_tail_defers_partial_line(tmp_path):
    path = tmp_path / "stream.jsonl"
    path.write_text(
        '{"type": "text", "text": "first"}\n{"type": "text", "text": "par',
        encoding="utf-8",
    )
    tail = Tail(path)
    events = tail.read_new()
    assert [e["text"] for e in events] == ["first"]
    with open(path, "ab") as fh:
        fh.write('tial"}\n'.encode("utf-8"))
    events = tail.read_new()
    assert [e["text"] for e in events] == ["partial"]


def test_tail_skips_broken_line(tmp_path):
    path = tmp_path / "stream.jsonl"
    path.write_text(
        '{"type": "text", "text": "ok"}\nnot json at all\n'
        '{"type": "text", "text": "after"}\n',
        encoding="utf-8",
    )
    tail = Tail(path)
    events = tail.read_new()
    assert [e["text"] for e in events] == ["ok", "after"]


def test_tail_restarts_from_zero_on_truncate(tmp_path):
    path = tmp_path / "stream.jsonl"
    path.write_text(
        '{"type": "text", "text": "one"}\n{"type": "text", "text": "two"}\n',
        encoding="utf-8",
    )
    tail = Tail(path)
    assert [e["text"] for e in tail.read_new()] == ["one", "two"]
    old_offset = tail.offset
    assert old_offset > 0
    # rotation/truncation: file is shorter than the stored offset
    path.write_text('{"type": "text", "text": "x"}\n', encoding="utf-8")
    assert os.path.getsize(path) < old_offset
    events = tail.read_new()
    assert [e["text"] for e in events] == ["x"]
