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
        if verbose:
            result = getattr(msg, "content", "")
            if result:
                _print_result(result)
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
        print(f"  {DIM}[text]{RESET} {line}")


def _print_tool(name: str, args: dict):
    """Print tool usage."""
    summary = _summarize_tool_args(name, args)
    print(f"  {CYAN}[tool]{RESET} {BOLD}{name}{RESET}: {DIM}{summary}{RESET}")


def _print_result(result: str):
    """Print tool result (only in verbose mode)."""
    if not result.strip():
        return
    display = result.strip()[:100]
    print(f"  {DIM}[result] {display}...{RESET}")


def _summarize_tool_args(name: str, args: dict) -> str:
    """Create a brief summary of tool arguments."""
    if name == "Read":
        return args.get("file_path", "?").split("/")[-1]
    elif name == "Write":
        return args.get("file_path", "?").split("/")[-1]
    elif name == "Edit":
        return f"{args.get('file_path', '?').split('/')[-1]} ({args.get('old_string', '')[:20]}...)"
    elif name == "Bash":
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


def print_step_header(step_num: int, total_steps: int, step_text: str):
    """Print step start header."""
    bar = _progress_bar(step_num - 1, total_steps)
    print(f"\n{BOLD}{CYAN}{'─' * 50}{RESET}")
    print(f"{BOLD}{CYAN}  Шаг {step_num}/{total_steps}: {step_text}{RESET}")
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
    filled = int(width * done / total)
    bar = "■" * filled + "□" * (width - filled)
    pct = int(100 * done / total)
    return f"[{bar}] {done}/{total} ({pct}%)"


def print_continuation_started(role: str, attempt: int, max_attempts: int) -> None:
    """Print notification when continuation agent starts."""
    print(f"\n{BOLD}🔄 [{role}]{RESET} No completion markers — "
          f"continuation agent {attempt}/{max_attempts}...")


def print_compact_triggered(tokens_used: int, context_limit: int) -> None:
    """Print notification when context compaction fires."""
    print(f"\n{BOLD}⚡ Context compacted{RESET} "
          f"({tokens_used // 1000}k/{context_limit // 1000}k tokens) — continuing...")


def print_batch_turn_header(role: str, phase_name: str, attempt: int, max_attempts: int, model_name: str = ""):
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


def print_step_list(plan_items: list) -> None:
    """Print done/remaining step list for session summary."""
    for item in plan_items:
        icon = f"{GREEN}✓{RESET}" if item.done else f"{RED}□{RESET}"
        print(f"    {icon} {item.text}")
