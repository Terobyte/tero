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
) -> str:
    findings = findings or []
    suite_delta = suite_delta or []
    protected = protected or []
    discarded = discarded or []
    return "\n".join(
        [
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
    )


def judge_prompt(
    *,
    preamble: str,
    contract: str,
    player_report: dict[str, Any],
    diff: str,
    status_short: str,
    suite_delta: list[str] | None = None,
    test_output: str = "",
    open_findings: list[dict[str, Any]] | None = None,
    protected_ids: list[str] | None = None,
) -> str:
    suite_delta = suite_delta or []
    open_findings = open_findings or []
    protected_ids = protected_ids or []
    return "\n".join(
        [
            "Ты judge. Файлы не менять.",
            "Карта player'а — навигация, не доказательство. "
            "Её утверждения проверь по diff и коду.",
            "Проверь контракт, регрессии, scope, suite_delta и git status.",
            "Ослабление / skip / xfail существующих тестов без пункта контракта — blocking.",
            "blocking — нарушение контракта, баг или регрессия. "
            "Стиль и «можно лучше» — advisory.",
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
            "## Карта player (навигация)",
            json.dumps(player_report, ensure_ascii=False, indent=2),
            "",
            "## git diff --cached -W",
            diff or "(пусто)",
            "",
            "## git status --short",
            status_short or "(чисто)",
            "",
            "## suite_delta",
            "\n".join(suite_delta) or "(нет)",
            "",
            "## Вывод тестов",
            test_output or "(нет)",
            "",
            "## open_findings",
            json.dumps(open_findings, ensure_ascii=False, indent=2),
            "",
            "## protected_ids (можно discard_test)",
            ", ".join(protected_ids) or "(нет)",
        ]
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
