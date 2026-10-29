"""Белые списки ретранслятора на Python и в JS совпадают (спек пульта 2, п. 6).

Python: src/relay/client.py (плоские ключи) и src/hud/model_view.py (вложенные);
JS: relay/src/filter.js. Сверка через `node -e`; без node — skip.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.hud.model import LOG_TEXT_LIMIT, VIEW_KEYS
from src.hud.model_view import (AGENT_FIELDS, LOG_FIELDS, RELAY_LOG_LIMIT, RESUME_FIELDS, SECTOR_FIELDS,
                                VERDICT_FIELDS, VIEW_FIELDS)
from src.relay.client import STATE_FIELDS

ROOT = Path(__file__).resolve().parent.parent.parent
_NAMES = ["STATE_FIELDS", "VIEW_FIELDS", "AGENT_FIELDS", "LOG_FIELDS", "SECTOR_FIELDS", "VERDICT_FIELDS",
          "RESUME_FIELDS"]
_SCRIPT = """
import('./relay/src/filter.js').then((m) => {
  const out = {};
  for (const name of %s) out[name] = [...m[name]];
  out.LOG_TEXT_LIMIT = m.LOG_TEXT_LIMIT;
  out.LOG_LIMIT = m.LOG_LIMIT;
  console.log(JSON.stringify(out));
});
""" % json.dumps(_NAMES)


@pytest.fixture(scope="module")
def js() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node не найден в PATH")
    proc = subprocess.run([node, "-e", _SCRIPT], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_flat_whitelist_matches_js(js):
    # the worker accepts state_at from any client; this client sets it itself, not by name
    assert set(js["STATE_FIELDS"]) - {"state_at"} == set(STATE_FIELDS)


def test_nested_whitelists_match_js(js):
    assert set(js["VIEW_FIELDS"]) == set(VIEW_FIELDS) == set(VIEW_KEYS)
    assert set(js["AGENT_FIELDS"]) == set(AGENT_FIELDS)
    assert set(js["LOG_FIELDS"]) == set(LOG_FIELDS)
    assert set(js["SECTOR_FIELDS"]) == set(SECTOR_FIELDS)
    assert set(js["VERDICT_FIELDS"]) == set(VERDICT_FIELDS)
    assert set(js["RESUME_FIELDS"]) == set(RESUME_FIELDS)


def test_limits_match_js(js):
    assert js["LOG_TEXT_LIMIT"] == LOG_TEXT_LIMIT == 300
    assert js["LOG_LIMIT"] == RELAY_LOG_LIMIT == 40


def test_pid_never_in_any_nested_whitelist():
    for fields in (VIEW_FIELDS, AGENT_FIELDS, LOG_FIELDS, SECTOR_FIELDS, VERDICT_FIELDS, RESUME_FIELDS):
        assert "pid" not in fields and "pid_started" not in fields
