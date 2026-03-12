<p align="center">
  <img src="clayde.jpg" width="400" alt="Clayde" />
</p>

# Clayde

Clayde is a persistent autonomous AI software agent that lives on a dedicated VM and works GitHub issues end-to-end — researching codebases, writing plans, implementing solutions, and opening pull requests.

---

## What is Clayde?

Clayde is assigned GitHub issues in software repositories. For each issue it:

1. Researches the codebase to understand the context
2. Writes an implementation plan and posts it as a GitHub comment
3. Waits for human approval (a 👍 reaction)
4. Implements the solution on a new branch
5. Opens a pull request and posts a summary comment

Clayde runs as a Docker container in a continuous loop (default: every 5 minutes), driven by a state machine persisted in `data/state.json`.

---

## State Machine

Each issue moves through the following states:

```
(new issue) ──► planning ──► awaiting_approval ──► implementing ──► done
                                                                  ↘ failed
```

| State | Description |
|---|---|
| `planning` | Claude is researching and writing a plan |
| `awaiting_approval` | Plan posted as comment; waiting for 👍 from an approver |
| `implementing` | Claude is implementing the solution |
| `done` | PR opened; issue complete |
| `failed` | Error occurred; requires manual reset to retry |
| `interrupted` | Claude hit a usage/rate limit; retried automatically next cycle |

---

## Safety Gates

Two independent checks must pass before any work begins:

**1. Issue-level gate** (before planning)
The issue must be created by a whitelisted user, or have a 👍 reaction from a whitelisted user on the issue itself.

**2. Plan approval gate** (before implementation)
The plan comment must have a 👍 reaction from any whitelisted user.

Whitelisted users are configured via `CLAYDE_WHITELISTED_USERS` in `data/config.env`.

This two-tier system ensures Clayde only acts on trusted issues and only implements plans that have been explicitly reviewed and approved.

---

## Capabilities

- **Multi-repo support**: Clones and works on any GitHub repository it has access to
- **Full issue lifecycle**: Plan → approval → implement → PR, with comments at each stage
- **Rate-limit resilience**: Detects Claude usage limits and automatically retries
- **Safety gates**: Whitelist + approval checks prevent unauthorized work
- **Observability**: OpenTelemetry tracing with JSONL file export and optional OTLP export
- **Tool-use loop**: Claude can execute bash commands and edit files in the target repo via the Anthropic SDK

---

## Tech Stack

| Component | Tool |
|---|---|
| Language | Python 3.13 |
| Package manager | `uv` |
| LLM | Claude (via Anthropic Python SDK) |
| GitHub API | PyGitHub |
| Deployment | Docker (continuous loop) |
| Configuration | pydantic-settings |
| Templating | Jinja2 |
| Observability | OpenTelemetry |
| State persistence | `state.json` |

---

## Configuration

`data/config.env` (plain `KEY=VALUE`, all prefixed with `CLAYDE_`):

| Key | Purpose |
|---|---|
| `CLAYDE_GITHUB_TOKEN` | Fine-grained PAT (Issues R/W, PRs R/W, Contents R/W) |
| `CLAYDE_GITHUB_USERNAME` | `ClaydeCode` |
| `CLAYDE_ENABLED` | Set to `true` to activate |
| `CLAYDE_WHITELISTED_USERS` | Comma-separated trusted GitHub usernames |
| `CLAYDE_CLAUDE_API_KEY` | Anthropic API key |
| `CLAYDE_CLAUDE_MODEL` | Model to use (default: `claude-sonnet-4-6`) |
