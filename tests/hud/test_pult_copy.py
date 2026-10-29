"""Спек пульта 2, п. 6: страница пульта = view.html плюс фиксированный блок в <head>."""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.hud.pult_copy import CHARSET, HEAD_BLOCK, make_copy

ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE = ROOT / "src" / "hud" / "view.html"
COPY = ROOT / "relay" / "public" / "index.html"


def test_committed_copy_equals_source_plus_head_block():
    source = SOURCE.read_text(encoding="utf-8")
    assert COPY.read_text(encoding="utf-8") == make_copy(source), (
        "relay/public/index.html отстал от src/hud/view.html: запусти relay/sync-hud.sh")


def test_head_block_is_exactly_what_the_phone_needs():
    assert 'name="viewport"' in HEAD_BLOCK
    assert "<title>" in HEAD_BLOCK
    assert 'rel="manifest"' in HEAD_BLOCK
    assert '<script src="source.js"></script>' in HEAD_BLOCK
    source = SOURCE.read_text(encoding="utf-8")
    copy = make_copy(source)
    head = copy.split("</head>", 1)[0]
    assert head.count("<script") == 1, "в <head> копии ровно один внешний скрипт"
    assert copy.index(CHARSET) < copy.index(HEAD_BLOCK)
    assert "src=" not in source and "href=" not in source, "источник остаётся самодостаточным"


def test_source_checks_tero_source_before_pywebview():
    source = SOURCE.read_text(encoding="utf-8")
    assert source.index("if (window.teroSource)") < source.index('addEventListener("pywebviewready"')
    assert "/*render:start*/" in source and "/*render:end*/" in source
    assert 'html[data-host="phone"]' in source, "режим телефона в CSS источника"


def test_sync_script_rewrites_the_copy():
    proc = subprocess.run(["sh", str(ROOT / "relay" / "sync-hud.sh")], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert COPY.read_text(encoding="utf-8") == make_copy(SOURCE.read_text(encoding="utf-8"))
