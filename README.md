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

Clayde runs as a cron job every 5 minutes, driven by a state machine persisted in `state.json`.

---

## State Machine

Each issue moves through the following states:

```
(new issue) ──► planning ──► awaiting_approval ──► implementing ──► done
                                                                  ↘ failed
```

| State | Description |
|---|---|
| `planning` | Claude CLI is being invoked to research and write a plan |
| `awaiting_approval` | Plan posted as comment; waiting for 👍 from an approver |
| `implementing` | Claude CLI is being invoked to implement the solution |
| `done` | PR opened; issue complete |
| `failed` | Error occurred; requires manual reset to retry |
| `interrupted` | Claude hit a usage/rate limit; retried automatically next cron tick |

---

## Safety Gates

Two independent checks must pass before any work begins:

**1. Issue-level gate** (before planning)
The issue must be created by a whitelisted user, or have a 👍 reaction from a whitelisted user on the issue itself.

**2. Plan approval gate** (before implementation)
The plan comment must have a 👍 reaction from the designated approver (`max-tet`), and the issue itself must also have a 👍 from a whitelisted user.

Whitelisted users: `max-tet`, `ClaydeCode`

This two-tier system ensures Clayde only acts on trusted issues and only implements plans that have been explicitly reviewed and approved.

---

## Capabilities

- **Multi-repo support**: Clones and works on any GitHub repository it has access to
- **Full issue lifecycle**: Plan → approval → implement → PR, with comments at each stage
- **Rate-limit resilience**: Detects Claude usage limits and automatically retries
- **Safety gates**: Whitelist + approval checks prevent unauthorized work

---

## Tech Stack

| Component | Tool |
|---|---|
| Language | Python 3.12 |
| Package manager | `uv` |
| LLM | Claude (via Claude Code CLI) |
| GitHub API | `gh` CLI |
| Scheduling | cron (every 5 min) |
| State persistence | `state.json` |

---

## Configuration

`config.env` (plain `KEY=VALUE`):

| Key | Purpose                               |
|---|---------------------------------------|
| `GITHUB_TOKEN` | Classic Token (full repo permissions) |
| `GITHUB_USERNAME` | `ClaydeCode`                          |
| `CLAYDE_ENABLED` | Set to `true` to activate             |
