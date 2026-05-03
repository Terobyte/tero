"""Runtime tracer for LDB block-level debugging.

Two APIs:

  **New API** — pass keyword ``timeout=<int>`` to get LDB-style subprocess tracing:
      result = trace_function(source=src, test="fn(1, 2)", entry="fn", timeout=10)
      # result: TraceResult(kind, blocks, error)
      # blocks[i].rendered — lines with variable-value annotations

  **Legacy API** — positional args only, no ``timeout``, returns list[BlockTrace]:
      traces = trace_function(prog, {"a": 1}, "fn")
      # traces[i].hit_count, traces[i].lines_hit — in-process sys.settrace data

The new API uses subprocess isolation (``python -m trace -t`` + instrumented runner)
so it survives async/threads and complex import graphs in production code.

The ``_str=str`` header is prepended to every instrumented file; the generated
``print()`` calls use ``_str(__var_list[x])`` instead of ``str(...)`` because user
code can shadow the built-in ``str``.

Trace logs are written to ``{cwd}/.ldb-trace/`` (never to ``../tracing_log/``).
"""

from __future__ import annotations

import ast
import os
import random
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Union

from src.ldb.staticfg import CFGBuilder


@dataclass
class BlockTrace:
    """Trace record for one basic block.

    Legacy API populates ``lines_hit`` and ``hit_count``.
    New API populates ``rendered`` (annotated source lines).
    """
    block_id: int
    lines_hit: list[int] = field(default_factory=list)
    hit_count: int = 0
    rendered: list[str] = field(default_factory=list)


@dataclass
class TraceResult:
    """Result from the new LDB-style trace_function call."""
    kind: str   # "ok" | "timeout" | "fail" | "parse_fail"
    blocks: list[BlockTrace]
    error: str = ""


# _str=str must be the first line of every instrumented file.
# Instrumented print() calls use _str(__var_list[x]) instead of str()
# so user-defined __str__ / str overrides cannot corrupt the value log.
_STR_HEADER = "_str=str\n"


def _build_line_to_block(prog: str, entry: str) -> dict[int, int]:
    """Map source line numbers (1-based) to basic-block IDs via staticfg."""
    builder = CFGBuilder()
    cfg = builder.build_from_src(entry, prog)

    if entry not in cfg.functioncfgs:
        raise ValueError(f"function '{entry}' not found")

    fcfg = cfg.functioncfgs[entry]
    mapping: dict[int, int] = {}
    for raw_block in fcfg:
        for stmt in raw_block.statements:
            if isinstance(stmt, ast.stmt) and hasattr(stmt, "lineno"):
                start = stmt.lineno
                end = getattr(stmt, "end_lineno", start)
                for lineno in range(start, end + 1):
                    mapping[lineno] = raw_block.id
    return mapping


def _get_function_range(prog: str, entry: str) -> Optional[list[int]]:
    """Return [start_0indexed, end_0indexed] of function ``entry`` in prog."""
    try:
        tree = ast.parse(prog)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == entry:
            return [node.lineno - 1, node.end_lineno - 1]
    return None


def _divide(prog: str):
    """Split prog into basic blocks via staticfg.

    Returns ``(divided_blocks, error_str)`` where divided_blocks is a list of
    ``[block_obj, source_lines, block_id]`` triples (same format as LDB).
    """
    try:
        cfg = CFGBuilder().build_from_src("block", prog)
    except Exception as exc:
        return None, str(exc)

    prog_lines = prog.split("\n")
    divided_blocks = []
    for block in cfg:
        at = block.at()
        end = block.end()
        if at is not None and end is not None:
            divided_blocks.append([block, prog_lines[at : end + 1], block.id])
    return divided_blocks, None


def _get_after(stmts) -> tuple[str, int]:
    """Return first non-empty statement and its 4-space indent depth."""
    for s in stmts:
        if s == "":
            continue
        return s.strip(), (len(s) - len(s.lstrip())) // 4
    return "", 0


