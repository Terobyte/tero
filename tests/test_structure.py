"""Structure ceilings: src files and functions stay small (tero plan 06)."""

import ast
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FILE_CEILINGS = {
    "src/loop/phase.py": 2109,
    "src/loop/run.py": 878,
    "src/menu.py": 548,
    "src/providers/codex.py": 540,
    "src/cli_entry.py": 487,
    "src/config.py": 516,
    "src/loop/agents.py": 459,
    "src/providers/subprocess_runner.py": 403,
}

FUNC_CEILINGS = {
    "src/loop/run.py::run": 334,
    "src/loop/run.py::resume": 311,
    "src/providers/subprocess_runner.py::run_subprocess_jsonl": 262,
    "src/loop/phase.py::_judge": 184,
}

DEFAULT_FILE_CEILING = 400
DEFAULT_FUNC_CEILING = 120
TIGHT_SLACK = 20


def _src_files() -> list[Path]:
    return sorted(ROOT.glob("src/**/*.py"))


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _func_len(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    return node.end_lineno - node.lineno + 1


def _funcs_with_nesting(tree: ast.Module) -> list[tuple[str, int, bool]]:
    """(name, length, nested) for every function/method via ast.walk.

    A function is nested when another function's line range contains it;
    class bodies do not nest (methods are top-level and keyed by name).
    """
    funcs = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    out = []
    for node in funcs:
        nested = any(
            other is not node
            and other.lineno <= node.lineno
            and node.end_lineno <= other.end_lineno
            for other in funcs
        )
        out.append((node.name, _func_len(node), nested))
    return out


def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def test_file_sizes_within_ceilings():
    for path in _src_files():
        rel = _rel(path)
        actual = len(path.read_text().splitlines())
        ceiling = FILE_CEILINGS.get(rel, DEFAULT_FILE_CEILING)
        assert actual <= ceiling, f"{rel} is {actual} lines, ceiling {ceiling}"


def test_function_sizes_within_ceilings():
    for path in _src_files():
        rel = _rel(path)
        tree = ast.parse(path.read_text())
        for name, actual, nested in _funcs_with_nesting(tree):
            key = f"{rel}::{name}"
            ceiling = DEFAULT_FUNC_CEILING if nested else FUNC_CEILINGS.get(key, DEFAULT_FUNC_CEILING)
            assert actual <= ceiling, f"{key} is {actual} lines, ceiling {ceiling}"


def test_ceilings_are_tight():
    for rel, ceiling in FILE_CEILINGS.items():
        path = ROOT / rel
        assert path.exists(), f"{rel} in FILE_CEILINGS does not exist"
        actual = len(path.read_text().splitlines())
        assert ceiling <= actual + TIGHT_SLACK, (
            f"{rel} ceiling {ceiling} is more than {actual} + {TIGHT_SLACK}"
        )
    for key, ceiling in FUNC_CEILINGS.items():
        rel, _, name = key.partition("::")
        path = ROOT / rel
        assert path.exists(), f"{key} in FUNC_CEILINGS does not exist"
        tree = ast.parse(path.read_text())
        matches = [
            actual
            for func_name, actual, nested in _funcs_with_nesting(tree)
            if func_name == name and not nested
        ]
        assert matches, f"{key} in FUNC_CEILINGS does not exist"
        actual = max(matches)
        assert ceiling <= actual + TIGHT_SLACK, (
            f"{key} ceiling {ceiling} is more than {actual} + {TIGHT_SLACK}"
        )


def test_every_src_module_imports():
    for path in _src_files():
        importlib.import_module(_module_name(path))
