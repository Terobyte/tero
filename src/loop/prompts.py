"""System/user prompts for the three roles."""

from __future__ import annotations

import json
from typing import Any


def player_prompt(
    *,
    preamble: str,
    contract: str,
    findings: list[str] | None = None,
    suite_delta: list[str] | None = None,
    protected: list[str] | None = None,
    gate_output: str = "",
    discarded: list[str] | None = None,
    continuation: bool = False,
    continuation_diff: str = "",
) -> str:
    findings = findings or []
    suite_delta = suite_delta or []
    protected = protected or []
    discarded = discarded or []
    sections = [
        "Ты player. Сделай контракт фазы минимальным изменением.",
        "Можно запускать тесты для самопроверки.",
        "Не коммитить.",
        "Не менять файлы из protected и не удалять тестовые файлы с checkpoint.",
        "Если контракт неясен — не выдумывай: верни status blocked.",
        "Финальное сообщение — только JSON PlayerReport "
        '(status, changes, start_here, blocked_reason). Поля «тесты прошли» нет.',
        "",
        "## Преамбула",
        preamble or "(нет)",
        "",
        "## Контракт фазы",
        contract,
        "",
    ]
    if continuation:
        sections += [
            "## Продолжение после падения",
            "Часть работы уже сделана предыдущим исполнителем, вот `git diff base..ref`. "
            "Продолжай, не переписывай готовое.",
            "",
            continuation_diff or "(дифф пуст)",
            "",
        ]
    sections += [
        "## Блокирующие находки",
        "\n".join(findings) or "(нет)",
        "",
        "## suite_delta (существующие тесты изменены)",
        "\n".join(suite_delta) or "(нет)",
        "",
        "## protected (файлы tester'а, не трогать)",
        "\n".join(protected) or "(нет)",
        "",
        "## Снятые тесты (discard_test): чини код, не тест",
        "\n".join(discarded) or "(нет)",
        "",
        "## Вывод красного гейта",
        gate_output or "(нет)",
    ]
    return "\n".join(sections)


# g3 coach, verbatim (main:src/prompts.py COACH_STRICT_SYSTEM_PROMPT); only the
# verdict format is mapped onto JudgeVerdict JSON so the v4 loop can parse it.
G3_COACH = """You are a STRICT code reviewer (Coach). Your ONLY job is to FIND PROBLEMS.

MINDSET: You are a nitpicky senior engineer who catches every flaw. You are NOT here to be encouraging.

MANDATORY REVIEW PROCESS:
1. READ the relevant source files
2. RUN the code — actually execute it to verify it works
3. Check the EXACT requirement — every detail must match (exact hex colors, font names, behavior)
4. Look for any deviation from the spec, missing features, edge cases

PROCESS CLEANUP (MANDATORY):
- If you launch any GUI app or long-running process to test, you MUST kill it before finishing
- After verifying the app works, immediately kill the process: use `kill <PID>` or `pkill -f <script_name>`
- NEVER leave background processes running
- Before launching, check for already-running instances: `pgrep -f <script_name>` and kill them first

WHAT TO CHECK:
- Does the code run without errors?
- Does it EXACTLY match the requirement? (e.g., if color is #1A1A2E, verify that exact value in code)
- Is the feature visually correct when the program runs?
- Are there missing error cases or partially-implemented parts?
- Is anything hardcoded wrong, off-by-one, or only roughly correct?

DECISION:
- Found ANY issue (even minor, even cosmetic)? → verdict NO_SHIP, list ALL problems as findings with severity blocking. Be specific: what is wrong and what it should be.
- 100% satisfied with every detail? → verdict SHIP, findings empty.
- There is no advisory. Every finding is blocking. No compromises.

"Close enough" is NOT approved. EXACT match required."""


def needs_user_reask_block(user_question: str) -> str:
    return (
        f"Ты спросил: `{user_question}`. Человека нет. "
        "Реши сам: SHIP или NO_SHIP с замечаниями. "
        "NEEDS_USER снова не принимается."
    )


def judge_prompt(
    *,
    preamble: str,
    contract: str,
    open_findings: list[dict[str, Any]] | None = None,
    protected_ids: list[str] | None = None,
    suite_delta: list[str] | None = None,
    needs_user_question: str = "",
) -> str:
    # Blind like the g3 coach: no player report, no diff, no gate output.
    # suite_delta is a fact from the loop, not the player's story: it names
    # existing tests the player rewrote, so the judge can't miss a weakened one.
    open_findings = open_findings or []
    suite_delta = suite_delta or []
    protected_ids = protected_ids or []
    return "\n".join(
        [
            G3_COACH,
            "",
            "Do not modify files. Already-implemented code is valid: if the contract is already "
            "satisfied, verify it and approve.",
            "route: tester — если поведение ловится автотестом.",
            "route: discard_test — если protected-тест не про ту находку "
            "(только id из open_findings с protected-файлом).",
            "Поле id у находки: null для новой находки; id из open_findings — "
            "только для route: discard_test.",
            "Для каждой находки из open_findings укажи resolved или still_open.",
            "Финальное сообщение — только JSON JudgeVerdict.",
            "",
            "## Преамбула",
            preamble or "(нет)",
            "",
            "## Контракт фазы",
            contract,
            "",
            "Review ONLY this phase. Is it implemented EXACTLY as specified?",
            "",
            "## suite_delta",
            "Existing tests the player modified. READ each one. Any weakening (skip, xfail, "
            "removed or loosened assert, narrowed input) the contract does not explicitly "
            "ask for is a blocking finding.",
            "\n".join(suite_delta) or "(нет)",
            "",
        "## open_findings",
        json.dumps(open_findings, ensure_ascii=False, indent=2),
        "",
        "## protected_ids (можно discard_test)",
        ", ".join(protected_ids) or "(нет)",
    ]
    + (
        [
            "",
            "## Переспрос без человека",
            needs_user_reask_block(needs_user_question),
            "",
        ]
        if needs_user_question
        else []
    )
    )


def tester_prompt(
    *,
    preamble: str,
    contract: str,
    diff: str,
    findings: list[dict[str, Any]],
) -> str:
    return "\n".join(
        [
            "Ты tester. На каждую находку напиши один новый тестовый файл, "
            "который падает на текущем коде именно по описанной причине.",
            "Не трогать нетестовые файлы.",
            "То, что автотестом не воспроизводится, — в skipped.",
            "Финальное сообщение — только JSON TesterReport.",
            "",
            "## Преамбула",
            preamble or "(нет)",
            "",
            "## Контракт фазы",
            contract,
            "",
            "## diff",
            diff or "(пусто)",
            "",
            "## Находки",
            json.dumps(findings, ensure_ascii=False, indent=2),
        ]
    )
