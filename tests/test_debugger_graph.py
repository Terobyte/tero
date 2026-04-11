"""Tests for src.debugger_graph — parsing, import resolution, SCC detection, graph builder."""

from src.debugger_graph import (
    DependencyGraph,
    ImportEdge,
    build_dependency_graph,
    find_sccs,
    parse_file,
    resolve_import,
)


# ---------------------------------------------------------------------------
# parse_file
# ---------------------------------------------------------------------------


def test_parse_file_functions(tmp_path):
    """Three top-level functions are extracted as FunctionSig objects."""
    src = tmp_path / "mod.py"
    src.write_text(
        "def foo(x, y):\n"
        "    pass\n"
        "\n"
        "async def bar() -> str:\n"
        "    pass\n"
        "\n"
        "def baz(a, *args, **kwargs):\n"
        "    pass\n"
    )
    node = parse_file(str(src), "mod.py", str(tmp_path))
    assert node is not None
    assert len(node.functions) == 3

    f0 = node.functions[0]
    assert f0.name == "foo"
    assert f0.args == ["x", "y"]
    assert f0.returns is None
    assert f0.is_async is False

    f1 = node.functions[1]
    assert f1.name == "bar"
    assert f1.returns == "str"
    assert f1.is_async is True

    f2 = node.functions[2]
    assert f2.name == "baz"
    assert "*args" in f2.args
    assert "**kwargs" in f2.args


def test_parse_file_classes(tmp_path):
    """A class with methods and base classes is extracted as a ClassSig."""
    src = tmp_path / "cls.py"
    src.write_text(
        "class Animal(Base):\n"
        "    def speak(self):\n"
        "        pass\n"
        "\n"
        "    async def move(self, direction):\n"
        "        pass\n"
    )
    node = parse_file(str(src), "cls.py", str(tmp_path))
    assert node is not None
    assert len(node.classes) == 1

    cls = node.classes[0]
    assert cls.name == "Animal"
    assert cls.bases == ["Base"]
    assert len(cls.methods) == 2
    assert cls.methods[0].name == "speak"
    assert cls.methods[0].is_async is False
    assert cls.methods[1].name == "move"
    assert cls.methods[1].is_async is True


def test_parse_file_imports(tmp_path):
    """from/import statements produce ImportEdge objects with resolved_path."""
    (tmp_path / "utils.py").write_text("def helper(): pass\n")

    src = tmp_path / "main.py"
    src.write_text(
        "from utils import helper\n"
        "import os\n"
    )
    node = parse_file(str(src), "main.py", str(tmp_path))
    assert node is not None
    assert len(node.imports) == 2

    # from utils import helper  → resolved to utils.py
    from_import = node.imports[0]
    assert from_import.target_module == "utils"
    assert "helper" in from_import.symbols
    assert from_import.resolved_path == "utils.py"

    # import os  → stdlib, resolved_path is None
    stdlib_import = node.imports[1]
    assert stdlib_import.target_module == "os"
    assert stdlib_import.resolved_path is None


def test_parse_file_external_calls(tmp_path):
    """Y.method() calls where Y is an imported module produce ExternalCall."""
    src = tmp_path / "caller.py"
    src.write_text(
        "import requests\n"
        "\n"
        "def fetch():\n"
        "    requests.get('http://example.com')\n"
    )
    node = parse_file(str(src), "caller.py", str(tmp_path))
    assert node is not None
    assert len(node.external_calls) >= 1

    call = node.external_calls[0]
    assert call.caller_func == "fetch"
    assert call.callee_module == "requests"
    assert call.callee_name == "get"


def test_parse_file_alias_tracking(tmp_path):
    """provider = create_provider(); provider.run() resolves the alias through a call-result assignment."""
    src = tmp_path / "alias.py"
    src.write_text(
        "from src.providers import create_provider\n"
        "\n"
        "provider = create_provider()\n"
        "\n"
        "def run():\n"
        "    provider.run()\n"
    )
    node = parse_file(str(src), "alias.py", str(tmp_path))
    assert node is not None
    assert len(node.external_calls) >= 1

    call = node.external_calls[0]
    assert call.caller_func == "run"
    assert call.callee_module == "src.providers"
    assert call.callee_name == "run"


def test_parse_file_syntax_error(tmp_path):
    """A malformed .py file returns None."""
    src = tmp_path / "bad.py"
    src.write_text("def foo(:\n    pass\n")
    node = parse_file(str(src), "bad.py", str(tmp_path))
    assert node is None


# ---------------------------------------------------------------------------
# resolve_import
# ---------------------------------------------------------------------------


def test_resolve_import_absolute(tmp_path):
    """Absolute import resolves to <module>.py within the project."""
    (tmp_path / "mymod.py").write_text("x = 1\n")
    result = resolve_import("mymod", str(tmp_path))
    assert result == "mymod.py"


