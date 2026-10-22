"""Red proof tests for phase guard bugs B41,B42,B43,B53,B54.

B54: a user's pre-existing `.claude` symlink is unlinked on round 1 (the
snapshot keeps only plain files, so the symlink looks new).

B42 note: top-level `.claude` as a symlink to an outside dir is already
caught by the current code (ls-files lists the symlink itself, guard
unlinks it) — only the nested-repo case is tested here.

B43 omitted: green on current code — the guard iterates
sorted(current | guarded), so the parent symlink `.claude` is unlinked
before `.claude/settings.json` is restored; nothing is written outside.
See report.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import Config
from src.loop.gates import detect
from src.loop.ledger import Ledger, new_run_dir
from src.loop.phase import run_phase
from tests.loop import fakes as scripted

FakeAgents = scripted.FakeAgents
finding = scripted.finding
_git = scripted.git
judge_returns = scripted.judge_returns
no_ship = scripted.no_ship
player_writes = scripted.player_writes
ship = scripted.ship
write_file = scripted.write_file
write_tester = scripted.write_tester_file

PLAN = """# Demo

Preamble.

## Add greet

- [ ] greet() returns hello
"""


def _repo(tmp_path: Path) -> Path:
    return scripted.init_repo(tmp_path, PLAN)


def _cfg(**overrides) -> Config:
    data = {"plan_file": "plan.md", "max_rounds": 8, "test_timeout_s": 30}
    data.update(overrides)
    return Config(**data)


async def _run(repo: Path, agents: FakeAgents, cfg: Config | None = None):
    from src.loop.plan import load_phases, load_preamble

    cfg = cfg or _cfg()
    ledger = Ledger(new_run_dir(repo, "demo"))
    phases = load_phases(repo / "plan.md")
    outcome = await run_phase(
        phases[0],
        repo=repo,
        plan_path=repo / "plan.md",
        preamble=load_preamble(repo / "plan.md"),
        cfg=cfg,
        agents=agents,
        test_command=detect(repo),
        ledger=ledger,
    )
    return outcome, ledger


def _kinds(ledger: Ledger, kind: str) -> list[dict]:
    return [
        json.loads(line)
        for line in ledger.path.read_text().splitlines()
        if json.loads(line)["kind"] == kind
    ]


B41_NEW = [
    ".mcp.json",
    "CLAUDE.md",
    "CLAUDE.local.md",
    "AGENTS.md",
    "GEMINI.md",
    ".cursorrules",
    "sub/CLAUDE.md",
]


@pytest.mark.asyncio
async def test_b41_new_instruction_files_removed_before_judge(tmp_path: Path):
    repo = _repo(tmp_path)
    seen: dict[str, object] = {}

    def player_plants(role, kwargs):
        assert role == "player"
        write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        for rel in B41_NEW:
            write_file(repo, rel, "evil instructions\n")
        return dict(scripted.PLAYER_DONE)

    def check_judge(role, kwargs):
        assert role == "judge"
        seen["left"] = sorted(rel for rel in B41_NEW if (repo / rel).exists())
        return ship()

    agents = FakeAgents(repo, [player_plants, check_judge])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", f"B41: setup failed, ожидался ship: {outcome!r}"
    assert seen.get("left") == [], (
        f"B41: файлы инструкций дожили до judge: {seen.get('left')}"
    )
    rows = _kinds(ledger, "agent_config_violation")
    paths = [row.get("path") for row in rows]
    for rel in B41_NEW:
        assert rel in paths, (
            f"B41: нет agent_config_violation для {rel}: {paths}"
        )


@pytest.mark.asyncio
async def test_b41_tracked_instruction_file_restored(tmp_path: Path):
    repo = _repo(tmp_path)
    write_file(repo, "CLAUDE.md", "original\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "cfg")

    def player_edits(role, kwargs):
        assert role == "player"
        write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        write_file(repo, "CLAUDE.md", "evil\n")
        return dict(scripted.PLAYER_DONE)

    agents = FakeAgents(repo, [player_edits, judge_returns(ship())])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", f"B41: setup failed, ожидался ship: {outcome!r}"
    assert (repo / "CLAUDE.md").read_text() == "original\n", (
        f"B41: закоммиченный CLAUDE.md не восстановлен: {(repo / 'CLAUDE.md').read_text()!r}"
    )


@pytest.mark.asyncio
async def test_b42_nested_repo_claude_invisible_to_guard(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "pkg").mkdir()
    _git(repo / "pkg", "init")
    _git(repo / "pkg", "config", "user.email", "tero@test")
    _git(repo / "pkg", "config", "user.name", "tero")
    write_file(repo, "pkg/a.txt", "a\n")
    _git(repo / "pkg", "add", "-A")
    _git(repo / "pkg", "commit", "-m", "init")
    seen: dict[str, object] = {}

    def player_plants(role, kwargs):
        assert role == "player"
        write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        write_file(repo, "pkg/.claude/settings.json", '{"evil": 1}')
        return dict(scripted.PLAYER_DONE)

    def check_judge(role, kwargs):
        assert role == "judge"
        seen["exists"] = (repo / "pkg/.claude/settings.json").exists()
        return ship()

    agents = FakeAgents(repo, [player_plants, check_judge])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", f"B42: setup failed, ожидался ship: {outcome!r}"
    assert seen.get("exists") is False, (
        "B42: pkg/.claude/settings.json внутри вложенного git-репо дожил до judge"
    )


@pytest.mark.asyncio
async def test_b53_discard_test_removes_both_tests_with_same_finding_id(tmp_path: Path):
    repo = _repo(tmp_path)
    p1 = "from src.greet import greet\n\ndef test_p1():\n    assert greet() == 'hello'\n"
    p2 = "from src.greet import greet\n\ndef test_p2():\n    assert greet() == 'hello'\n"

    def tester_two(role, kwargs):
        assert role == "tester"
        write_file(repo, "tests/test_p1.py", p1)
        write_file(repo, "tests/test_p2.py", p2)
        return {
            "tests": [
                {"finding_id": "F1", "path": "tests/test_p1.py", "name": "test_p1"},
                {"finding_id": "F1", "path": "tests/test_p2.py", "name": "test_p2"},
            ],
            "skipped": [],
        }

    agents = FakeAgents(
        repo,
        [
            player_writes(repo, "src/greet.py", "def greet():\n    return 'nope'\n"),
            judge_returns(no_ship([finding(route="tester", problem="returns nope")])),
            tester_two,
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(
                no_ship(
                    [finding(route="discard_test", id="F1", problem="wrong test")],
                    previous=[{"id": "F1", "state": "still_open"}],
                )
            ),
            player_writes(repo, "src/greet.py", "def greet():\n    return 'hello'\n"),
            judge_returns(ship(previous=[{"id": "F1", "state": "resolved"}])),
        ],
    )
    outcome, _ = await _run(repo, agents)
    assert outcome.status == "ship", f"B53: setup failed, ожидался ship: {outcome!r}"
    assert not (repo / "tests/test_p1.py").exists(), (
        "B53: первый тест с дублирующимся finding_id не снят (остался на диске)"
    )
    assert not (repo / "tests/test_p2.py").exists(), (
        "B53: второй тест с дублирующимся finding_id не снят (остался на диске)"
    )


def _nested_pkg(repo: Path) -> None:
    (repo / "pkg").mkdir()
    _git(repo / "pkg", "init")
    _git(repo / "pkg", "config", "user.email", "tero@test")
    _git(repo / "pkg", "config", "user.name", "tero")
    write_file(repo, "pkg/a.txt", "a\n")
    _git(repo / "pkg", "add", "-A")
    _git(repo / "pkg", "commit", "-m", "init")


def _player_greets(repo: Path, extra=None):
    def run(role, kwargs):
        assert role == "player"
        write_file(repo, "src/greet.py", "def greet():\n    return 'hello'\n")
        if extra:
            extra()
        return dict(scripted.PLAYER_DONE)

    return run


@pytest.mark.asyncio
async def test_guard_keeps_preexisting_untracked_instruction_file(tmp_path: Path):
    repo = _repo(tmp_path)
    write_file(repo, "docs/AGENTS.md", "user notes\n")
    agents = FakeAgents(repo, [_player_greets(repo), judge_returns(ship())])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", outcome
    assert (repo / "docs/AGENTS.md").read_text() == "user notes\n"
    assert _kinds(ledger, "agent_config_violation") == []


@pytest.mark.asyncio
async def test_guard_keeps_and_restores_nested_repo_config(tmp_path: Path):
    repo = _repo(tmp_path)
    _nested_pkg(repo)
    write_file(repo, "pkg/.claude/settings.json", '{"user": 1}')
    write_file(repo, "pkg/.claude/keep.json", '{"keep": 1}')
    seen: dict[str, object] = {}

    def check_judge(role, kwargs):
        assert role == "judge"
        seen["settings"] = (repo / "pkg/.claude/settings.json").read_text()
        return ship()

    agents = FakeAgents(
        repo,
        [
            _player_greets(repo, lambda: write_file(repo, "pkg/.claude/settings.json", '{"evil": 1}')),
            check_judge,
        ],
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", outcome
    assert seen.get("settings") == '{"user": 1}'
    assert (repo / "pkg/.claude/keep.json").read_text() == '{"keep": 1}'
    assert [row["path"] for row in _kinds(ledger, "agent_config_violation")] == [
        "pkg/.claude/settings.json"
    ]


@pytest.mark.asyncio
async def test_guard_removes_new_symlinked_config_dir(tmp_path: Path):
    repo = _repo(tmp_path)
    _nested_pkg(repo)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "settings.json").write_text('{"evil": 1}')
    seen: dict[str, object] = {}

    def plant():
        (repo / ".claude").symlink_to(outside, target_is_directory=True)
        (repo / "pkg/.gemini").symlink_to(outside, target_is_directory=True)

    def check_judge(role, kwargs):
        assert role == "judge"
        seen["left"] = [
            rel for rel in (".claude", "pkg/.gemini") if (repo / rel).is_symlink()
        ]
        return ship()

    agents = FakeAgents(repo, [_player_greets(repo, plant), check_judge])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", outcome
    assert seen.get("left") == []
    assert (outside / "settings.json").read_text() == '{"evil": 1}', "guard wrote outside the repo"
    paths = [row["path"] for row in _kinds(ledger, "agent_config_violation")]
    assert ".claude" in paths and "pkg/.gemini" in paths, paths


@pytest.mark.asyncio
async def test_guard_keeps_preexisting_symlinked_config_dir(tmp_path: Path):
    repo = _repo(tmp_path)
    shared = tmp_path.parent / f"{tmp_path.name}-shared"
    shared.mkdir()
    (shared / "settings.json").write_text('{"user": 1}')
    (repo / ".claude").symlink_to(shared, target_is_directory=True)
    agents = FakeAgents(repo, [_player_greets(repo), judge_returns(ship())])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", outcome
    assert (repo / ".claude").is_symlink()
    assert (shared / "settings.json").read_text() == '{"user": 1}'
    assert _kinds(ledger, "agent_config_violation") == []


@pytest.mark.asyncio
async def test_guard_keeps_tracked_conftest(tmp_path: Path):
    repo = _repo(tmp_path)
    write_file(repo, "tests/conftest.py", "X = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "conftest")
    agents = FakeAgents(repo, [_player_greets(repo), judge_returns(ship())])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", outcome
    assert (repo / "tests/conftest.py").read_text() == "X = 1\n"
    assert _kinds(ledger, "suite_violation") == []
    assert "tests/conftest.py" in _git(repo, "ls-files")


@pytest.mark.asyncio
async def test_guard_never_writes_through_symlinked_parent(tmp_path: Path):
    import shutil

    repo = _repo(tmp_path)
    write_file(repo, "pkg/.claude/settings.json", '{"user": 1}')
    outside = tmp_path.parent / f"{tmp_path.name}-victim"
    (outside / ".claude").mkdir(parents=True)
    (outside / ".claude/settings.json").write_text("VICTIM\n")
    seen: dict[str, object] = {}

    def swap():
        shutil.rmtree(repo / "pkg")
        (repo / "pkg").symlink_to(outside, target_is_directory=True)

    def check_judge(role, kwargs):
        assert role == "judge"
        seen["pkg_link"] = (repo / "pkg").is_symlink()
        seen["settings"] = (repo / "pkg/.claude/settings.json").read_text()
        return ship()

    agents = FakeAgents(repo, [_player_greets(repo, swap), check_judge])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", outcome
    assert (outside / ".claude/settings.json").read_text() == "VICTIM\n", "guard wrote outside the repo"
    assert seen == {"pkg_link": False, "settings": '{"user": 1}'}


CASE_VARIANTS = ["claude.md", "Agents.MD", ".CLAUDE/settings.json", "pkg/.Gemini/settings.json"]


@pytest.mark.asyncio
async def test_guard_folds_case(tmp_path: Path):
    # macOS and Windows filesystems are case-insensitive: the judge's CLI opening
    # CLAUDE.md reads claude.md, and pytest reads Pytest.ini as pytest.ini.
    repo = _repo(tmp_path)
    seen: dict[str, object] = {}

    def plant():
        for rel in CASE_VARIANTS:
            write_file(repo, rel, "evil instructions\n")
        write_file(repo, "Pytest.ini", "[pytest]\n")

    def check_judge(role, kwargs):
        assert role == "judge"
        seen["left"] = [
            rel for rel in [*CASE_VARIANTS, "Pytest.ini"] if (repo / rel).exists()
        ]
        return ship()

    agents = FakeAgents(repo, [_player_greets(repo, plant), check_judge])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", outcome
    assert seen.get("left") == []
    paths = [row["path"] for row in _kinds(ledger, "agent_config_violation")]
    for rel in CASE_VARIANTS:
        assert rel in paths, (rel, paths)
    assert [row["path"] for row in _kinds(ledger, "suite_violation")] == ["Pytest.ini"]


OPENCODE_NEW = ["opencode.json", "opencode.jsonc", "AGENTS.override.md", "sub/opencode.json"]


@pytest.mark.asyncio
async def test_guard_covers_opencode_and_codex_override(tmp_path: Path):
    repo = _repo(tmp_path)
    seen: dict[str, object] = {}

    def plant():
        for rel in OPENCODE_NEW:
            write_file(repo, rel, '{"evil": 1}\n')

    def check_judge(role, kwargs):
        assert role == "judge"
        seen["left"] = [rel for rel in OPENCODE_NEW if (repo / rel).exists()]
        return ship()

    agents = FakeAgents(repo, [_player_greets(repo, plant), check_judge])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", outcome
    assert seen.get("left") == []
    paths = [row["path"] for row in _kinds(ledger, "agent_config_violation")]
    assert sorted(paths) == sorted(OPENCODE_NEW), paths


@pytest.mark.asyncio
async def test_guard_covers_in_repo_target_of_config_symlink(tmp_path: Path):
    # The user's `.claude` links to a plain dir inside the repo; the judge reads
    # cfg/ through the link, so cfg/ is agent config too.
    repo = _repo(tmp_path)
    write_file(repo, "cfg/settings.json", '{"user": 1}')
    (repo / ".claude").symlink_to("cfg", target_is_directory=True)
    write_file(repo, "notes/real.md", "user\n")
    (repo / "CLAUDE.md").symlink_to("notes/real.md")
    seen: dict[str, object] = {}

    def tamper():
        write_file(repo, "cfg/settings.json", '{"evil": 1}')
        write_file(repo, "cfg/hooks.json", '{"evil": 1}')
        write_file(repo, "notes/real.md", "evil\n")

    def check_judge(role, kwargs):
        assert role == "judge"
        seen["settings"] = (repo / ".claude/settings.json").read_text()
        seen["hooks"] = (repo / ".claude/hooks.json").exists()
        seen["claude_md"] = (repo / "CLAUDE.md").read_text()
        return ship()

    agents = FakeAgents(repo, [_player_greets(repo, tamper), check_judge])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", outcome
    assert seen == {"settings": '{"user": 1}', "hooks": False, "claude_md": "user\n"}
    assert (repo / ".claude").is_symlink() and (repo / "CLAUDE.md").is_symlink()
    paths = sorted(row["path"] for row in _kinds(ledger, "agent_config_violation"))
    assert paths == ["cfg/hooks.json", "cfg/settings.json", "notes/real.md"], paths


@pytest.mark.asyncio
async def test_guard_new_config_link_into_repo_keeps_target_files(tmp_path: Path):
    # Only links present at phase start are expanded; a new `.claude -> src` is
    # just removed, the files it points at stay.
    repo = _repo(tmp_path)
    write_file(repo, "src/keep.py", "K = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "keep")

    def plant():
        (repo / ".claude").symlink_to("src", target_is_directory=True)

    agents = FakeAgents(repo, [_player_greets(repo, plant), judge_returns(ship())])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", outcome
    assert not (repo / ".claude").is_symlink()
    for rel in ("src/keep.py", "src/__init__.py", "src/greet.py"):
        assert (repo / rel).exists(), rel
    assert [row["path"] for row in _kinds(ledger, "agent_config_violation")] == [".claude"]


@pytest.mark.asyncio
async def test_guard_follows_link_inside_expanded_target(tmp_path: Path):
    repo = _repo(tmp_path)
    write_file(repo, "cfg/settings.json", '{"user": 1}')
    write_file(repo, "data/note.md", "user\n")
    (repo / "cfg/inner").symlink_to("../data/note.md")
    (repo / ".claude").symlink_to("cfg", target_is_directory=True)
    seen: dict[str, object] = {}

    def check_judge(role, kwargs):
        assert role == "judge"
        seen["inner"] = (repo / ".claude/inner").read_text()
        return ship()

    agents = FakeAgents(
        repo, [_player_greets(repo, lambda: write_file(repo, "data/note.md", "evil\n")), check_judge]
    )
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", outcome
    assert seen == {"inner": "user\n"}
    assert [row["path"] for row in _kinds(ledger, "agent_config_violation")] == ["data/note.md"]


@pytest.mark.asyncio
async def test_guard_restores_redirected_middle_link(tmp_path: Path):
    repo = _repo(tmp_path)
    write_file(repo, "cfg/settings.json", '{"user": 1}')
    write_file(repo, "notes/real.md", "user notes\n")
    (repo / "mid").symlink_to("cfg", target_is_directory=True)
    (repo / ".claude").symlink_to("mid", target_is_directory=True)
    seen: dict[str, object] = {}

    def redirect():
        (repo / "mid").unlink()
        (repo / "mid").symlink_to("notes", target_is_directory=True)

    def check_judge(role, kwargs):
        assert role == "judge"
        seen["settings"] = (repo / ".claude/settings.json").read_text()
        return ship()

    agents = FakeAgents(repo, [_player_greets(repo, redirect), check_judge])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", outcome
    assert seen == {"settings": '{"user": 1}'}
    assert (repo / "notes/real.md").read_text() == "user notes\n"
    assert [row["path"] for row in _kinds(ledger, "agent_config_violation")] == ["mid"]


@pytest.mark.asyncio
async def test_guard_new_link_through_user_link_keeps_user_link(tmp_path: Path):
    # Guarded paths are fixed at phase start: a new `.claude -> shortcut` must not
    # pull the user's own `shortcut -> notes` link into the guard and delete it.
    repo = _repo(tmp_path)
    write_file(repo, "notes/real.md", "user notes\n")
    (repo / "shortcut").symlink_to("notes", target_is_directory=True)

    def plant():
        (repo / ".claude").symlink_to("shortcut", target_is_directory=True)

    agents = FakeAgents(repo, [_player_greets(repo, plant), judge_returns(ship())])
    outcome, ledger = await _run(repo, agents)
    assert outcome.status == "ship", outcome
    assert not (repo / ".claude").is_symlink()
    assert (repo / "shortcut").is_symlink()
    assert (repo / "notes/real.md").read_text() == "user notes\n"
    assert [row["path"] for row in _kinds(ledger, "agent_config_violation")] == [".claude"]
