<p align="center">
  <img src="clayde.jpg" width="400" alt="Clayde" />
</p>

# Clayde

Clayde is a persistent autonomous AI software agent that lives on a dedicated VM and works GitHub issues end-to-end — researching codebases, writing plans, implementing solutions, and opening pull requests.

---

## What is Clayde?

Clayde is assigned GitHub issues in software repositories. For each issue it:

1. Checks for new whitelist-visible activity since its last access
2. Invokes Claude with the full issue context — Claude decides what to do next: ask clarifying questions, post a plan, implement the solution, or address review comments
3. Posts a summary comment after each work cycle
4. Opens a pull request (Claude creates the PR directly with a description and, for diffs spanning more than 3 files, a recommended reading order) and assigns the issue author as reviewer
5. Monitors the PR and addresses review comments when they appear

Clayde runs as a Docker container in a continuous loop (default: every 5 minutes). Rather than a rigid state machine, it uses **timestamp-based activity detection**: each issue records the last time it was processed, and only new visible activity since that timestamp triggers a new Claude invocation.

---

## How It Works

Clayde's loop is event-driven and stateless by design:

1. **Fetch assigned issues** from GitHub.
2. **For each issue**: check whether there is new whitelist-visible activity (comments or PR reviews) since `last_seen_at`. If the issue has never been seen, or a previous run was interrupted, it is always processed.
3. **Invoke Claude once** with the full context: issue body, all visible comments, and any open PR reviews. Claude decides the next action — no hard phases.
4. **Detect PR**: after each run, check for an open PR on the working branch and persist its URL.
5. **Update `last_seen_at`** to the current time so Clayde's own reply comments don't re-trigger a cycle.
6. **Crash recovery**: `in_progress` is set before invoking Claude and cleared after. If the process crashes mid-run, the next cycle retries automatically.
7. **Pure PR approvals** (no comments) update `last_seen_at` without invoking Claude.
8. **Closed issues** are pruned from state automatically.

---

## Safety & Content Filtering

Clayde uses **content filtering** rather than gatekeeping which issues to work on. It will only act on content that is visible:

- An issue body or comment is **visible** if it was written by a whitelisted user, or has a 👍 reaction from a whitelisted user.
- If an issue has no visible content at all, it is skipped.
- Blocked issues (those with "blocked by #N" or "depends on #N" in the body) are also skipped.

There are no hard approval gates — Claude engages with the issue as soon as there is visible content, and the human can steer the conversation by replying in the issue thread.

Whitelisted users are configured via `CLAYDE_WHITELISTED_USERS` in `data/config.env`.

---

## Capabilities

- **Multi-repo support**: Clones and works on any GitHub repository it has access to
- **Event-driven loop**: Only invokes Claude when there is new visible activity — no wasted cycles
- **Natural conversation**: Claude engages directly in the issue comment thread, asking questions and posting plans as needed
- **Full issue lifecycle**: Engage → implement → PR → review, all driven by new activity
- **PR creation by Claude**: Claude writes the PR description and a recommended reading order for larger diffs
- **PR review handling**: Reads and addresses reviewer feedback automatically
- **Rate-limit resilience**: Detects Claude usage limits and automatically retries
- **Crash recovery**: `in_progress` flag ensures interrupted runs are retried next cycle
- **Safety filtering**: Whitelist-based content filtering prevents acting on unauthorized content
- **Observability**: OpenTelemetry tracing with JSONL file export
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

