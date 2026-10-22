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


def test_opencode_has_no_pinned_default_model():
    # free names rotate; with no model chosen opencode uses its own default
    assert OpenCodeConfig().default_model == ""
    assert "-m" not in OpenCodeProvider()._build_command()


def _menu_lists(monkeypatch, models):
    from src.providers import free_models

    monkeypatch.setattr(free_models, "list_models", lambda command="opencode": models)


def test_menu_offers_todays_free_models(monkeypatch):
    from src.menu import _model_presets_for_provider

    _menu_lists(monkeypatch, ["opencode/new-free", "opencode/paid", "openrouter/x:free"])
    presets = _model_presets_for_provider("opencode")
    assert [v for v in presets.values() if v != "__custom__"] == ["opencode/new-free", "openrouter/x:free"]
    assert "__custom__" in presets.values()


def test_menu_without_list_offers_manual_id(monkeypatch):
    from src.menu import _model_presets_for_provider

    _menu_lists(monkeypatch, [])
    assert list(_model_presets_for_provider("opencode").values()) == ["__custom__"]


def test_fallback_menu_picks_free_model_by_number(monkeypatch):
    from src.config import Config
    from src.menu import _fallback_menu

    _menu_lists(monkeypatch, ["opencode/a-free", "opencode/b-free"])
    answers = iter(["p", "opencode", "2", "high", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(answers))
    assert _fallback_menu(Config()).player_model == "opencode/b-free"


def test_fallback_menu_takes_typed_model_id(monkeypatch):
    from src.config import Config
    from src.menu import _fallback_menu

    _menu_lists(monkeypatch, [])
    answers = iter(["p", "opencode", "openrouter/moonshotai/kimi-k2:free", "high", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(answers))
    assert _fallback_menu(Config()).player_model == "openrouter/moonshotai/kimi-k2:free"
