"""Role prompts must carry the v4 rules, not player self-score."""

from src.loop import prompts as role_prompts


def test_player_prompt_forbids_commits_and_protected_edits():
    text = role_prompts.player_prompt(
        preamble="common",
        contract="## Phase\n- [ ] parse empty",
        findings=["F1: parse('') raises"],
        suite_delta=["tests/test_old.py"],
        protected=["tests/test_new_red.py"],
        gate_output="FAILED tests/test_new_red.py::test_x",
    )
    assert "не коммит" in text.lower() or "do not commit" in text.lower()
    assert "tests/test_new_red.py" in text
    assert "tests/test_old.py" in text
    assert "parse empty" in text
    assert "PlayerReport" in text
    assert "tests passed" not in text.lower()


def test_judge_prompt_says_map_is_navigation():
    text = role_prompts.judge_prompt(
        preamble="common",
        contract="## Phase\n- [ ] parse empty",
        player_report={"status": "done", "changes": [], "start_here": [], "blocked_reason": ""},
        diff="diff --git a/src.py",
        status_short=" M src.py",
        suite_delta=["tests/test_old.py"],
        test_output="1 passed",
        open_findings=[{"id": "F1", "problem": "raises"}],
        protected_ids=["F1"],
    )
    assert "навигац" in text.lower() or "navigation" in text.lower()
    assert "JudgeVerdict" in text
    assert "discard_test" in text
    assert "F1" in text
    assert "suite_delta" in text or "tests/test_old.py" in text
    assert "git status" in text.lower() or " M src.py" in text


def test_tester_prompt_one_new_file_per_finding():
    text = role_prompts.tester_prompt(
        preamble="common",
        contract="## Phase",
        diff="diff",
        findings=[{"id": "F1", "problem": "raises", "expected": "[]"}],
    )
    assert "один" in text.lower() or "one new" in text.lower()
    assert "TesterReport" in text
    assert "F1" in text
