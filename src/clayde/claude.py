"""Claude invocation via the Anthropic API or the Claude Code CLI."""

import dataclasses
import json
import logging
import os
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
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

# Text patterns that indicate a usage/rate limit in CLI output.
_LIMIT_PATTERNS = [
    "usage limit",
    "rate limit",
    "session limit",
    "claude code pro",
    "you've reached",
    "exceeded your",
    "hit your limit",
    "resets at",
]


@dataclasses.dataclass
class InvocationResult:
    """Result of a Claude invocation, including output text and cost."""

    output: str
    cost_eur: float
    input_tokens: int
    output_tokens: int


class UsageLimitError(Exception):
    """Raised when Claude reports a usage/rate limit."""

    def __init__(self, message: str, cost_eur: float = 0.0):
        super().__init__(message)
        self.cost_eur = cost_eur


def format_cost_line(cost_eur: float) -> str:
    """Format a cost line for inclusion in GitHub comments."""
    return f"\n\n💸 This task cost {cost_eur:.2f}€"


def _calculate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for the given model and token counts."""
    input_price, output_price = _MODEL_PRICING.get(model, (3.0, 15.0))
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


# ---------------------------------------------------------------------------
# Backend abstraction
# ---------------------------------------------------------------------------


class ClaudeBackend(ABC):
    """Abstract base for Claude invocation backends."""

    @abstractmethod
    def invoke(
        self,
        prompt: str,
        repo_path: str,
        *,
        branch_name: str | None = None,
        conversation_path: Path | None = None,
    ) -> InvocationResult: ...

    @abstractmethod
    def is_available(self) -> bool: ...


# ---------------------------------------------------------------------------
# API backend (Anthropic SDK)
# ---------------------------------------------------------------------------


class ApiBackend(ClaudeBackend):
    """Invokes Claude via the Anthropic Python SDK with a tool-use loop."""

    def _get_client(self) -> anthropic.Anthropic:
        settings = get_settings()
        return anthropic.Anthropic(api_key=settings.claude_api_key)

    @staticmethod
    def _execute_tool(block, cwd: str) -> str:
        if block.name == "bash":
            return ApiBackend._run_bash(block, cwd)
        elif block.name == "str_replace_based_edit_tool":
            return ApiBackend._run_editor(block, cwd)
        else:
            return f"[error: unknown tool: {block.name}]"

    @staticmethod
    def _run_bash(block, cwd: str) -> str:
        bash_timeout = get_settings().claude_bash_timeout_s
        cmd = block.input.get("command", "")
        try:
            result = subprocess.run(
                cmd, shell=True, cwd=cwd, text=True, capture_output=True,
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

    @staticmethod
    def _run_editor(block, cwd: str) -> str:
        command = block.input.get("command", "view")
        path = block.input.get("path", "")
        full_path = Path(cwd) / path if path and not Path(path).is_absolute() else Path(path)
        if command == "view":
            return ApiBackend._editor_view(full_path)
        elif command == "create":
            return ApiBackend._editor_create(full_path, path, block.input.get("file_text", ""))
        elif command == "str_replace":
            return ApiBackend._editor_str_replace(
                full_path, path, block.input.get("old_str", ""), block.input.get("new_str", ""),
            )
        elif command == "undo_edit":
            return "[error: undo_edit not supported]"
        else:
            return f"[error: unknown text_editor command: {command}]"

    @staticmethod
    def _editor_view(full_path: Path) -> str:
        try:
            if full_path.is_dir():
                entries = sorted(full_path.iterdir())
                lines = [str(e.relative_to(full_path)) + ("/" if e.is_dir() else "") for e in entries]
                return "\n".join(lines) or "(empty directory)"
            return full_path.read_text()
        except Exception as e:
            return f"[error: {e}]"

    @staticmethod
    def _editor_create(full_path: Path, display_path: str, file_text: str) -> str:
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(file_text)
            return f"File created: {display_path}"
        except Exception as e:
            return f"[error: {e}]"

    @staticmethod
    def _editor_str_replace(full_path: Path, display_path: str, old_str: str, new_str: str) -> str:
        try:
            content = full_path.read_text()
            if old_str not in content:
                return f"[error: old_str not found in {display_path}]"
            full_path.write_text(content.replace(old_str, new_str, 1))
            return f"Replacement done in {display_path}"
        except Exception as e:
            return f"[error: {e}]"

    # -- conversation persistence --

    @staticmethod
    def _serialize_messages(messages: list) -> list:
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

    @staticmethod
    def _save_conversation(conversation_path: Path, messages: list) -> None:
        try:
            conversation_path.parent.mkdir(parents=True, exist_ok=True)
            serialized = ApiBackend._serialize_messages(messages)
            conversation_path.write_text(json.dumps(serialized, default=str))
            log.info("Saved conversation (%d messages) to %s", len(messages), conversation_path)
        except Exception as e:
            log.warning("Failed to save conversation: %s", e)

    @staticmethod
    def _load_conversation(conversation_path: Path) -> list | None:
        try:
            if conversation_path.exists():
                messages = json.loads(conversation_path.read_text())
                log.info("Loaded conversation (%d messages) from %s", len(messages), conversation_path)
                return messages
        except Exception as e:
            log.warning("Failed to load conversation: %s", e)
        return None

    def _build_usage_limit_error(
        self, message, *, cause, model, input_tokens, output_tokens,
        repo_path, branch_name, conversation_path, messages, span,
    ) -> UsageLimitError:
        if branch_name:
            commit_wip(repo_path, branch_name)
        if conversation_path:
            self._save_conversation(conversation_path, messages)
        partial_cost_eur = _calculate_cost_usd(model, input_tokens, output_tokens) * _EUR_PER_USD
        exc = UsageLimitError(message, cost_eur=partial_cost_eur)
        span.set_attribute("claude.usage_limit", True)
        span.record_exception(exc)
        return exc

    def _load_or_start_conversation(self, prompt, conversation_path, span) -> list:
        if conversation_path:
            saved = self._load_conversation(conversation_path)
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
        self, *, client, model, max_tokens, identity, messages, deadline,
        repo_path, span, timeout_s, token_counter,
    ) -> str:
        tools = [
            {"type": "bash_20250124", "name": "bash"},
            {"type": "text_editor_20250728", "name": "str_replace_based_edit_tool"},
        ]
        turn_count = 0
        output = ""
        while time.monotonic() < deadline:
            response = client.beta.messages.create(
                model=model, max_tokens=max_tokens, system=identity,
                tools=tools, messages=messages, betas=["computer-use-2024-10-22"],
            )
            turn_count += 1
            token_counter["input"] += response.usage.input_tokens
            token_counter["output"] += response.usage.output_tokens
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "end_turn":
                output = "".join(b.text for b in response.content if hasattr(b, "text"))
                self._set_ratelimit_attributes(span, response)
                break
            tool_results = self._execute_all_tools(response.content, repo_path)
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

    def _execute_all_tools(self, content: list, repo_path: str) -> list:
        results = []
        for block in content:
            if block.type == "tool_use":
                output = self._execute_tool(block, cwd=repo_path)
                log.info("Tool %s executed (output: %d chars)", block.name, len(output))
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        return results

    @staticmethod
    def _set_ratelimit_attributes(span, response) -> None:
        try:
            headers = response._raw_response.headers
            remaining_requests = int(headers.get("anthropic-ratelimit-requests-remaining", -1))
            remaining_tokens = int(headers.get("anthropic-ratelimit-tokens-remaining", -1))
            span.set_attribute("claude.ratelimit_requests_remaining", remaining_requests)
            span.set_attribute("claude.ratelimit_tokens_remaining", remaining_tokens)
        except Exception:
            pass

    # -- public interface --

    def invoke(
        self, prompt, repo_path, *, branch_name=None, conversation_path=None,
    ) -> InvocationResult:
        tracer = get_tracer()
        with tracer.start_as_current_span("clayde.invoke_claude") as span:
            settings = get_settings()
            model = settings.claude_model
            tool_loop_timeout_s = settings.claude_tool_loop_timeout_s
            max_tokens = settings.claude_max_tokens
            identity = (APP_DIR / "CLAUDE.md").read_text()
            client = self._get_client()
            span.set_attribute("claude.model", model)
            span.set_attribute("claude.backend", "api")
            token_counter = {"input": 0, "output": 0}

            try:
                messages = self._load_or_start_conversation(prompt, conversation_path, span)
                deadline = time.monotonic() + tool_loop_timeout_s
                output = self._run_tool_loop(
                    client=client, model=model, max_tokens=max_tokens,
                    identity=identity, messages=messages, deadline=deadline,
                    repo_path=repo_path, span=span, timeout_s=tool_loop_timeout_s,
                    token_counter=token_counter,
                )
            except anthropic.APIConnectionError as e:
                log.error("Claude API connection error: %s", e)
                raise self._build_usage_limit_error(
                    f"Claude API connection error: {e}", cause=e, model=model,
                    input_tokens=token_counter["input"], output_tokens=token_counter["output"],
                    repo_path=repo_path, branch_name=branch_name,
                    conversation_path=conversation_path, messages=messages, span=span,
                ) from e
            except anthropic.RateLimitError as e:
                log.error("Claude API rate limit hit: %s", e)
                raise self._build_usage_limit_error(
                    f"Claude API rate limit: {e}", cause=e, model=model,
                    input_tokens=token_counter["input"], output_tokens=token_counter["output"],
                    repo_path=repo_path, branch_name=branch_name,
                    conversation_path=conversation_path, messages=messages, span=span,
                ) from e
            except anthropic.APIStatusError as e:
                if e.status_code == 529:
                    log.error("Claude API overloaded (529): %s", e)
                    raise self._build_usage_limit_error(
                        f"Claude API overloaded: {e}", cause=e, model=model,
                        input_tokens=token_counter["input"], output_tokens=token_counter["output"],
                        repo_path=repo_path, branch_name=branch_name,
                        conversation_path=conversation_path, messages=messages, span=span,
                    ) from e
                log.error("Claude API error %d: %s", e.status_code, e)
                span.set_attribute("claude.api_error", e.status_code)
                raise

            total_input = token_counter["input"]
            total_output = token_counter["output"]
            cost_usd = _calculate_cost_usd(model, total_input, total_output)
            cost_eur = cost_usd * _EUR_PER_USD
            span.set_attribute("claude.input_tokens", total_input)
            span.set_attribute("claude.output_tokens", total_output)
            span.set_attribute("claude.cost_usd", cost_usd)
            span.set_attribute("claude.cost_eur", cost_eur)
            span.set_attribute("claude.output_length", len(output))
            span.set_attribute("claude.timeout", False)
            span.set_attribute("claude.usage_limit", False)
            return InvocationResult(
                output=output, cost_eur=cost_eur,
                input_tokens=total_input, output_tokens=total_output,
            )

    def is_available(self) -> bool:
        tracer = get_tracer()
        with tracer.start_as_current_span("clayde.claude_available_check") as span:
            try:
                client = self._get_client()
                settings = get_settings()
                client.messages.create(
                    model=settings.claude_model, max_tokens=5,
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


# ---------------------------------------------------------------------------
# CLI backend (Claude Code subprocess)
# ---------------------------------------------------------------------------


def _is_limit_error(text: str) -> bool:
    """Return True if text contains a usage/rate limit pattern."""
    t = text.lower()
    return any(p in t for p in _LIMIT_PATTERNS)


def _make_cli_env() -> dict[str, str]:
    """Build an environment dict for CLI subprocess calls."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    return env


