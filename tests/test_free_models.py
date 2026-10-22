"""Free opencode models are listed at start, not pinned in the code."""

import sys

import pytest

from src.providers.free_models import is_free, list_models
from src.providers.opencode import OpenCodeConfig, OpenCodeProvider


@pytest.mark.parametrize(
    "model",
    ["opencode/mimo-v2.6-flash-free", "openrouter/moonshotai/kimi-k2:free", "OPENCODE/X-FREE"],
)
def test_is_free_by_suffix(model):
    assert is_free(model)


@pytest.mark.parametrize("model", ["opencode/gpt-5", "free", "", "opencode/freeform-1"])
def test_is_free_rejects_paid_and_keyword(model):
    assert not is_free(model)


def _fake_cli(tmp_path, body: str) -> str:
    script = tmp_path / "fake-opencode"
    script.write_text(f"#!{sys.executable}\nimport sys\n{body}\n")
    script.chmod(0o755)
    return str(script)


def test_list_models_reads_cli_output(tmp_path):
    cli = _fake_cli(
        tmp_path,
        "assert sys.argv[1:] == ['models']\n"
        "print('opencode/a-free')\nprint('')\nprint('openrouter/b:free  extra')\nprint('junk')",
    )
    assert list_models(cli) == ["opencode/a-free", "openrouter/b:free"]


def test_list_models_failed_cli_is_empty(tmp_path):
    assert list_models(_fake_cli(tmp_path, "sys.exit(3)")) == []
    assert list_models(str(tmp_path / "missing")) == []


def test_opencode_default_model_is_any_free():
    assert OpenCodeConfig().default_model == "free"


def test_opencode_free_keyword_resolves_from_cli(tmp_path):
    cli = _fake_cli(tmp_path, "print('opencode/paid')\nprint('opencode/today-free')")
    cmd = OpenCodeProvider(OpenCodeConfig(command=cli))._build_command()
    assert cmd[cmd.index("-m") + 1] == "opencode/today-free"


def test_opencode_free_keyword_without_list_uses_cli_default(tmp_path):
    cli = _fake_cli(tmp_path, "sys.exit(1)")
    cmd = OpenCodeProvider(OpenCodeConfig(command=cli))._build_command()
    assert "-m" not in cmd


def test_fallback_menu_opencode_defaults_to_any_free(monkeypatch):
    from src.config import Config
    from src.menu import _fallback_menu

    answers = iter(["p", "opencode", "", "high", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(answers))
    assert _fallback_menu(Config()).player_model == "free"
