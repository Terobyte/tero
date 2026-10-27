"""Scoped gate: run only the tests affected by the round's changes.

Rules (contract 2026-10-25-fast-gate):
- changed files = `git diff --name-only <checkpoint>` (worktree vs checkpoint)
  plus untracked files (`git ls-files --others --exclude-standard`);
- a changed test file `tests/**/test_*.py` runs (deleted ones are skipped);
- a changed `src/<...>/<name>.py` maps to every `tests/**/test_*.py` whose
  text mentions the dotted module path (`src.loop.gates`) or a line like
  `from src.loop import ... gates ...`; `__init__.py` maps via the package path;
- `tests/test_structure.py` is always added when it exists;
- anything else (config files, .md/.yaml, src module with no matching tests,
  empty result, git failure) means `None` — run the full suite.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.loop.gates import GateResult, TestCommand, run_all
from src.loop.git import GitError


_FULL_SUITE_FILES = frozenset(
    {"conftest.py", "pyproject.toml", "pytest.ini", "setup.cfg", "tox.cfg", "tox.ini"}
)
_STRUCTURE_TEST = "tests/test_structure.py"
_RELAY_WRAPPER = "tests/relay/test_worker_js.py"


def _relay_wrapper_for(repo: str | Path, changed: set[str]) -> set[str]:
    """relay/** и widget/** отображаются на JS-обёртку (план 08-4).

    F16: каталог relay/test может отсутствовать (удалён целиком) — обёртка
    выбирается по факту изменения relay/** или widget/**; без самой обёртки
    подменяем выбор: вызывающий решает по наличию hits (см. select_tests).
    """
    root = Path(repo)
    # F2: любой файл relay/** или widget/** (не только .js) — иначе
    # правка schema.sql/wrangler.toml/package.json проскакивает мимо обёртки.
    hits = {p for p in changed if p.startswith(("relay/", "widget/"))}
    if not hits:
        return set()
    if (root / _RELAY_WRAPPER).is_file():
        return {_RELAY_WRAPPER}
    # F16: обёртки нет, а relay меняли — помечаем недостачу, чтобы select_tests
    # не вернул частичный список, а ушёл в полный набор.
    return {"<missing-relay-wrapper>"}


ROOT = Path(__file__).resolve().parent.parent.parent


def select_tests(repo: str | Path, checkpoint: str) -> list[str] | None:
    """Test files affected by changes since checkpoint, or None for the full suite."""
    try:
        changed = _git_names(repo, "diff", "--name-only", checkpoint)
        changed |= _git_names(
            repo, "ls-files", "--others", "--exclude-standard"
        )
    except OSError:
        return None
    if _needs_full(changed):
        return None
    tests = _test_files(repo, changed)
    relay = _relay_wrapper_for(repo, changed)
    if "<missing-relay-wrapper>" in relay:
        # F16: relay/widget меняли, а обёртки нет — частичный список
        # уронил бы JS-тесты мимо гейта; уходим в полный набор.
        return None
    checked = _checked_src(changed)
    per_src = _match_tests(repo, checked) if checked else {}
    for src, matched in per_src.items():
        if not matched:
            # Изменённый src-модуль не нашёл ни одного теста сам по себе.
            return None
    found = set().union(*per_src.values()) if per_src else set()
    picked = sorted(tests | found | relay)
    if not picked and (tests or checked):
        # .py меняли, а тестов не нашлось — безопаснее полный набор.
        return None
    out = [p for p in picked if (Path(repo) / p).is_file()]
    if (Path(repo) / _STRUCTURE_TEST).is_file() and _STRUCTURE_TEST not in out:
        out.append(_STRUCTURE_TEST)
    return sorted(out) or None


def _git_names(repo: str | Path, *args: str) -> set[str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise OSError(f"git {' '.join(args)} failed")
    return {line for line in proc.stdout.splitlines() if line.strip()}


def _needs_full(changed: set[str]) -> bool:
    return any(_forces_full(path) for path in changed)


def _forces_full(path: str) -> bool:
    name = Path(path).name
    if name in _FULL_SUITE_FILES:
        return True
    if not path.endswith(".py"):
        return False
    parts = Path(path).parts
    if parts[:1] == ("tests",) or parts[:1] == ("src",):
        return False
    return True


def _test_files(repo: str | Path, changed: set[str]) -> set[str]:
    out = set()
    for path in changed:
        parts = Path(path).parts
        if len(parts) < 2 or parts[0] != "tests":
            continue
        if Path(path).name.startswith("test_") and path.endswith(".py"):
            if (Path(repo) / path).is_file():
                out.add(path)
    return out


def _checked_src(changed: set[str]) -> set[str]:
    return {p for p in changed if p.endswith(".py") and Path(p).parts[:1] == ("src",)}


def _match_tests(repo: str | Path, src_paths: set[str]) -> dict[str, set[str]]:
    bodies = _read_tests(repo)
    per_src: dict[str, set[str]] = {}
    for src in src_paths:
        matched = set()
        for dotted in _dotted_names(src):
            for rel, text in bodies.items():
                if dotted in text or _from_import_line(text, src):
                    matched.add(rel)
        per_src[src] = matched
    return per_src


def _dotted_names(src: str) -> list[str]:
    stem = Path(src).with_suffix("").as_posix().replace("/", ".")
    if stem.endswith(".__init__"):
        pkg = stem[: -len(".__init__")]
        return [pkg]
    return [stem]


def _read_tests(repo: str | Path) -> dict[str, str]:
    bodies = {}
    root = Path(repo)
    for path in sorted(root.glob("tests/**/test_*.py")):
        try:
            bodies[path.relative_to(root).as_posix()] = path.read_text()
        except OSError:
            continue
    return bodies