def _instrument_simple_block(prog: str, entry: str, divided_blocks: list) -> str:
    """Insert ``Value_After`` print calls at each basic-block boundary.

    Adapted from LDB ``tracer.py:instrument_simple_block``.
    The generated code uses ``_str`` — caller must prepend ``_STR_HEADER``.
    """
    stmts = prog.split("\n")
    rang = _get_function_range(prog, entry)
    if rang is None:
        return prog

    block_insert: set[int] = set()
    for b in divided_blocks:
        at = b[0].at()
        end = b[0].end()
        if at is not None:
            block_insert.add(at - 1)
        if end is not None:
            block_insert.add(end)

    res: list[str] = []
    for i, stmt in enumerate(stmts):
        if i < rang[0] or i > rang[1]:
            res.append(stmt)
            continue
        if (i + 1) not in block_insert:
            res.append(stmt)
            continue

        refs, indent_after = _get_after(list(reversed(stmts[: i + 1])))
        if refs.startswith(("else:", "elif ", "if ", "while ", "for ", "def ")):
            refs, indent_after = _get_after(stmts[i + 1 :])

        payload = (
            "    " * indent_after
            + f"__var_list = vars();"
            + f"print(f'Value_After:{i + 1}|'"
            + " + '|'.join([(x + '=' + _str(__var_list[x]))"
            + " for x in __var_list if not x.startswith('__')]));"
        )

        if " return " in stmt:
            stmt = stmt.replace(" return ", " _ret = ")
            payload = payload + " return _ret"

        res.append(stmt)
        res.append(payload)

    return "\n".join(res)


def _normalize_test_for_trace(test: Any) -> str:
    """Convert test to a runnable string (replace ``assert X == Y`` -> ``print(X)``).

    LDB avoids AssertionError interrupting the trace by converting assertion
    statements to prints so the execution path is captured even for buggy code.
    """
    if test is None:
        return ""
    if not isinstance(test, str):
        return str(test)
    result = test
    if "assert " in result:
        result = result.replace("assert ", "print(")
        if " == " in result:
            result = result.split(" == ")[0] + ")"
    return result


