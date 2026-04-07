"""Real-time terminal UI for SDK messages."""

# ANSI color codes
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
BLUE   = "\033[34m"
CYAN   = "\033[36m"
YELLOW = "\033[33m"
GREEN  = "\033[32m"
RED    = "\033[31m"


def _get_msg_type(msg) -> str:
    """Get message type name, supporting duck-typing."""
    # Check for __class_name__ (AdaptedMessage)
    if hasattr(msg, "__class_name__"):
        return getattr(msg, "__class_name__")
    return type(msg).__name__


def stream_messages(msg, verbose: bool = False, role: str = "") -> int:
    """Stream SDK messages to terminal in real-time.

    Returns count of tools used.
    """
    tools_used = 0
    msg_type = _get_msg_type(msg)

    # AssistantMessage with content
    if msg_type == "AssistantMessage" or hasattr(msg, "role") and getattr(msg, "role") == "assistant":
        content = getattr(msg, "content", None)
        if content:
            if isinstance(content, str):
                _print_text(content, verbose)
            elif isinstance(content, list):
                for block in content:
                    block_type = type(block).__name__
                    if block_type == "TextBlock" or hasattr(block, "text"):
                        text = getattr(block, "text", "")
                        _print_text(text, verbose)
                    elif block_type == "ToolUseBlock" or hasattr(block, "name"):
                        tools_used += 1
                        tool_name = getattr(block, "name", "unknown")
                        tool_input = getattr(block, "input", {})
                        _print_tool(tool_name, tool_input)
        return tools_used

    # ToolUseBlock directly (check type via duck-typing)
    if msg_type == "ToolUseBlock" or (hasattr(msg, "name") and hasattr(msg, "input")):
        tools_used += 1
        tool_name = getattr(msg, "name", "unknown")
        tool_input = getattr(msg, "input", {})
        _print_tool(tool_name, tool_input)
        return tools_used

    # ToolResultMessage
    if msg_type == "ToolResultMessage" or hasattr(msg, "tool_use_id"):
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            for block in content:
                if type(block).__name__ == "ToolUseBlock" or (
                    hasattr(block, "name") and hasattr(block, "input")
                ):
                    tools_used += 1
                    tool_name = getattr(block, "name", "unknown")
                    tool_input = getattr(block, "input", {})
                    _print_tool(tool_name, tool_input)
        for result, is_error in _extract_tool_results(msg):
            if verbose or is_error:
                _print_result(result, is_error=is_error)
        return tools_used

    return tools_used


def _print_text(text: str, verbose: bool = False):
    """Print text block content."""
    if not text.strip():
        return

    display = text.strip()
    if len(display) > 200 and not verbose:
        display = display[:200] + "..."

    lines = display.split("\n")
    if not verbose:
        lines = lines[:5]

    for line in lines:
        print(f"  {DIM}[text]{RESET} {line}", flush=True)


def _print_tool(name: str, args: dict):
    """Print tool usage."""
    summary = _summarize_tool_args(name, args)
    print(f"  {CYAN}[tool]{RESET} {BOLD}{name}{RESET}: {DIM}{summary}{RESET}", flush=True)


def _extract_tool_results(msg) -> list[tuple[str, bool]]:
    """Normalize tool result payloads from SDK and AdaptedMessage objects."""
    content = getattr(msg, "content", "")

    if isinstance(content, list):
        results = []
        for block in content:
            if hasattr(block, "content"):
                results.append((
                    getattr(block, "content", ""),
                    bool(getattr(block, "is_error", False)),
                ))
            elif hasattr(block, "text"):
                results.append((getattr(block, "text", ""), False))
        return results

    return [(content, bool(getattr(msg, "is_error", False)))]


def _print_result(result: str, is_error: bool = False):
    """Print tool result payloads for verbose or error cases."""
    if not result.strip():
        return
    display = result.strip()
    if len(display) > 100:
        display = display[:100] + "..."

    color = RED if is_error else DIM
    print(f"  {color}[result]{RESET} {display}", flush=True)


def _summarize_tool_args(name: str, args: dict) -> str:
    """Create a brief summary of tool arguments."""
    file_path = args.get("file_path") or args.get("path") or args.get("file") or "?"

    if name in ("Read", "file_read"):
        return file_path.split("/")[-1]
    elif name in ("Write", "file_write"):
        return file_path.split("/")[-1]
    elif name in ("Edit", "file_edit"):
        snippet = args.get("old_string", "")[:20]
        return f"{file_path.split('/')[-1]} ({snippet}...)" if snippet else file_path.split("/")[-1]
    elif name in ("Bash", "shell"):
        cmd = args.get("command", "?")
        return cmd[:50] + "..." if len(cmd) > 50 else cmd
    elif name == "Glob":
        return args.get("pattern", "?")
    elif name == "Grep":
        return args.get("pattern", "?")
    else:
        if args:
            first_val = next(iter(args.values()))
            if isinstance(first_val, str):
                return first_val[:30]
        return ""