def test_resolve_import_relative_dot(tmp_path):
    """Single-dot relative import (level=1) resolves to a sibling module."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "sibling.py").write_text("x = 1\n")

    result = resolve_import(
        "sibling", str(tmp_path),
        source_file="pkg/main.py", level=1,
    )
    assert result == "pkg/sibling.py"


def test_resolve_import_relative_dotdot(tmp_path):
    """Double-dot relative import (level=2) resolves to parent package module."""
    pkg = tmp_path / "pkg"
    sub = pkg / "sub"
    sub.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (sub / "__init__.py").write_text("")
    (pkg / "helper.py").write_text("x = 1\n")

    result = resolve_import(
        "helper", str(tmp_path),
        source_file="pkg/sub/main.py", level=2,
    )
    assert result == "pkg/helper.py"


def test_resolve_import_stdlib():
    """stdlib module name resolves to None (not a project file)."""
    result = resolve_import("json", "/some/dir")
    assert result is None


def test_resolve_import_third_party():
    """Unresolvable third-party package resolves to None."""
    result = resolve_import("requests", "/some/dir")
    assert result is None


def test_resolve_import_package_init(tmp_path):
    """Importing a package resolves to its __init__.py."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("x = 1\n")

    result = resolve_import("mypkg", str(tmp_path))
    assert result == "mypkg/__init__.py"


# ---------------------------------------------------------------------------
# find_sccs  (Tarjan's algorithm)
# ---------------------------------------------------------------------------


def test_find_sccs_cycle():
    """A→B→C→A yields one cycle with three nodes."""
    adj = {
        "A": ["B"],
        "B": ["C"],
        "C": ["A"],
    }
    sccs = find_sccs(adj)
    cycles = [s for s in sccs if len(s) >= 2]
    assert len(cycles) == 1
    assert set(cycles[0]) == {"A", "B", "C"}


def test_find_sccs_no_cycle():
    """A→B→C with no back-edge yields no cycles (2+ nodes)."""
    adj = {
        "A": ["B"],
        "B": ["C"],
        "C": [],
    }
    sccs = find_sccs(adj)
    cycles = [s for s in sccs if len(s) >= 2]
    assert len(cycles) == 0


def test_find_sccs_two_cycles():
    """A↔B and C↔D yields two separate 2-node cycles."""
    adj = {
        "A": ["B"],
        "B": ["A"],
        "C": ["D"],
        "D": ["C"],
    }
    sccs = find_sccs(adj)
    cycles = [s for s in sccs if len(s) >= 2]
    assert len(cycles) == 2
    cycle_sets = [set(c) for c in cycles]
    assert {"A", "B"} in cycle_sets
    assert {"C", "D"} in cycle_sets


# ---------------------------------------------------------------------------
# build_dependency_graph
# ---------------------------------------------------------------------------


def test_build_dependency_graph(tmp_path):
    """Build a graph from 4 interdependent files and verify edges."""
    (tmp_path / "a.py").write_text("from b import func_b\n")
    (tmp_path / "b.py").write_text("from c import func_c\n")
    (tmp_path / "c.py").write_text("def func_c(): pass\n")
    (tmp_path / "d.py").write_text("from a import func_b\nfrom c import func_c\n")

    graph = build_dependency_graph(str(tmp_path))
    assert len(graph.files) == 4
    assert "a.py" in graph.files
    assert "b.py" in graph.files
    assert "c.py" in graph.files
    assert "d.py" in graph.files

    # a.py → b.py
    assert any(
        e.resolved_path == "b.py" for e in graph.edges if e.source_file == "a.py"
    )
    # b.py → c.py
    assert any(
        e.resolved_path == "c.py" for e in graph.edges if e.source_file == "b.py"
    )
    # d.py → a.py and d.py → c.py
    assert any(
        e.resolved_path == "a.py" for e in graph.edges if e.source_file == "d.py"
    )
    assert any(
        e.resolved_path == "c.py" for e in graph.edges if e.source_file == "d.py"
    )


def test_dependency_graph_helpers():
    """dependents_of and dependencies_of return correct sets from edges."""
    graph = DependencyGraph()
    graph.edges = [
        ImportEdge("a.py", "b", ["func_b"], 1, resolved_path="b.py"),
        ImportEdge("b.py", "c", ["func_c"], 1, resolved_path="c.py"),
        ImportEdge("d.py", "a", ["func_a"], 1, resolved_path="a.py"),
    ]

    # dependents_of("a.py") → files that import from a.py
    assert graph.dependents_of("a.py") == {"d.py"}

    # dependencies_of("a.py") → files a.py imports from
    assert graph.dependencies_of("a.py") == {"b.py"}

    # dependents_of("b.py")
    assert graph.dependents_of("b.py") == {"a.py"}

    # dependencies_of("b.py")
    assert graph.dependencies_of("b.py") == {"c.py"}

    # dependents_of("c.py") → a.py (via b.py's edge resolves to c.py, not a.py directly)
    # Only edges with resolved_path == "c.py" count: b.py → c.py
    assert graph.dependents_of("c.py") == {"b.py"}
