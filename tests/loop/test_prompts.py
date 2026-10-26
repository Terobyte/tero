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


def test_judge_prompt_is_g3_coach_and_blind():
    text = role_prompts.judge_prompt(
        preamble="common",
        contract="## Phase\n- [ ] parse empty",
        open_findings=[{"id": "F1", "problem": "raises"}],
        protected_ids=["F1"],
    )
    assert "Your ONLY job is to FIND PROBLEMS" in text
    assert "RUN the code" in text
    assert "NO_SHIP" in text
    assert "level" in text
    assert "parse empty" in text
    assert "JudgeVerdict" in text
    assert "discard_test" in text
    assert "F1" in text
    assert "Карта player" not in text
    assert "Вывод тестов" not in text
    assert "git diff" not in text


def test_judge_prompt_lists_touched_existing_tests():
    text = role_prompts.judge_prompt(
        preamble="common",
        contract="## Phase\n- [ ] parse empty",
        suite_delta=["tests/test_old.py"],
    )
    assert "tests/test_old.py" in text
    assert "skip" in text and "xfail" in text
    assert "blocking" in text


def test_judge_prompt_without_suite_delta_says_none():
    text = role_prompts.judge_prompt(preamble="", contract="c")
    assert "## suite_delta" in text
    assert "(нет)" in text


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


def test_coach_prompt_asks_level():
    text = role_prompts.judge_prompt(preamble="common", contract="## Phase\n- [ ] x")
    assert "even cosmetic" not in text
    for level in ("blocker", "major", "minor", "nit"):
        assert level in text