def _resolve_cli_bin() -> str:
    """Return the path to the Claude CLI binary."""
    path = shutil.which("claude")
    if path:
        return path
    return str(Path.home() / ".local" / "bin" / "claude")


class CliBackend(ClaudeBackend):
    """Invokes Claude via the Claude Code CLI subprocess."""

    @staticmethod
    def _save_session_id(conversation_path: Path, session_id: str) -> None:
        """Persist a CLI session ID for later resumption."""
        try:
            conversation_path.parent.mkdir(parents=True, exist_ok=True)
            conversation_path.write_text(json.dumps({"session_id": session_id}))
            log.info("Saved CLI session %s to %s", session_id, conversation_path)
        except Exception as e:
            log.warning("Failed to save CLI session ID: %s", e)

    @staticmethod
    def _load_session_id(conversation_path: Path) -> str | None:
        """Load a previously saved CLI session ID. Returns None if not found."""
        try:
            if conversation_path.exists():
                data = json.loads(conversation_path.read_text())
                session_id = data.get("session_id")
                if session_id:
                    log.info("Loaded CLI session %s from %s", session_id, conversation_path)
                    return session_id
        except Exception as e:
            log.warning("Failed to load CLI session ID: %s", e)
        return None

    def invoke(
        self, prompt, repo_path, *, branch_name=None, conversation_path=None,
    ) -> InvocationResult:
        tracer = get_tracer()
        with tracer.start_as_current_span("clayde.invoke_claude") as span:
            settings = get_settings()
            timeout_s = settings.claude_tool_loop_timeout_s
            identity = (APP_DIR / "CLAUDE.md").read_text()
            cli_bin = _resolve_cli_bin()

            span.set_attribute("claude.backend", "cli")
            span.set_attribute("claude.cli_bin", cli_bin)

            cmd = [
                cli_bin, "-p", prompt,
                "--append-system-prompt", identity,
                "--output-format", "json",
                "--dangerously-skip-permissions",
            ]

            # Resume from a previous session if available
            if conversation_path:
                session_id = self._load_session_id(conversation_path)
                if session_id:
                    cmd.extend(["--resume", session_id])
                    span.set_attribute("claude.resumed", True)
                    span.set_attribute("claude.resumed_session_id", session_id)
                    log.info("Resuming CLI session %s", session_id)
                else:
                    span.set_attribute("claude.resumed", False)

            try:
                result = subprocess.run(
                    cmd, cwd=repo_path, env=_make_cli_env(),
                    text=True, capture_output=True, timeout=timeout_s,
                )
            except subprocess.TimeoutExpired:
                log.error("Claude CLI timed out after %ds", timeout_s)
                if branch_name:
                    commit_wip(repo_path, branch_name)
                span.set_attribute("claude.timeout", True)
                exc = UsageLimitError(f"Claude CLI timed out after {timeout_s}s")
                span.record_exception(exc)
                raise exc

            span.set_attribute("claude.timeout", False)
            span.set_attribute("claude.exit_code", result.returncode)

            combined = (result.stdout or "") + (result.stderr or "")

            # Parse JSON output
            output_text = ""
            session_id = None
            try:
                parsed = json.loads(result.stdout)
                output_text = parsed.get("result", "")
                session_id = parsed.get("session_id")
            except (json.JSONDecodeError, TypeError):
                # Fall back to raw stdout if JSON parsing fails
                output_text = result.stdout or ""

            # Save session ID for potential resumption
            if conversation_path and session_id:
                self._save_session_id(conversation_path, session_id)

            # Check for usage/rate limits
            if _is_limit_error(combined):
                log.error("Claude CLI usage limit detected")
                if branch_name:
                    commit_wip(repo_path, branch_name)
                span.set_attribute("claude.usage_limit", True)
                exc = UsageLimitError("Claude CLI usage limit hit")
                span.record_exception(exc)
                raise exc

            if result.returncode != 0:
                log.error("Claude CLI exited with code %d", result.returncode)
                if result.stderr:
                    log.error("stderr: %s", result.stderr[:500])

            span.set_attribute("claude.usage_limit", False)
            span.set_attribute("claude.output_length", len(output_text))

            return InvocationResult(
                output=output_text, cost_eur=0.0,
                input_tokens=0, output_tokens=0,
            )

    def is_available(self) -> bool:
        tracer = get_tracer()
        with tracer.start_as_current_span("clayde.claude_available_check") as span:
            cli_bin = _resolve_cli_bin()
            try:
                result = subprocess.run(
                    [cli_bin, "-p", "respond with: OK",
                     "--output-format", "json",
                     "--dangerously-skip-permissions",
                     "--no-session-persistence"],
                    env=_make_cli_env(), text=True, capture_output=True, timeout=60,
                )
                combined = (result.stdout or "") + (result.stderr or "")
                if _is_limit_error(combined):
                    span.set_attribute("claude.available", False)
                    return False
                span.set_attribute("claude.available", True)
                return True
            except Exception as exc:
                log.warning("Claude CLI availability pre-check raised %s — assuming available", exc)
                span.set_attribute("claude.available", True)
                span.set_attribute("claude.check_error", str(exc))
                return True


