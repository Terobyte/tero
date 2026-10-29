"""limits фазы 9: неизвестное по умолчанию, известное — как есть."""

from src.providers import limits as _limits


def test_limits_unknown_by_default():
    """Провайдер без данных → limits[p] == "неизвестно"."""
    assert _limits.normalize_limits(None)["codex"] == _limits.UNKNOWN
    assert _limits.normalize_limits({})["codex"] == _limits.UNKNOWN
    assert _limits.describe("codex", object()) == _limits.UNKNOWN


def test_limits_keep_reported_tokens():
    class _Provider:
        last_context_tokens = 85000

    assert _limits.describe("codex", _Provider()) == "токены 85000"
    assert _limits.normalize_limits({"codex": "токены 85000"})["codex"] == "токены 85000"


def test_limits_quota_reported():
    assert _limits.describe("codex", None, "quota") == "квота"


def test_limits_saved_after_role_call(tmp_path):
    """LiveAgents после вызова пишет limits из уже сообщённых токенов."""
    import asyncio

    from src.loop.agents import LiveAgents
    from src.loop.state import RunState

    run_dir = tmp_path / "run"
    RunState(run_dir / "state.json").update(status="running")

    class _Provider:
        streams = False
        last_context_tokens = 85000

        async def run(self, **kwargs):
            yield '{"ok": true}'

    agents = LiveAgents(str(tmp_path))
    agents.run_dir = str(run_dir)
    agents.factory = lambda _name: _Provider()
    asyncio.run(agents.run_role(
        "player", provider="codex", model="m", prompt="P", timeout=5,
        ctx=__import__("src.loop.schemas", fromlist=["ValidateCtx"]).ValidateCtx(),
    ))
    saved = RunState.read(run_dir / "state.json") or {}
    assert saved["limits"]["codex"] == "токены 85000"


def test_limits_in_status_and_page(tmp_path):
    """status_payload отдаёт limits; renderPage показывает их и «неизвестно»."""
    import json
    import subprocess
    import sys

    from src.loop.state import RunState
    from src.loop.wait import status_payload

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "plan.md").write_text("# Demo\n\n## One\n\n- [ ] x\n")
    run_dir = tmp_path / "run"
    RunState(run_dir / "state.json").update(
        status="running", plan="plan.md", phase="One", phase_index=0)
    payload = status_payload(run_dir, repo)
    assert payload["limits"] == {"codex": "неизвестно"}

    root = __import__("pathlib").Path(__file__).resolve().parent.parent.parent
    pult_js = (root / "relay" / "public" / "pult.js").as_uri()
    page = tmp_path / "page_check.mjs"
    page.write_text(
        f"import {{ renderBelow }} from '{pult_js}';\n"
        "const withKnown = renderBelow({ limits: { codex: 'квота' } }, { token: 't' });\n"
        "const without = renderBelow({}, { token: 't' });\n"
        "if (!withKnown.includes('квота')) throw new Error('limits не на странице');\n"
        "if (!without.includes('неизвестно')) throw new Error('нет «неизвестно»');\n"
        "console.log('page limits ok');\n"
    )
    proc = subprocess.run(
        ["node", str(page)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert "page limits ok" in proc.stdout
    assert isinstance(json.dumps(payload["limits"]), str)


def test_limits_keep_active_provider(tmp_path):
    """F6: лимит активного провайдера не отбрасывается статусом."""
    from src.loop.state import RunState
    from src.loop.wait import status_payload

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "plan.md").write_text("# Demo\n\n## One\n\n- [ ] x\n")
    run_dir = tmp_path / "run"
    RunState(run_dir / "state.json").update(
        status="running", plan="plan.md", phase="One", phase_index=0,
        provider="claude", limits={"claude": "неизвестно"})
    payload = status_payload(run_dir, repo)
    assert payload["limits"]["claude"] == "неизвестно"