See [Configuration](#configuration) for all available settings.

### 4. Choose a Claude backend

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

1. On the host machine, create a **dedicated** login for the container in its
   own config directory (kept separate from your personal `~/.claude`):
   ```bash
   CLAUDE_CONFIG_DIR=~/clayde-claude claude login
   ```
2. Set in `data/config.env`:
   ```
   CLAYDE_CLAUDE_BACKEND=cli
   ```
   (`CLAYDE_CLAUDE_API_KEY` is not required for the CLI backend.)

The `docker-compose.yml` mounts the `~/clayde-claude` **directory** into the
container as its Claude config dir. Two things matter here:

- **Mount the directory, not the `.credentials.json` file.** The CLI refreshes
  its short-lived OAuth token by writing a new file and atomically renaming it
  into place — which changes the file's inode. A single-file bind mount is
  pinned to the original inode at container start, so it never sees the new
  token and the container fails with "authentication expired" until you restart
  the stack. A directory mount resolves the path live, so refreshes propagate
  with no restart.
- **Use a dedicated login, not your personal `~/.claude`.** That directory
  holds your interactive sessions, projects, and history; sharing it exposes
  that state to the container. A separate login also gives the container its own
  OAuth refresh-token lineage, so its token refreshes can't invalidate your
  host login (refresh tokens are single-use).

### 5. Start the container

```bash
docker compose up -d
```

Clayde will start its loop, checking for assigned issues every 5 minutes (configurable via `CLAYDE_INTERVAL`).

### 6. Assign issues to your bot

In any repository the bot has access to, assign issues to the bot account. Clayde will pick them up automatically on the next loop cycle.

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
| `CLAYDE_INTERVAL` | Loop interval in seconds (default: `300`) |
| `CLAYDE_CLAUDE_BACKEND` | `api` (default) or `cli` |
| `CLAYDE_CLAUDE_API_KEY` | Anthropic API key (required when backend=`api`) |
| `CLAYDE_CLAUDE_MODEL` | Model to use (default: `claude-opus-4-6`) |
| `CLAYDE_PEBBLE_ENABLED` | Set to `true` to enable the Pebble webhook |
| `CLAYDE_PEBBLE_TOKEN` | Bearer token the Pebble app sends |
| `CLAYDE_PEBBLE_HOST` | Public hostname for Traefik routing |
| `CLAYDE_PEBBLE_PORT` | Internal HTTP port (default `8080`) |
| `CLAYDE_PEBBLE_TIMEOUT` | Per-request CLI timeout seconds (default `300`) |
| `CLAYDE_PEBBLE_QUEUE_MAX` | Max queued jobs before 503 (default `100`) |
| `CLAYDE_NTFY_TOPIC` | ntfy.sh topic for Pebble outcome notifications |
| `CLAYDE_NTFY_BASE_URL` | ntfy base URL (override for self-host) |
| `CLAYDE_NTFY_TIMEOUT_S` | ntfy POST timeout seconds (default `10`) |
| `CLAYDE_KB_PATH` | In-container KB path; Pebble per-request cwd (default `/home/clayde/knowledge_base`) |

---

## Pebble Watch Integration

Clayde can also receive voice commands from a Pebble watch app via an
HTTPS webhook. When enabled, the container additionally serves a FastAPI
endpoint alongside the existing GitHub poll loop.

To enable:

1. Set `CLAYDE_PEBBLE_ENABLED=true` and a strong random
   `CLAYDE_PEBBLE_TOKEN` in `data/config.env`.
2. Set `CLAYDE_PEBBLE_HOST` to the public hostname Traefik should serve
   (e.g. `clayde.example.com`). The hostname must resolve to the host's
   public IP and ports `80` + `443` must be open for Let's Encrypt
   HTTP-01 challenges.
3. Mount one or more skill directories under `/skills/` in
   `docker-compose.yml`. Each skill is a markdown file with frontmatter
   `name` and `description` (see `CLAUDE.md` for the full format).
   Built-in skills (currently `ping`) are baked into the image at
   `/skills/builtin/`.
4. Mount `~/knowledge_base` to `/home/clayde/knowledge_base` (already
   wired in `docker-compose.yml`) so Claude has a writable working
   directory. Sync across devices is handled by Syncthing on the host —
   the container performs no `git` against the KB.
5. Set `CLAYDE_NTFY_TOPIC` (and optionally `CLAYDE_NTFY_BASE_URL` for
   self-hosted ntfy) to receive outcome notifications on your phone for
   every Pebble request.
6. Configure the Pebble app to POST to
   `https://<CLAYDE_PEBBLE_HOST>/webhook/pebble` with the bearer token.

The webhook is fire-and-forget: requests return `200` with a job id and
work happens asynchronously in a single serial worker. Each request
spawns a fresh Claude CLI session (no context carries between requests)
with `cwd` set to the knowledge-base mount. Claude is free to use any
number of skills per request; every terminal outcome (success, failure,
timeout, usage limit, queue full, etc.) emits an ntfy notification.

---

## Scheduler

Clayde can also run tasks on a schedule — recurring (cron) or one-off (a
single future time) — instead of waiting for a Pebble request. Scheduled
runs feed the same job queue and worker as the Pebble webhook, so they use
the same Claude CLI backend and permission mode; only the prompt framing and
notification behaviour differ (below).

To enable:

1. Create the task directory on the host: `mkdir -p ~/clayde-tasks`. It's
   mounted **read-write** at `/tasks` (already wired in
   `docker-compose.yml`) — read-write because fired one-off tasks are moved
   into a `done/` subdirectory, not deleted.
2. Set `CLAYDE_SCHEDULER_ENABLED=true` in `data/config.env`. Other
   `CLAYDE_SCHEDULER_*` keys (poll interval `INTERVAL_S`, default timezone
   `TZ`, in-container task dir `DIR`, per-task CLI timeout `TIMEOUT`) have
   working defaults — see `config.env.template` if you need to change them.
3. Drop one markdown file per task into `~/clayde-tasks/`:

   ```markdown
   ---
   cron: "0 8 * * *"        # recurring, 5-field cron
   # at: 2026-09-21T08:00    # one-off, ISO-8601 local datetime (mutually exclusive with cron)
   tz: Europe/Berlin         # optional; default CLAYDE_SCHEDULER_TZ
   enabled: true             # optional; default true
   title: keep-warm          # optional; label for logs only
   timeout: 4h               # optional; default CLAYDE_SCHEDULER_TIMEOUT (300s)
   ---
   Run a trivial health check and confirm you are alive.
   ```

   Exactly one of `cron` or `at` is required — `cron` is a standard 5-field
   expression; `at` is a local datetime interpreted in `tz`. Everything
   after the frontmatter is the prompt sent to Claude. Malformed files
   (missing/both schedule keys, bad cron, unterminated frontmatter) are
   logged and skipped, not ntfy'd — that would spam every poll tick.

   `timeout` sets this task's own CLI timeout, overriding
   `CLAYDE_SCHEDULER_TIMEOUT` for that one job — useful for a long overnight
   deep-research run that needs more than the default budget. It accepts a
   duration (`4h`, `90m`, `45s`) or a bare number of seconds, and is
   hard-capped at 4 hours; a requested value above the cap is clamped and
   logged, not rejected. A malformed `timeout` value makes the whole file
   malformed, same as a bad `cron`.

   Long-running tasks should have their prompt instruct the agent to persist
   progress periodically (e.g. write interim findings to the KB inbox as it
   goes), not just at the end. A run that hits a usage limit or its timeout
   is a single unattended attempt with no auto-resume, so whatever interim
   state it wrote is all that survives.

A fired one-off task is moved to `~/clayde-tasks/done/<epoch>-<name>.md`
rather than deleted, so it stays as a record of what ran and when. Recurring
tasks are never moved; their last-fired time is tracked in the container's
own `/data/scheduler_state.json`, keyed by filename.

### Notifications

Unlike Pebble requests, a scheduler job stays **silent on success** — the
framework emits no ntfy for a clean run. The framework still ntfy's on
**failure** (timeout, usage limit, CLI error, auth error, worker crash),
because a run that didn't finish can't self-report. If a task should notify
on success (e.g. "call the dentist" or a genuine reminder), say so in the
task's own prompt and let the agent send it itself via the `ntfy-ping`
skill — there is no per-task notify field.

### Skill library mount

`docker-compose.yml` mounts the whole personal skill library read-only at
`/skills/kb`, alongside the existing `/skills/personal` and `/skills/shared`
Pebble skill dirs, so a scheduled task (or a Pebble request) can use any
skill from the knowledge base, including `ntfy-ping`.

### Permission mode

Both scheduled and Pebble jobs run the Claude CLI with
`--permission-mode auto --permission-prompts none` — Claude proceeds without
interactive approval, since nobody is watching an unattended run to answer a
prompt.

### Bootstrapping caveat

The scheduler presumes the Claude CLI login (see [Option B: Claude Code
CLI](#option-b-claude-code-cli-cli) above) is already established. A recurring
task that runs the CLI regularly keeps that login's OAuth refresh lineage
alive once it's ticking — but the login has to be created once, by hand,
*before* the first tick, and must not be left to lapse in the meantime. A
scheduler enabled against a login that was never created, or that expired
before its first run, fails with an auth error on every tick (which does
ntfy, per the failure behaviour above).
