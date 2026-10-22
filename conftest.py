"""Minimal asyncio support for the local test suite.

This keeps async tests runnable without requiring an external pytest plugin.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

import src

_ROOT = Path(__file__).resolve().parent


def pytest_configure(config) -> None:
    # an installed tero from another checkout would make red→green fake
    if not Path(src.__file__).resolve().is_relative_to(_ROOT):
        raise pytest.UsageError(f"src imported from {src.__file__}, not from {_ROOT}")
    config.addinivalue_line("markers", "asyncio: run async test in a local event loop")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    """Keep every test away from the real ~/.tero (the menu saves there on start)."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    return home


def pytest_pyfunc_call(pyfuncitem):
    if "asyncio" not in pyfuncitem.keywords:
        return None

    test_func = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_func):
        return None

    kwargs = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
    }

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(test_func(**kwargs))
    finally:
        asyncio.set_event_loop(None)
        loop.close()
    return True