def _from_import_line(text: str, src: str) -> bool:
    parts = Path(src).with_suffix("").parts
    if len(parts) < 2 or parts[0] != "src":
        return False
    pkg, name = ".".join(parts[:-1]), parts[-1]
    for line in text.splitlines():
        code = line.split("#", 1)[0].strip()
        if not code.startswith(("from ", "import ")):
            continue
        if code.startswith("from ") and name in [
            item.strip().split(" as ")[0].strip()
            for item in code[5:].split(" import ", 1)[-1].split(",")
        ]:
            head, _, tail = code[5:].partition(" import ")
            if head.strip() in (pkg, "src") or tail.strip().startswith(name):
                return True
        elif code.startswith("import ") and f"src.{name}" in code:
            return True
    return False


def format_gate_fact(
    *,
    command: str,
    scope: str,
    files: list[str] | None,
    exit_code: int,
    tail: str,
) -> str:
    """Fact block for the judge prompt: what the loop already ran."""
    if scope == "scoped":
        label = f"scoped ({len(files or [])} файлов: {', '.join(files or [])})"
    else:
        label = "полный"
    lines = [
        "## Тесты цикла",
        f"команда: {command or '(нет)'}",
        f"scope: {label}",
        f"код выхода: {exit_code}",
        "последняя строка итога:",
        (tail or "").strip().splitlines()[-1] if (tail or "").strip() else "(нет)",
        "",
        "Цикл уже прогнал тесты на этом дереве (факт выше). "
        "Полный набор сам не перезапускай. "
        "Точечные тесты под свои гипотезы — можно. "
        "Если scope scoped — полный набор цикл прогонит сам после твоего SHIP.",
    ]
    return "\n".join(lines)


def scoped_files(
    command: TestCommand,
    *,
    repo: str | Path,
    enabled: bool,
    checkpoint: str,
) -> list[str] | None:
    """Scoped-список тест-файлов или None (полный прогон, как раньше)."""
    if not enabled or not command.run_files:
        return None
    try:
        return select_tests(repo, checkpoint)
    except OSError:
        return None


def describe(files: list[str] | None) -> tuple[str, list[str] | None]:
    """Пометка scope и список для прогона (None — полный, как раньше)."""
    if files is None:
        return "полный", None
    return f"scoped {len(files)} файлов", files


def emit_fields(result: GateResult, scope: str, tail: str, marked: bool) -> dict:
    """Поля события gate: при marked — пометка scope в тексте и событии."""
    fields: dict = {
        "ok": result.ok,
        "exit_code": result.exit_code,
        "role": "gate",
        "tail": tail,
    }
    if marked:
        fields["scope"] = scope
        verdict = "зелёные" if result.ok else "красные"
        fields["text"] = f"тесты: {verdict} (код {result.exit_code}), scope: {scope}\n{tail}"
    return fields