# ---------------------------------------------------------------------------
# Backend dispatch
# ---------------------------------------------------------------------------


def _get_backend() -> ClaudeBackend:
    """Return the configured Claude backend instance."""
    settings = get_settings()
    if settings.claude_backend == "cli":
        return CliBackend()
    return ApiBackend()


# ---------------------------------------------------------------------------
# Public module-level functions (unchanged signatures for callers)
# ---------------------------------------------------------------------------


def invoke_claude(
    prompt: str,
    repo_path: str,
    *,
    branch_name: str | None = None,
    conversation_path: Path | None = None,
) -> InvocationResult:
    """Invoke Claude with the given prompt.

    Uses tool-use mode (bash + text_editor) so Claude can explore and
    modify the repository.

    The backend (API or CLI) is selected by the ``claude_backend`` setting.

    Args:
        prompt: The user prompt to send to Claude.
        repo_path: Path to the repository (used as cwd for tool execution).
        branch_name: If provided, WIP changes are committed to this branch
            on rate limit interruption.
        conversation_path: If provided, conversation state is saved to this
            path on interruption and resumed from it on next invocation.

    Returns:
        InvocationResult with the output text and cost information.

    Raises:
        UsageLimitError: When Claude reports a rate/usage limit.
            The exception carries ``cost_eur`` for partial cost accumulation.
    """
    return _get_backend().invoke(
        prompt, repo_path,
        branch_name=branch_name, conversation_path=conversation_path,
    )


def is_claude_available() -> bool:
    """Return True if Claude is available (rate limit not currently hit).

    Makes a minimal invocation. Returns False when a limit is detected;
    returns True on success or any other error (fail-open to avoid
    suppressing real work on spurious pre-check errors).
    """
    return _get_backend().is_available()
