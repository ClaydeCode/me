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
- **Dual Claude backend**: Use the Anthropic API (pay-per-token) or the Claude Code CLI (subscription-based)

---

## Tech Stack

| Component | Tool |
|---|---|
| Language | Python 3.13 |
| Package manager | `uv` |
| LLM | Claude (Anthropic SDK or Claude Code CLI) |
| GitHub API | PyGitHub |
| Deployment | Docker (continuous loop) |
| Configuration | pydantic-settings |
| Templating | Jinja2 |
| Observability | OpenTelemetry |
| State persistence | `state.json` |

---

## Setup

### 1. Create the data directory

```bash
mkdir -p data/logs data/repos
cp config.env.template data/config.env
```

Edit `data/config.env` and fill in the required values (see Configuration below).

### 2. Choose a Claude backend

Clayde supports two backends for invoking Claude, selected by `CLAYDE_CLAUDE_BACKEND` in `data/config.env`:

#### Option A: Anthropic API (`api`, default)

Uses the Anthropic Python SDK with a tool-use loop. Pay-per-token.

1. Get an API key from [console.anthropic.com](https://console.anthropic.com/)
2. Set in `data/config.env`:
   ```
   CLAYDE_CLAUDE_BACKEND=api
   CLAYDE_CLAUDE_API_KEY=sk-ant-...
   ```

#### Option B: Claude Code CLI (`cli`)

Runs the Claude Code CLI as a subprocess. Uses your Claude Pro/Max subscription — no per-token cost.

1. On the host machine, log in to the CLI:
   ```bash
   claude login
   ```
2. Set in `data/config.env`:
   ```
   CLAYDE_CLAUDE_BACKEND=cli
   ```
   (`CLAYDE_CLAUDE_API_KEY` is not required for the CLI backend.)

The `docker-compose.yml` mounts `~/.claude/.credentials.json` from the host directly into the container. Token refreshes, logouts, and account switches on the host are immediately reflected.

### 3. Start the container

```bash
docker compose up -d
```

Clayde will start its loop, checking for assigned issues every 5 minutes (configurable via `CLAYDE_LOOP_INTERVAL_S`).

---

## Configuration

`data/config.env` (plain `KEY=VALUE`, all prefixed with `CLAYDE_`):

| Key | Purpose |
|---|---|
| `CLAYDE_GITHUB_TOKEN` | Classic PAT with full `repo` scope |
| `CLAYDE_GITHUB_USERNAME` | The bot account username |
| `CLAYDE_GIT_NAME` | Git commit author name (defaults to `CLAYDE_GITHUB_USERNAME` if not set) |
| `CLAYDE_GIT_EMAIL` | Git commit author email (required) |
| `CLAYDE_ENABLED` | Set to `true` to activate |
| `CLAYDE_WHITELISTED_USERS` | Comma-separated trusted GitHub usernames |
| `CLAYDE_CLAUDE_BACKEND` | `api` (default) or `cli` |
| `CLAYDE_CLAUDE_API_KEY` | Anthropic API key (required when backend=`api`) |
| `CLAYDE_CLAUDE_MODEL` | Model to use (default: `claude-opus-4-6`) |

---

## Deploying Your Own Instance

Clayde is designed to be deployed by anyone. To run your own instance:

### 1. Create a dedicated bot GitHub account

Create a GitHub account for your bot (e.g. `my-bot`). This is the account that will be assigned issues and open pull requests.

### 2. Create a GitHub Personal Access Token for the bot

From the bot account, create a classic personal access token with the full **`repo`** scope.

### 3. Configure the instance

```bash
mkdir -p data/logs data/repos
cp config.env.template data/config.env
```

Edit `data/config.env`:

```
CLAYDE_GITHUB_TOKEN=github_pat_...
CLAYDE_GITHUB_USERNAME=my-bot
CLAYDE_GIT_EMAIL=my-bot@example.com
CLAYDE_ENABLED=true
CLAYDE_WHITELISTED_USERS=your-username,my-bot
```

### 4. Choose a Claude backend and start

Follow the backend instructions in the [Setup](#setup) section above, then run:

```bash
docker compose up -d
```

### 5. Assign issues to your bot

In any repository the bot has access to, assign issues to the bot account. Clayde will pick them up automatically on the next loop cycle.