def print_player_header(
    step_num: int,
    total_steps: int,
    attempt: int,
    max_attempts: int,
    model_name: str = "",
):
    """Print player turn header."""
    attempt_info = f"  попытка {attempt}/{max_attempts}" if attempt > 1 else ""
    model_info = f" [{model_name}]" if model_name else ""
    print(f"\n{BOLD}{BLUE}═══ PLAYER{model_info} — шаг {step_num}/{total_steps}{attempt_info} {RESET}")


def print_coach_header(step_num: int, total_steps: int, attempt: int, model_name: str):
    """Print coach turn header."""
    print(f"\n{BOLD}{YELLOW}═══ COACH [{model_name}] — шаг {step_num}/{total_steps} попытка {attempt} {RESET}")


def _format_roles(roles: list[str] | None) -> str:
    """Render active persona roles for terminal output."""
    ordered: list[str] = []
    for role in roles or []:
        if role and role not in ordered:
            ordered.append(role)
    return ", ".join(ordered)


def print_batch_turn_header(
    role: str,
    phase_name: str,
    attempt: int,
    max_attempts: int,
    model_name: str = "",
    active_roles: list[str] | None = None,
):
    """Print a batch phase turn header with explicit role/model labeling."""
    role_upper = role.upper()
    colors = {
        "player": BLUE,
        "coach": YELLOW,
        "judge": GREEN,
    }
    color = colors.get(role, CYAN)
    model_info = f" [{model_name}]" if model_name else ""
    print(
        f"\n{BOLD}{color}═══ {role_upper}{model_info} — фаза {phase_name} "
        f"попытка {attempt}/{max_attempts} {RESET}"
    )
    roles_text = _format_roles(active_roles)
    if roles_text:
        print(f"{DIM}  Роли: {roles_text}{RESET}")


def print_preplanner_header(model_name: str = "") -> None:
    """Print the Phase 0 pre-planner start header."""
    model_info = f" [{model_name}]" if model_name else ""
    print(f"\n{BOLD}{CYAN}═══ PHASE 0{model_info} — polishing plan {RESET}")


def print_preplan_result(
    num_phases: int,
    num_roles: int,
    enriched_plan_path: str = "",
) -> None:
    """Print the Phase 0 pre-planner completion summary."""
    print(
        f"  {GREEN}Plan polished: {num_phases} phases, {num_roles} role assignments{RESET}\n"
    )
    if enriched_plan_path:
        print(f"  {DIM}Enriched plan: {enriched_plan_path}{RESET}\n")


def print_step_header(
    step_num: int,
    total_steps: int,
    step_text: str,
    roles: list[str] | None = None,
):
    """Print step start header."""
    bar = _progress_bar(step_num - 1, total_steps)
    print(f"\n{BOLD}{CYAN}{'─' * 50}{RESET}")
    print(f"{BOLD}{CYAN}  Шаг {step_num}/{total_steps}: {step_text}{RESET}")
    roles_text = _format_roles(roles)
    if roles_text:
        print(f"{DIM}  Роли: {roles_text}{RESET}")
    print(f"{CYAN}  {bar}{RESET}")
    print(f"{BOLD}{CYAN}{'─' * 50}{RESET}")


def print_step_approved(step_num: int, step_text: str):
    """Print step approval."""
    print(f"\n  {BOLD}{GREEN}✓ Шаг {step_num} принят: {step_text}{RESET}")


def print_step_rejected(issues_text: str):
    """Print coach rejection with issues."""
    print(f"\n  {BOLD}{RED}✗ Отклонено — проблемы:{RESET}")
    for line in issues_text.strip().splitlines():
        if line.strip():
            print(f"    {RED}{line}{RESET}")


def print_turn_timing(
    role: str,
    duration_s: float,
    tools_used: int,
    tokens_used: int = 0,
    context_window: int = 0,
):
    """Print turn completion timing with optional token usage."""
    token_info = ""
    if tokens_used > 0:
        pct = f" | {int(100 * tokens_used / context_window)}% ctx" if context_window > 0 else ""
        token_info = f" | {tokens_used:,} ◉{pct}"
    print(f"\n  {DIM}{role.capitalize()}: {duration_s:.0f}s | Инструменты: {tools_used}{token_info}{RESET}")