def _get_trace(
    exec_prog: str, entry: str, trace_dir: Path, timeout: int
) -> Union[list[str], str]:
    """Run ``python -m trace -t`` on exec_prog and return filtered trace lines.

    Temp file lives in ``trace_dir`` and is cleaned up unconditionally.
    Named ``_ldb_t.py.XXXX`` so Python's trace module derives module name
    ``_ldb_t.py`` (via ``os.path.splitext``), making the mark predictable.
    """
    rid = random.randint(0, 100_000)
    fname = str(trace_dir / f"_ldb_t.py.{rid}")
    try:
        with open(fname, "w") as fh:
            fh.write(exec_prog)

        try:
            res = subprocess.run(
                [sys.executable, "-m", "trace", "-t", fname],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return "*timeout*"
        except Exception as exc:
            return f"*execution fail*{exc}"
    finally:
        try:
            os.remove(fname)
        except FileNotFoundError:
            pass

    trace_out = res.stdout.decode("utf-8", errors="replace")

    # Python's trace module derives module name via os.path.splitext(basename)[0].
    # The mark is emitted with a leading space: " --- modulename: ..."
    # Trace lines use just the basename (not full path), e.g. "_ldb_t.py.12345(3): code"
    basename = os.path.basename(fname)          # "_ldb_t.py.12345"
    module_name = os.path.splitext(basename)[0] # "_ldb_t.py"
    mark = f"--- modulename: {module_name}, funcname: {entry}\n"

    if mark not in trace_out:
        return f"*parse fail*function '{entry}' not found in trace"

    raw_lines = trace_out.split(mark)[1].split("\n")

    trace_lines: list[str] = []
    for line in raw_lines:
        stripped = line.lstrip()
        if stripped.startswith(("'''", '"""', "#")):
            continue
        # Trace lines start with the BASENAME (not full path), e.g. "_ldb_t.py.12345(3): code"
        if stripped.startswith(basename):
            suffix = stripped[len(basename):]   # "(lineno): code"
            if not trace_lines or trace_lines[-1] != suffix:
                trace_lines.append(suffix)

    return trace_lines


def _collect_runtime_value(
    value_prof_prog: str, trace_dir: Path, timeout: int
) -> str:
    """Run value_prof_prog as a script and capture stdout."""
    rid = random.randint(0, 100_000)
    fname = str(trace_dir / f"_ldb_vp.py.{rid}")
    try:
        with open(fname, "w") as fh:
            fh.write(value_prof_prog)

        try:
            res = subprocess.run(
                [sys.executable, fname],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return "*timeout*"
        except Exception as exc:
            return f"*execution fail*{exc}"
    finally:
        try:
            os.remove(fname)
        except FileNotFoundError:
            pass

    stderr = res.stderr.decode("utf-8", errors="replace")
    if "Traceback (most recent call last):" in stderr and "AssertionError" not in stderr:
        return f"*execution fail*{stderr[:200]}"

    return res.stdout.decode("utf-8", errors="replace")


def _get_lineno(trace_line: str) -> int:
    """Extract line number from trace suffix like ``(3): code``."""
    m = re.search(r"\((\d+)\):", trace_line)
    return int(m.group(1)) if m else -1


def _get_line(trace_line: str) -> str:
    """Extract code text from trace suffix like ``(3):  code``."""
    m = re.search(r"\(\d+\):", trace_line)
    return trace_line[m.end() + 1 :] if m else trace_line


def _get_indent(trace_line: str) -> int:
    """Return 4-space indent depth of the code in a trace suffix line."""
    code = _get_line(trace_line)
    return (len(code) - len(code.lstrip())) // 4


def _extract_value_lines(output: str) -> list[str]:
    """Filter stdout to lines starting with ``Value_After:``."""
    return [x for x in output.split("\n") if x.startswith("Value_")]


def _parse_runtime_value_simple_block(
    output: str, trace_lines: list[str]
) -> list[list[str]]:
    """Interleave trace lines with value annotations to produce rendered blocks.

    Adapted from LDB ``tracer.py:parse_runtime_value_simple_block``.
    Each returned block is a list of strings like:
      ``["# a=2  b=3", "    s = a - b", "# a=2  b=3  s=-1"]``
    """
    value_profiles = _extract_value_lines(output)
    trace_len = len(trace_lines)

    if not value_profiles or trace_len == 0:
        return [[_get_line(l) for l in trace_lines]] if trace_lines else []

    trace_linenos = [_get_lineno(l) for l in trace_lines]
    blocks: list[list[str]] = []
    blk: list[str] = []
    last_bp = ""
    trace_idx = 0

    for vp_line in value_profiles:
        if trace_idx >= trace_len:
            break
        parts = vp_line.split(":")
        try:
            lineno = int(parts[1].split("|")[0])
        except (IndexError, ValueError):
            continue

        values = "\t".join(vp_line.split("|")[1:])
        if len(values) > 100:
            values = values[:50] + "..." + values[-50:]

        if lineno not in trace_linenos:
            last_bp = values
            continue

        indent = _get_indent(trace_lines[trace_idx])
        blk.append("    " * indent + "# " + last_bp)

        while trace_idx < trace_len and _get_lineno(trace_lines[trace_idx]) != lineno:
            blk.append(_get_line(trace_lines[trace_idx]))
            trace_idx += 1

        if trace_idx == trace_len:
            break

        blk.append(_get_line(trace_lines[trace_idx]))
        blk.append("    " * _get_indent(trace_lines[trace_idx]) + "# " + values)
        last_bp = values
        blocks.append(blk)
        blk = []
        trace_idx += 1

    if trace_idx < trace_len:
        leading_indent = _get_indent(trace_lines[trace_idx])
        trailer = ["    " * leading_indent + "# " + last_bp] + blk
        while trace_idx < trace_len:
            trailer.append(_get_line(trace_lines[trace_idx]))
            trace_idx += 1
        blocks.append(trailer)

    return blocks


def _trace_ldb(source: str, test: Any, entry: str, timeout: int) -> TraceResult:
    """New LDB-style tracing via subprocess.  Returns TraceResult."""
    working_dir = Path.cwd()
    trace_dir = working_dir / ".ldb-trace"
    trace_dir.mkdir(exist_ok=True)

    try:
        ast.parse(source)
    except SyntaxError as exc:
        return TraceResult(kind="fail", blocks=[], error=f"SyntaxError: {exc}")

    # _str=str header is mandatory — instrumented prints use _str()
    header_source = _STR_HEADER + source

    divided_blocks, divide_error = _divide(header_source)
    if divided_blocks is None:
        return TraceResult(kind="fail", blocks=[], error=divide_error or "divide failed")

    trace_test = _normalize_test_for_trace(test)
    exec_prog = header_source + "\n" + trace_test

    # Step 1: line-level trace via python -m trace -t
    trace_lines = _get_trace(exec_prog, entry, trace_dir, timeout)
    if isinstance(trace_lines, str):
        if trace_lines == "*timeout*":
            return TraceResult(kind="timeout", blocks=[], error="execution timed out")
        return TraceResult(kind="fail", blocks=[], error=trace_lines)

    # Step 2: instrument and run to collect variable values at block boundaries
    instrumented = _instrument_simple_block(header_source, entry, divided_blocks)
    value_output = _collect_runtime_value(
        instrumented + "\n" + trace_test, trace_dir, timeout
    )
    if isinstance(value_output, str) and (
        value_output == "*timeout*" or value_output.startswith("*execution fail*")
    ):
        return TraceResult(kind="fail", blocks=[], error=value_output)

    # Step 3: parse into rendered blocks
    rendered_groups = _parse_runtime_value_simple_block(value_output, trace_lines)

    # Write trace log to .ldb-trace/ (not ../tracing_log/ as in LDB upstream)
    log_file = trace_dir / f"trace.log.{random.randint(0, 100_000)}"
    try:
        log_file.write_text(
            f"Trace:\n{''.join(trace_lines[:10])}\n\nValue Profile Output:\n{value_output}"
        )
    except OSError:
        pass

    blocks = [
        BlockTrace(block_id=i, rendered=group)
        for i, group in enumerate(rendered_groups)
    ]
    return TraceResult(kind="ok", blocks=blocks, error="")


def _trace_legacy(prog: str, test: Any, entry: str) -> list[BlockTrace]:
    """Legacy in-process tracing via sys.settrace.  Returns list[BlockTrace]."""
    line_to_block = _build_line_to_block(prog, entry)

    filename = "<ldb_trace>"
    code = compile(prog, filename, "exec")
    namespace: dict[str, Any] = {}
    exec(code, namespace)  # noqa: S102

    func = namespace.get(entry)
    if func is None:
        raise ValueError(f"function '{entry}' not found after compilation")

    visited: dict[int, BlockTrace] = {}
    order: list[int] = []

    def _on_trace(frame, event, _arg):
        if event == "line" and frame.f_code.co_filename == filename:
            bid = line_to_block.get(frame.f_lineno)
            if bid is not None:
                if bid not in visited:
                    visited[bid] = BlockTrace(block_id=bid)
                    order.append(bid)
                visited[bid].lines_hit.append(frame.f_lineno)
                visited[bid].hit_count += 1
        return _on_trace

    old_trace = sys.gettrace()
    sys.settrace(_on_trace)
    try:
        if isinstance(test, dict):
            func(**test)
        elif isinstance(test, str):
            exec(test, namespace)  # noqa: S102
        elif callable(test):
            test(func)
        else:
            func()
    finally:
        sys.settrace(old_trace)

    return [visited[bid] for bid in order]


def trace_function(
    source: Any = None,
    test: Any = None,
    entry: str = None,
    *,
    timeout: Optional[int] = None,
) -> Union[TraceResult, List[BlockTrace]]:
    """Execute *entry* from *source* under *test* and return a trace.

    **New API** — pass ``timeout=<int>`` (keyword-only):
        Returns ``TraceResult(kind, blocks, error)``.  Each ``block.rendered``
        is a list of annotated source lines with variable values at block
        boundaries.  Uses subprocess isolation so production imports/async
        code does not interfere with the tracer.

    **Legacy API** — positional args, no ``timeout``:
        Returns ``list[BlockTrace]`` with ``block_id``, ``lines_hit``,
        ``hit_count``.  Uses in-process ``sys.settrace``.

    Args:
        source: Python source code string containing the target function.
        test:   Test input — dict of kwargs, str of test code, callable
                receiving the target function, or None (no args).
        entry:  Name of the function to trace.
        timeout: Max seconds per subprocess call (activates new LDB path).
    """
    if source is None:
        raise ValueError("source (first argument) is required")

    if timeout is not None:
        return _trace_ldb(str(source), test, entry, timeout)
    return _trace_legacy(str(source), test, entry)