def finish_gate(
    ctx, ledger, st, *, command, result, run, scope, repo, marked, timeout_s, duration,
) -> None:
    """Отчёт о гейте: факт в состояние, события, артефакт, консоль."""
    from src.loop.gates import print_red_tail, tail_lines

    tail = tail_lines(result.output)
    remember_gate(st, command, result, run, repo, marked=marked)
    ctx.emit("gate", **emit_fields(result, scope, tail, marked))
    gate_state: dict = {"ok": result.ok, "exit_code": result.exit_code, "tail": tail}
    gate_kwargs: dict = {"ok": result.ok, "exit_code": result.exit_code, "round": st.round}
    if marked:
        gate_state["scope"] = scope
        gate_kwargs["scope"] = scope
    ctx.update_state(gate=gate_state)
    ledger.event("gate", **gate_kwargs)
    ledger.artifact(ctx.round_file("tests.log"), result.output)
    if result.ok:
        print(
            f"  тесты: зелёные (код {result.exit_code}, {duration:.1f} с)"
            + (f", scope: {scope}" if marked else ""),
            flush=True,
        )
    elif result.timed_out:
        print(f"  тесты: таймаут ({timeout_s} с)", flush=True)
        print_red_tail(result.output)
    else:
        print(f"  тесты: КРАСНЫЕ (код {result.exit_code})", flush=True)
        print_red_tail(result.output)


def report_skipped(ctx, ledger) -> None:
    """Гейт пропущен (нет команды): те же событие/состояние/консоль, что раньше."""
    from datetime import datetime, timezone

    print("  тесты пропущены (нет команды)", flush=True)
    ctx.emit("gate", result="skipped", role="gate")
    ctx.update_state(
        role="gate",
        provider="",
        model="",
        step_started=datetime.now(timezone.utc).isoformat(),
        gate={"ok": True, "result": "skipped"},
    )
    ledger.event("gate", result="skipped")


def remember_gate(st, command: TestCommand, result: GateResult, files, repo, *, marked: bool) -> None:
    """Факт последнего гейта для промпта судьи и зелёного полного дерева."""
    from src.loop.git import write_tree

    st.gate_scope = "scoped" if files is not None else "full"
    st.gate_files = list(files) if files is not None else []
    st.gate_command = " ".join(command.run_all)
    st.gate_exit_code = result.exit_code
    if marked and result.ok and files is None:
        try:
            st.last_green_full_tree = write_tree(repo)
        except (GitError, OSError):
            pass


def full_gate(
    command: TestCommand,
    repo: str | Path,
    ctx,
    ledger,
    st,
    *,
    timeout_s: int,
    enabled: bool,
) -> str | None:
    """Полный прогон перед SHIP. None — не нужен, иначе green/red."""
    from src.loop.gates import print_red_tail
    from src.loop.git import write_tree

    if not enabled:
        return None
    try:
        tree_now: str | None = write_tree(repo)
    except (GitError, OSError):
        # Дерево сравнить не с чем — прогоняем полный набор без записи,
        # вместо молчаливого пропуска.
        tree_now = None
    if tree_now is not None and st.last_green_full_tree == tree_now:
        return None
    print("  полный гейт перед ship …", flush=True)
    result = run_all(command, repo=repo, timeout_s=timeout_s)
    ledger.event(
        "gate",
        ok=result.ok,
        exit_code=result.exit_code,
        round=st.round,
        scope="полный перед ship",
    )
    ledger.artifact(ctx.round_file("tests-full.log"), result.output)
    remember_full_gate(st, command, result)
    if result.ok:
        print(f"  полный гейт: зелёный (код {result.exit_code})", flush=True)
        if tree_now is not None:
            st.last_green_full_tree = tree_now
        return "green"
    print(f"  полный гейт: КРАСНЫЙ (код {result.exit_code})", flush=True)
    print_red_tail(result.output)
    ledger.event("full_gate_red", round=st.round)
    ctx.emit(
        "full_gate_red",
        round=st.round,
        role="gate",
        text=f"полный гейт красный (код {result.exit_code}) — шипать нельзя",
    )
    return "red"


def remember_full_gate(st, command: TestCommand, result: GateResult) -> None:
    st.gate_output = result.output
    st.gate_scope = "full"
    st.gate_files = []
    st.gate_command = " ".join(command.run_all)
    st.gate_exit_code = result.exit_code