def print_all_done(total_steps: int):
    """Print session complete banner."""
    bar = _progress_bar(total_steps, total_steps)
    print(f"\n{BOLD}{GREEN}{'═' * 50}{RESET}")
    print(f"{BOLD}{GREEN}  ✓ Все {total_steps} шагов выполнены!{RESET}")
    print(f"{GREEN}  {bar}{RESET}")
    print(f"{BOLD}{GREEN}{'═' * 50}{RESET}")


def print_verdict(approved: bool, issues: list[str] | None = None):
    """Backward-compatible verdict printer used by older tests/callers."""
    if approved:
        print(f"\n{BOLD}{GREEN}APPROVED{RESET}")
        return

    print(f"\n{BOLD}{RED}NOT APPROVED{RESET}")
    for issue in issues or []:
        print(f"  - {issue}")


def _progress_bar(done: int, total: int, width: int = 20) -> str:
    """Render a simple progress bar."""
    if total == 0:
        return "[]"
    done = max(0, min(done, total))
    filled = int(width * done / total)
    bar = "■" * filled + "□" * (width - filled)
    pct = int(100 * done / total)
    return f"[{bar}] {done}/{total} ({pct}%)"


def print_test_writer_header(step_num: int, total_steps: int):
    """Print header for test writer phase."""
    print(f"\n{BOLD}{CYAN}🧪 TEST WRITER — шаг {step_num}/{total_steps}{RESET}")


def print_tdd_status(tests_passed: bool, test_output: str):
    """Print TDD test run results."""
    if tests_passed:
        print(f"\n  {BOLD}{GREEN}✓ Тесты прошли{RESET}")
    else:
        print(f"\n  {BOLD}{RED}✗ Тесты упали:{RESET}")
        for line in test_output.strip().splitlines()[:10]:
            if line.strip():
                print(f"    {RED}{line}{RESET}")


def print_code_review_header(
    step_num: int,
    total_steps: int,
    provider_name: str = "",
    iteration: int = 1,
    max_iterations: int = 1,
):
    """Print header for code review phase."""
    provider_info = f" ({provider_name})" if provider_name else ""
    iter_info = f" iter {iteration}/{max_iterations}" if max_iterations > 1 else ""
    print(f"\n{BOLD}{YELLOW}🔍 CODE REVIEW{provider_info} — шаг {step_num}/{total_steps}{iter_info}{RESET}")


def print_review_passed(step_num: int):
    """Print code review passed message."""
    print(f"\n  {BOLD}{GREEN}✓ Code Review passed — no critical issues{RESET}")


def print_review_issues(issues_text: str):
    """Print code review issues found."""
    print(f"\n  {BOLD}{YELLOW}⚠ Code Review found issues:{RESET}")
    for line in issues_text.strip().splitlines()[:10]:
        if line.strip():
            print(f"    {YELLOW}{line}{RESET}")


def print_step_list(plan_items: list) -> None:
    """Print done/remaining step list for session summary."""
    for item in plan_items:
        icon = f"{GREEN}✓{RESET}" if item.done else f"{RED}□{RESET}"
        print(f"    {icon} {item.text}")


def print_coach_no_verdict_retry(attempt: int, max_attempts: int):
    """Print coach retry message on NoVerdict."""
    print(f"\n  {BOLD}{YELLOW}⚠ Coach не дал вердикт — повтор {attempt}/{max_attempts}...{RESET}")


def print_coach_fallback_escalation(fallback_name: str):
    """Print fallback coach escalation message."""
    print(f"\n  {BOLD}{YELLOW}⚠ Coach молчит — передаю {fallback_name} для вердикта...{RESET}")


def print_compact_triggered(tokens_used: int, context_limit: int) -> None:
    """Print a compact notification with rough token usage context."""
    before_k = max(1, round(tokens_used / 1000))
    limit_k = max(1, round(context_limit / 1000))
    print(
        f"\n  {BOLD}{CYAN}⚡ Context compacted{RESET} "
        f"{DIM}({before_k}k / {limit_k}k tokens){RESET}"
    )


def print_continuation_started(role: str, attempt: int, max_attempts: int) -> None:
    """Print continuation retry status."""
    print(
        f"\n  {BOLD}{CYAN}↻ Continuation{RESET} "
        f"{DIM}{role} {attempt}/{max_attempts}{RESET}"
    )
