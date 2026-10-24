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
    "src/cli_entry.py": 454,
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


def test_gemini_module_gone():
    import importlib.util
    import warnings

    from src.providers import create_provider
    from src.providers.agy import AgyProvider

    assert importlib.util.find_spec("src.providers.gemini") is None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert isinstance(create_provider("gemini"), AgyProvider)


def _top_level_defs(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    nodes = list(tree.body)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            nodes.extend(node.body)
    return [n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _mentions(tree: ast.Module) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.append(node.value)
    return names


def test_no_unreferenced_private_functions():
    exceptions: set[str] = set()
    trees = {_rel(p): ast.parse(p.read_text()) for p in _src_files()}
    mentioned: dict[str, int] = {}
    for tree in trees.values():
        for name in _mentions(tree):
            mentioned[name] = mentioned.get(name, 0) + 1
    unreferenced = []
    for rel, tree in trees.items():
        for node in _top_level_defs(tree):
            name = node.name
            if not name.startswith("_") or (name.startswith("__") and name.endswith("__")):
                continue
            if name in exceptions:
                continue
            if mentioned.get(name, 0) == 0:
                unreferenced.append(f"{rel}::{name}")
    assert unreferenced == []


def _type_checking_nodes(tree: ast.Module) -> set[int]:
    inside = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            inside.update(id(child) for child in ast.walk(node))
    return inside


def _dunder_all(tree: ast.Module) -> set[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            return {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
    return set()


def test_no_unused_imports():
    unused = []
    for path in _src_files():
        text = path.read_text()
        lines = text.splitlines()
        tree = ast.parse(text)
        skip = _type_checking_nodes(tree)
        exported = _dunder_all(tree)
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)) or id(node) in skip:
                continue
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                continue
            if any("# noqa: F401" in lines[i - 1] for i in range(node.lineno, node.end_lineno + 1)):
                continue
            for alias in node.names:
                name = (alias.asname or alias.name).split(".")[0]
                if name not in used and name not in exported:
                    unused.append(f"{_rel(path)}:{node.lineno} {name}")
    assert unused == []
