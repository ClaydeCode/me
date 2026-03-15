"""Claude API invocation via the Anthropic Python SDK."""

import dataclasses
import json
import logging
import subprocess
import time
from pathlib import Path

import anthropic

from clayde.config import APP_DIR, get_settings
from clayde.git import commit_wip
from clayde.telemetry import get_tracer

log = logging.getLogger("clayde.claude")

# Pricing in USD per 1M tokens (input, output) for known models.
# Update these periodically as pricing changes.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-6": (15.0, 75.0),
    "claude-opus-4-5": (15.0, 75.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (0.8, 4.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-3-5-sonnet-20241022": (3.0, 15.0),
    "claude-3-5-haiku-20241022": (0.8, 4.0),
    "claude-3-opus-20240229": (15.0, 75.0),
}

# EUR/USD conversion rate — last updated: 2026-03
_EUR_PER_USD = 0.92


@dataclasses.dataclass
class InvocationResult:
    """Result of a Claude invocation, including output text and cost."""

    output: str
    cost_eur: float
    input_tokens: int
    output_tokens: int


class UsageLimitError(Exception):
    """Raised when Claude API reports a usage/rate limit."""

    def __init__(self, message: str, cost_eur: float = 0.0):
        super().__init__(message)
        self.cost_eur = cost_eur


def format_cost_line(cost_eur: float) -> str:
    """Format a cost line for inclusion in GitHub comments."""
    return f"\n\n💸 This task cost {cost_eur:.2f}€"


def _get_client() -> anthropic.Anthropic:
    """Return an Anthropic client configured with the API key from settings."""
    settings = get_settings()
    return anthropic.Anthropic(api_key=settings.claude_api_key)


def _calculate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for the given model and token counts."""
    input_price, output_price = _MODEL_PRICING.get(model, (3.0, 15.0))
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def _execute_tool(block, cwd: str) -> str:
    """Dispatch a tool call to the appropriate handler and return output."""
    if block.name == "bash":
        return _run_bash(block, cwd)
    elif block.name == "str_replace_based_edit_tool":
        return _run_editor(block, cwd)
    else:
        return f"[error: unknown tool: {block.name}]"


def _run_bash(block, cwd: str) -> str:
    """Execute a bash command and return its combined stdout/stderr output."""
    bash_timeout = get_settings().claude_bash_timeout_s
    cmd = block.input.get("command", "")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=bash_timeout,
        )
        output = result.stdout or ""
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return f"[error: command timed out after {bash_timeout}s]"
    except Exception as e:
        return f"[error: {e}]"


def _run_editor(block, cwd: str) -> str:
    """Execute a str_replace_based_edit_tool command and return a status string."""
    command = block.input.get("command", "view")
    path = block.input.get("path", "")
    full_path = Path(cwd) / path if path and not Path(path).is_absolute() else Path(path)

    if command == "view":
        return _editor_view(full_path)
    elif command == "create":
        return _editor_create(full_path, path, block.input.get("file_text", ""))
    elif command == "str_replace":
        return _editor_str_replace(
            full_path, path,
            block.input.get("old_str", ""),
            block.input.get("new_str", ""),
        )
    elif command == "undo_edit":
        return "[error: undo_edit not supported]"
    else:
        return f"[error: unknown text_editor command: {command}]"


def _editor_view(full_path: Path) -> str:
    try:
        if full_path.is_dir():
            entries = sorted(full_path.iterdir())
            lines = [str(e.relative_to(full_path)) + ("/" if e.is_dir() else "")
                     for e in entries]
            return "\n".join(lines) or "(empty directory)"
        return full_path.read_text()
    except Exception as e:
        return f"[error: {e}]"


def _editor_create(full_path: Path, display_path: str, file_text: str) -> str:
    try:
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(file_text)
        return f"File created: {display_path}"
    except Exception as e:
        return f"[error: {e}]"


def _editor_str_replace(full_path: Path, display_path: str, old_str: str, new_str: str) -> str:
    try:
        content = full_path.read_text()
        if old_str not in content:
            return f"[error: old_str not found in {display_path}]"
        full_path.write_text(content.replace(old_str, new_str, 1))
        return f"Replacement done in {display_path}"
    except Exception as e:
        return f"[error: {e}]"



def _serialize_messages(messages: list) -> list:
    """Serialize messages for JSON persistence.

    Assistant messages contain Anthropic SDK pydantic content blocks
    that need .model_dump(). User messages are already plain dicts.
    """
    serialized = []
    for msg in messages:
        if msg["role"] == "assistant" and isinstance(msg["content"], list):
            dumped = [
                block.model_dump() if hasattr(block, "model_dump") else block
                for block in msg["content"]
            ]
            serialized.append({"role": "assistant", "content": dumped})
        else:
            serialized.append(msg)
    return serialized


def _save_conversation(conversation_path: Path, messages: list) -> None:
    """Save conversation messages to a JSON file."""
    try:
        conversation_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = _serialize_messages(messages)
        conversation_path.write_text(json.dumps(serialized, default=str))
        log.info("Saved conversation (%d messages) to %s", len(messages), conversation_path)
    except Exception as e:
        log.warning("Failed to save conversation: %s", e)


def _load_conversation(conversation_path: Path) -> list | None:
    """Load conversation messages from a JSON file. Returns None if not found."""
    try:
        if conversation_path.exists():
            messages = json.loads(conversation_path.read_text())
            log.info("Loaded conversation (%d messages) from %s", len(messages), conversation_path)
            return messages
    except Exception as e:
        log.warning("Failed to load conversation: %s", e)
    return None


def _build_usage_limit_error(
    message: str,
    *,
    cause: Exception,
    model: str,
    input_tokens: int,
    output_tokens: int,
    repo_path: str,
    branch_name: str | None,
    conversation_path: "Path | None",
    messages: list,
    span,
) -> UsageLimitError:
    """Commit WIP, save conversation, and return a UsageLimitError with partial cost."""
    if branch_name:
        commit_wip(repo_path, branch_name)
    if conversation_path:
        _save_conversation(conversation_path, messages)
    partial_cost_eur = _calculate_cost_usd(model, input_tokens, output_tokens) * _EUR_PER_USD
    exc = UsageLimitError(message, cost_eur=partial_cost_eur)
    span.set_attribute("claude.usage_limit", True)
    span.record_exception(exc)
    return exc


def _load_or_start_conversation(
    prompt: str,
    conversation_path: "Path | None",
    span,
) -> list:
    """Return the message list to start the tool loop with.

    If a saved conversation exists at conversation_path, resume from it by
    appending a continuation message. Otherwise start fresh with the prompt.
    """
    if conversation_path:
        saved = _load_conversation(conversation_path)
        if saved:
            saved.append({"role": "user", "content":
                "You were interrupted by a rate limit. Your previous edits have been "
                "preserved on the branch. Continue where you left off."})
            span.set_attribute("claude.resumed", True)
            span.set_attribute("claude.resumed_messages", len(saved))
            log.info("Resuming conversation with %d existing messages", len(saved))
            return saved

    span.set_attribute("claude.resumed", False)
    return [{"role": "user", "content": prompt}]


def _run_tool_loop(
    *,
    client,
    model: str,
    max_tokens: int,
    identity: str,
    messages: list,
    deadline: float,
    repo_path: str,
    span,
    timeout_s: int,
    token_counter: dict,
) -> str:
    """Run the Claude API tool-use loop until end_turn, timeout, or exception.

    token_counter is a mutable {"input": int, "output": int} dict that is
    updated after every API call so partial counts are available if an
    exception propagates out of this function.

    Returns the final output text. Raises TimeoutError if the deadline is exceeded.
    """
    tools = [
        {"type": "bash_20250124", "name": "bash"},
        {"type": "text_editor_20250728", "name": "str_replace_based_edit_tool"},
    ]
    turn_count = 0
    output = ""

    while time.monotonic() < deadline:
        response = client.beta.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=identity,
            tools=tools,
            messages=messages,
            betas=["computer-use-2024-10-22"],
        )
        turn_count += 1
        token_counter["input"] += response.usage.input_tokens
        token_counter["output"] += response.usage.output_tokens
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            output = "".join(b.text for b in response.content if hasattr(b, "text"))
            _set_ratelimit_attributes(span, response)
            break

        tool_results = _execute_all_tools(response.content, repo_path)
        if not tool_results:
            log.warning("No tool calls and stop_reason=%s — breaking loop", response.stop_reason)
            break
        messages.append({"role": "user", "content": tool_results})
    else:
        span.set_attribute("claude.timeout", True)
        exc = TimeoutError(f"Claude tool loop exceeded {timeout_s}s")
        span.record_exception(exc)
        raise exc

    span.set_attribute("claude.turns", turn_count)
    return output


def _execute_all_tools(content: list, repo_path: str) -> list:
    """Execute every tool_use block in a response and return tool_result messages."""
    results = []
    for block in content:
        if block.type == "tool_use":
            output = _execute_tool(block, cwd=repo_path)
            log.info("Tool %s executed (output: %d chars)", block.name, len(output))
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })
    return results


def invoke_claude(
    prompt: str,
    repo_path: str,
    *,
    branch_name: str | None = None,
    conversation_path: Path | None = None,
) -> InvocationResult:
    """Invoke the Claude API with the given prompt.

    Uses tool-use mode (bash + text_editor) so Claude can explore and
    modify the repository.

    Args:
        prompt: The user prompt to send to Claude.
        repo_path: Path to the repository (used as cwd for tool execution).
        branch_name: If provided, WIP changes are committed to this branch
            on rate limit interruption.
        conversation_path: If provided, conversation is saved to this path
            on interruption and resumed from it on next invocation.

    Returns:
        InvocationResult with the output text and cost information.

    Raises:
        UsageLimitError: When the Claude API reports a rate/usage limit.
            The exception carries ``cost_eur`` for partial cost accumulation.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.invoke_claude") as span:
        settings = get_settings()
        model = settings.claude_model
        tool_loop_timeout_s = settings.claude_tool_loop_timeout_s
        max_tokens = settings.claude_max_tokens
        identity = (APP_DIR / "CLAUDE.md").read_text()
        client = _get_client()

        span.set_attribute("claude.model", model)

        token_counter = {"input": 0, "output": 0}

        try:
            messages = _load_or_start_conversation(prompt, conversation_path, span)
            deadline = time.monotonic() + tool_loop_timeout_s

            output = _run_tool_loop(
                client=client,
                model=model,
                max_tokens=max_tokens,
                identity=identity,
                messages=messages,
                deadline=deadline,
                repo_path=repo_path,
                span=span,
                timeout_s=tool_loop_timeout_s,
                token_counter=token_counter,
            )

        except anthropic.APIConnectionError as e:
            log.error("Claude API connection error: %s", e)
            raise _build_usage_limit_error(
                f"Claude API connection error: {e}",
                cause=e,
                model=model,
                input_tokens=token_counter["input"],
                output_tokens=token_counter["output"],
                repo_path=repo_path,
                branch_name=branch_name,
                conversation_path=conversation_path,
                messages=messages,
                span=span,
            ) from e

        except anthropic.RateLimitError as e:
            log.error("Claude API rate limit hit: %s", e)
            raise _build_usage_limit_error(
                f"Claude API rate limit: {e}",
                cause=e,
                model=model,
                input_tokens=token_counter["input"],
                output_tokens=token_counter["output"],
                repo_path=repo_path,
                branch_name=branch_name,
                conversation_path=conversation_path,
                messages=messages,
                span=span,
            ) from e

        except anthropic.APIStatusError as e:
            if e.status_code == 529:
                log.error("Claude API overloaded (529): %s", e)
                raise _build_usage_limit_error(
                    f"Claude API overloaded: {e}",
                    cause=e,
                    model=model,
                    input_tokens=token_counter["input"],
                    output_tokens=token_counter["output"],
                    repo_path=repo_path,
                    branch_name=branch_name,
                    conversation_path=conversation_path,
                    messages=messages,
                    span=span,
                ) from e
            log.error("Claude API error %d: %s", e.status_code, e)
            span.set_attribute("claude.api_error", e.status_code)
            raise

        # Record usage metrics
        total_input_tokens = token_counter["input"]
        total_output_tokens = token_counter["output"]
        cost_usd = _calculate_cost_usd(model, total_input_tokens, total_output_tokens)
        cost_eur = cost_usd * _EUR_PER_USD
        span.set_attribute("claude.input_tokens", total_input_tokens)
        span.set_attribute("claude.output_tokens", total_output_tokens)
        span.set_attribute("claude.cost_usd", cost_usd)
        span.set_attribute("claude.cost_eur", cost_eur)
        span.set_attribute("claude.output_length", len(output))
        span.set_attribute("claude.timeout", False)
        span.set_attribute("claude.usage_limit", False)

        return InvocationResult(
            output=output,
            cost_eur=cost_eur,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
        )


def _set_ratelimit_attributes(span, response) -> None:
    """Extract rate-limit headers from the response and set them as span attributes."""
    try:
        headers = response._raw_response.headers
        remaining_requests = int(headers.get("anthropic-ratelimit-requests-remaining", -1))
        remaining_tokens = int(headers.get("anthropic-ratelimit-tokens-remaining", -1))
        span.set_attribute("claude.ratelimit_requests_remaining", remaining_requests)
        span.set_attribute("claude.ratelimit_tokens_remaining", remaining_tokens)
    except Exception:
        # Non-fatal — rate-limit headers are best-effort
        pass


def is_claude_available() -> bool:
    """Return True if Claude is available (rate limit not currently hit).

    Makes a minimal API call. Returns False on RateLimitError; returns True on
    success or any other exception (fail-open to avoid suppressing real work on
    spurious pre-check errors).
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.claude_available_check") as span:
        try:
            client = _get_client()
            settings = get_settings()
            client.messages.create(
                model=settings.claude_model,
                max_tokens=5,
                messages=[{"role": "user", "content": "respond with: OK"}],
            )
            span.set_attribute("claude.available", True)
            return True
        except anthropic.RateLimitError as e:
            log.warning("Claude availability check: rate limit hit — %s", e)
            span.set_attribute("claude.available", False)
            return False
        except anthropic.AuthenticationError as exc:
            log.error("Claude availability check: authentication failed — %s", exc)
            span.set_attribute("claude.available", False)
            span.set_attribute("claude.check_error", str(exc))
            return False
        except Exception as exc:
            log.warning("Claude availability pre-check raised %s — assuming available", exc)
            span.set_attribute("claude.available", True)
            span.set_attribute("claude.check_error", str(exc))
            return True
