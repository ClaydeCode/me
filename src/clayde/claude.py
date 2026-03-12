"""Claude API invocation via the Anthropic Python SDK."""

import logging
import subprocess
import time
from pathlib import Path

import anthropic

from clayde.config import APP_DIR, get_settings
from clayde.telemetry import get_tracer

log = logging.getLogger("clayde.claude")

# Pricing in USD per 1M tokens (input, output) for known models.
# Update these periodically as pricing changes.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-5": (15.0, 75.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (0.8, 4.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-3-5-sonnet-20241022": (3.0, 15.0),
    "claude-3-5-haiku-20241022": (0.8, 4.0),
    "claude-3-opus-20240229": (15.0, 75.0),
}

# EUR/USD conversion rate — update periodically.
_EUR_PER_USD = 0.92


class UsageLimitError(Exception):
    """Raised when Claude API reports a usage/rate limit."""


def _get_client() -> anthropic.Anthropic:
    """Return an Anthropic client configured with the API key from settings."""
    settings = get_settings()
    return anthropic.Anthropic(api_key=settings.claude_api_key)


def _calculate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for the given model and token counts."""
    input_price, output_price = _MODEL_PRICING.get(model, (3.0, 15.0))
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def _execute_tool(block, cwd: str) -> str:
    """Execute a bash or text_editor tool call locally and return output."""
    if block.name == "bash":
        cmd = block.input.get("command", "")
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=300,
            )
            output = result.stdout or ""
            if result.stderr:
                output += "\n[stderr]\n" + result.stderr
            return output or "(no output)"
        except subprocess.TimeoutExpired:
            return "[error: command timed out after 300s]"
        except Exception as e:
            return f"[error: {e}]"

    elif block.name == "str_replace_based_edit_tool":
        command = block.input.get("command", "view")
        path = block.input.get("path", "")
        full_path = Path(cwd) / path if path and not Path(path).is_absolute() else Path(path)

        if command == "view":
            try:
                if full_path.is_dir():
                    entries = sorted(full_path.iterdir())
                    lines = [str(e.relative_to(full_path)) + ("/" if e.is_dir() else "")
                             for e in entries]
                    return "\n".join(lines) or "(empty directory)"
                else:
                    return full_path.read_text()
            except Exception as e:
                return f"[error: {e}]"

        elif command == "create":
            file_text = block.input.get("file_text", "")
            try:
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(file_text)
                return f"File created: {path}"
            except Exception as e:
                return f"[error: {e}]"

        elif command == "str_replace":
            old_str = block.input.get("old_str", "")
            new_str = block.input.get("new_str", "")
            try:
                content = full_path.read_text()
                if old_str not in content:
                    return f"[error: old_str not found in {path}]"
                new_content = content.replace(old_str, new_str, 1)
                full_path.write_text(new_content)
                return f"Replacement done in {path}"
            except Exception as e:
                return f"[error: {e}]"

        elif command == "undo_edit":
            # Not supported in this implementation
            return "[error: undo_edit not supported]"

        else:
            return f"[error: unknown text_editor command: {command}]"

    else:
        return f"[error: unknown tool: {block.name}]"


def invoke_claude(prompt: str, repo_path: str) -> str:
    """Invoke the Claude API with the given prompt.

    Uses tool-use mode (bash + text_editor) so Claude can explore and
    modify the repository.

    Args:
        prompt: The user prompt to send to Claude.
        repo_path: Path to the repository (used as cwd for tool execution).

    Raises:
        UsageLimitError: When the Claude API reports a rate/usage limit.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.invoke_claude") as span:
        settings = get_settings()
        model = settings.claude_model
        identity = (APP_DIR / "CLAUDE.md").read_text()
        client = _get_client()

        span.set_attribute("claude.model", model)

        total_input_tokens = 0
        total_output_tokens = 0

        try:
            tools = [
                {"type": "bash_20250124", "name": "bash"},
                {"type": "text_editor_20250429", "name": "str_replace_based_edit_tool"},
            ]
            messages = [{"role": "user", "content": prompt}]
            deadline = time.monotonic() + 1800
            turn_count = 0
            output = ""

            while time.monotonic() < deadline:
                response = client.beta.messages.create(
                    model=model,
                    max_tokens=8192,
                    system=identity,
                    tools=tools,
                    messages=messages,
                    betas=["computer-use-2024-10-22"],
                )
                turn_count += 1

                # Accumulate token usage across turns
                total_input_tokens += response.usage.input_tokens
                total_output_tokens += response.usage.output_tokens

                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason == "end_turn":
                    output = "".join(
                        b.text for b in response.content if hasattr(b, "text")
                    )
                    _set_ratelimit_attributes(span, response)
                    break

                # Execute tool calls and feed results back
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = _execute_tool(block, cwd=repo_path)
                        log.info("Tool %s executed (output: %d chars)", block.name, len(result))
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                if not tool_results:
                    # No tool calls and stop reason isn't end_turn — break to avoid infinite loop
                    log.warning("No tool calls and stop_reason=%s — breaking loop", response.stop_reason)
                    break

                messages.append({"role": "user", "content": tool_results})
            else:
                span.set_attribute("claude.timeout", True)
                exc = TimeoutError("Claude tool loop exceeded 1800s")
                span.record_exception(exc)
                raise exc

            span.set_attribute("claude.turns", turn_count)

        except anthropic.RateLimitError as e:
            log.error("Claude API rate limit hit: %s", e)
            span.set_attribute("claude.usage_limit", True)
            exc = UsageLimitError(f"Claude API rate limit: {e}")
            span.record_exception(exc)
            raise exc from e

        except anthropic.APIStatusError as e:
            if e.status_code == 529:
                log.error("Claude API overloaded (529): %s", e)
                span.set_attribute("claude.usage_limit", True)
                exc = UsageLimitError(f"Claude API overloaded: {e}")
                span.record_exception(exc)
                raise exc from e
            log.error("Claude API error %d: %s", e.status_code, e)
            span.set_attribute("claude.api_error", e.status_code)
            raise

        # Record usage metrics
        cost_usd = _calculate_cost_usd(model, total_input_tokens, total_output_tokens)
        span.set_attribute("claude.input_tokens", total_input_tokens)
        span.set_attribute("claude.output_tokens", total_output_tokens)
        span.set_attribute("claude.cost_usd", cost_usd)
        span.set_attribute("claude.cost_eur", cost_usd * _EUR_PER_USD)
        span.set_attribute("claude.output_length", len(output))
        span.set_attribute("claude.timeout", False)
        span.set_attribute("claude.usage_limit", False)

        return output


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
