"""LLM-driven input synthesis for ldb — synthesize_inputs_llm(provider, source, entry)."""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from typing import List

from src.ldb.prompts import INPUT_PROMPT_LDB
from src.providers.message_adapter import normalize_message


def _dotted_parts(entry: str) -> tuple[str | None, str]:
    """Split 'Class.method' into (class_name, method_name) or (None, entry)."""
    if "." in entry:
        cls, _, method = entry.partition(".")
        return cls, method
    return None, entry


def _extract_signature(source: str, entry: str) -> str:
    """Extract the function signature and docstring for *entry* from *source*."""
    cls_name, func_name = _dotted_parts(entry)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return f"def {entry}(...): ..."
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        if cls_name is not None:
            parent = getattr(node, "parent_class", None)
            if parent is None:
                for cls_node in tree.body:
                    if (
                        isinstance(cls_node, ast.ClassDef)
                        and cls_node.name == cls_name
                        and node in cls_node.body
                    ):
                        parent = cls_node
                        break
            if parent is None:
                for cls_node in ast.walk(tree):
                    if isinstance(cls_node, ast.ClassDef) and cls_node.name == cls_name:
                        found = False
                        for child in cls_node.body:
                            if child is node:
                                found = True
                                break
                        if found:
                            parent = cls_node
                            break
                if parent is None:
                    continue
        lines = source.splitlines()
        sig_end = (node.body[0].lineno - 1) if node.body else node.lineno
        sig_lines = lines[node.lineno - 1 : sig_end]
        sig = "\n".join(sig_lines).strip()
        docstring = ast.get_docstring(node, clean=True) or ""
        parts = [sig.rstrip(":")]
        if docstring:
            parts.append(f'    """{docstring}"""')
        return "\n".join(parts)
    return f"def {entry}(...): ..."


def _extract_surrounding_code(source: str, entry: str, context_lines: int = 40) -> str:
    """Return a window of source around *entry*."""
    cls_name, func_name = _dotted_parts(entry)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source[:2000]
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        if cls_name is not None:
            matched = False
            for cls_node in ast.walk(tree):
                if isinstance(cls_node, ast.ClassDef) and cls_node.name == cls_name:
                    for child in cls_node.body:
                        if child is node:
                            matched = True
                            break
                    if matched:
                        break
            if not matched:
                continue
        lines = source.splitlines()
        start = max(0, node.lineno - context_lines - 1)
        end = min(len(lines), getattr(node, "end_lineno", node.lineno) + context_lines)
        return "\n".join(lines[start:end])
    return source[:2000]


async def _collect_text(
    provider,
    prompt: str,
    system_prompt: str,
    working_dir: str,
    max_turns: int = 1,
    model: str = "",
) -> str:
    """Collect full text output from a provider call."""
    from src.config import get_effective_context_limit

    resolved_model = (
        model
        or getattr(provider, "config", None)
        and getattr(provider.config, "default_model", "")
        or "unknown"
    )
    effective_limit = get_effective_context_limit(resolved_model, 0, provider=provider)

    run_kwargs: dict = {
        "prompt": prompt,
        "system_prompt": system_prompt,
        "working_dir": working_dir,
        "max_turns": max_turns,
        "model": model,
    }
    params = inspect.signature(provider.run).parameters
    accepts_kwargs = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    if "context_limit" in params or accepts_kwargs:
        run_kwargs["context_limit"] = effective_limit

    parts: list[str] = []
    async for message in provider.run(**run_kwargs):
        adapted = normalize_message(message)
        if adapted:
            text = adapted.get_text_content()
            if text:
                parts.append(text)
    return "\n".join(parts)


def _parse_entry_lines(raw: str) -> list[str]:
    """Extract entry(...) lines from synthesizer output."""
    matches = re.findall(r"^entry\(.*\)\s*$", raw, re.MULTILINE)
    return matches


async def synthesize_inputs_llm(
    provider, source: str, entry: str, working_dir: str = ".", model: str = ""
) -> List[str]:
    """Use an LLM to synthesize test inputs for a function.

    Args:
        provider: An AgentProvider instance used to call the LLM.
        source: Python source code containing the target function.
        entry: Name of the function to generate inputs for.
        working_dir: Working directory for the provider.
        model: Optional model override.

    Returns:
        List of entry(...) strings — each is a concrete call expression.
    """
    sig = _extract_signature(source, entry)
    surrounding = _extract_surrounding_code(source, entry)

    user_prompt = (
        f"## Target function: `{entry}`\n\n"
        f"### Signature\n```\n{sig}\n```\n\n"
        f"### Surrounding code\n```python\n{surrounding}\n```\n\n"
        f"Generate diverse test inputs for `{entry}`."
    )

    raw = await _collect_text(
        provider,
        prompt=user_prompt,
        system_prompt=INPUT_PROMPT_LDB,
        working_dir=working_dir,
        max_turns=1,
        model=model,
    )

    entries = _parse_entry_lines(raw)
    if entries:
        return entries

    return [
        line.strip()
        for line in raw.splitlines()
        if line.strip()
        and not line.strip().startswith("#")
        and not line.strip().startswith("```")
    ][:10]
