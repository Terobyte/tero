"""BUG-019: `tero relay setup` writes `relay:` into ~/.tero/config.yaml and the loader warned about it."""

import warnings

from src.config import resolve_config


def _warnings_for(tmp_path, monkeypatch, *, global_yaml: str = "", project_yaml: str = "") -> list[str]:
    home = tmp_path / "home"
    (home / ".tero").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    if global_yaml:
        (home / ".tero" / "config.yaml").write_text(global_yaml)
    repo = tmp_path / "repo"
    (repo / ".tero").mkdir(parents=True)
    if project_yaml:
        (repo / ".tero" / "config.yaml").write_text(project_yaml)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resolve_config({"working_dir": str(repo)})
    return [str(w.message) for w in caught if "вне defaults" in str(w.message)]


def test_global_relay_section_is_known(tmp_path, monkeypatch):
    assert _warnings_for(tmp_path, monkeypatch,
                         global_yaml="relay:\n  url: https://relay.example\n") == []


def test_project_relay_section_still_warns(tmp_path, monkeypatch):
    # the relay client reads only ~/.tero; a repo must not look like it sets the relay
    found = _warnings_for(tmp_path, monkeypatch,
                          project_yaml="relay:\n  url: https://evil.example\n")
    assert len(found) == 1 and "relay" in found[0]
