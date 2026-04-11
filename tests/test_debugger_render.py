"""Tests for src.debugger_render — file rendering, contract rendering, context assembly."""

from src.debugger_contracts import ExportContract, FileContract
from src.debugger_graph import DependencyGraph, ImportEdge
from src.debugger_render import (
    build_context_from_graph,
    render_contracts_section,
    render_file_with_lines,
)


# ---------------------------------------------------------------------------
# render_file_with_lines
# ---------------------------------------------------------------------------


def test_render_file_with_lines():
    """A small file (≤ 500 lines) is rendered in full with numbered lines."""
    source = "def hello():\n    return 42\n"
    result = render_file_with_lines("example.py", source)

    assert result.startswith("### File: example.py\n")
    assert "```python" in result
    assert result.endswith("```\n")
    # Line numbers present
    assert "1: def hello():" in result
    assert "2:     return 42" in result


def test_render_file_large_truncation():
    """A 600-line file is truncated: first 200 lines + marker + last 100 lines."""
    lines = [f"# line {i}" for i in range(1, 601)]
    source = "\n".join(lines)

    result = render_file_with_lines("big.py", source)

    assert result.startswith("### File: big.py\n")
    # Should contain the omission marker
    assert "lines omitted" in result
    # Head should include early lines
    assert "1: # line 1" in result
    assert "200: # line 200" in result
    # Tail should include late lines
    assert "501: # line 501" in result
    assert "600: # line 600" in result
    # Middle lines should NOT appear
    assert "250: # line 250" not in result
    # Symbol index should be appended (no top-level defs → no symbols in this
    # case, so we just verify the structure is valid markdown)
    assert "```" in result


# ---------------------------------------------------------------------------
# render_contracts_section
# ---------------------------------------------------------------------------


def test_render_contracts_section():
    """FileContract objects are rendered into a markdown section."""
    contract = FileContract(
        rel_path="src/utils.py",
        exports=[
            ExportContract(
                name="parse_int",
                signature="parse_int(s: str) -> int",
                preconditions=["s is non-empty"],
                postconditions=["result >= 0"],
                side_effects=["logs input"],
                raises=["ValueError"],
                return_type="int",
            ),
        ],
    )
    result = render_contracts_section({"src/utils.py": contract})

    assert result.startswith("## Dependency Contracts\n")
    assert "### src/utils.py" in result
    assert "#### `parse_int`" in result
    assert "**signature**" in result
    assert "**pre**" in result
    assert "**post**" in result
    assert "**side_effects**" in result
    assert "**raises**" in result
    assert "**returns**" in result
    assert "ValueError" in result


def test_render_contracts_empty():
    """An empty contracts dict produces an empty string."""
    assert render_contracts_section({}) == ""


# ---------------------------------------------------------------------------
# build_context_from_graph
# ---------------------------------------------------------------------------


def test_build_context_from_graph(tmp_path):
    """Two source files plus a shared dependency produce context with both
    file listings and the dependency contract."""
    # -- create files on disk --
    (tmp_path / "a.py").write_text("from shared import helper\nx = helper()\n")
    (tmp_path / "b.py").write_text("from shared import helper\ny = helper()\n")
    (tmp_path / "shared.py").write_text("def helper():\n    return 1\n")

    # -- graph with edges from a/b → shared --
    graph = DependencyGraph(
        files={},
        edges=[
            ImportEdge("a.py", "shared", ["helper"], 1, "shared.py"),
            ImportEdge("b.py", "shared", ["helper"], 1, "shared.py"),
        ],
    )

    # -- contracts for the shared dep --
    shared_contract = FileContract(
        rel_path="shared.py",
        exports=[
            ExportContract(name="helper", signature="helper() -> int", return_type="int"),
        ],
    )
    contracts = {"shared.py": shared_contract}

    ctx = build_context_from_graph(
        graph, contracts, str(tmp_path), ["a.py", "b.py"]
    )

    # Both files rendered
    assert "### File: a.py" in ctx
    assert "### File: b.py" in ctx
    # Dependency contract section present
    assert "## Dependency Contracts" in ctx
    assert "### shared.py" in ctx
    assert "`helper`" in ctx


def test_build_context_deduplicates_deps(tmp_path):
    """When two files share the same dependency, the dep contract appears once."""
    (tmp_path / "alpha.py").write_text("from util import do_thing\n")
    (tmp_path / "beta.py").write_text("from util import do_thing\n")
    (tmp_path / "util.py").write_text("def do_thing(): pass\n")

    graph = DependencyGraph(
        files={},
        edges=[
            ImportEdge("alpha.py", "util", ["do_thing"], 1, "util.py"),
            ImportEdge("beta.py", "util", ["do_thing"], 1, "util.py"),
        ],
    )

    util_contract = FileContract(
        rel_path="util.py",
        exports=[ExportContract(name="do_thing", signature="do_thing()")],
    )
    contracts = {"util.py": util_contract}

    ctx = build_context_from_graph(
        graph, contracts, str(tmp_path), ["alpha.py", "beta.py"]
    )

    # The contract heading for util.py should appear exactly once
    assert ctx.count("### util.py") == 1
