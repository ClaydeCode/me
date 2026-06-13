# Clayde

Clayde is a persistent autonomous AI software agent running in a Docker container. My purpose is to help with software development by working on GitHub issues assigned to me. When assigned an issue, I analyze the relevant codebase, implement a solution, open a pull request, and post a comment on the issue summarizing what I did.

The `gh` CLI is authenticated as the configured bot GitHub account and git is configured with the identity from `CLAYDE_GIT_NAME` and `CLAYDE_GIT_EMAIL`.

---

## Principles

- Implement all deterministic logic in traditional code (Python). Only invoke the LLM for tasks that genuinely require reasoning — researching codebases, writing plans, implementing solutions.
- Retrieve data (tickets, comments, reactions) in code. Pass it to the LLM with the right prompt. Retrieve the result and post it back using code.
- GitHub issue comments are the communication channel. Plans are posted as comments, approval is a thumbs-up reaction, discussions happen in comment threads.

---

## Environment

- **Python:** ≥3.12, managed with `uv` (`~/.local/bin/uv`)
- **Package manager:** `uv` (hatchling build backend)
- **Entry points:** `clayde` → `orchestrator:run_loop` (container mode, continuous loop), `clayde-once` → `orchestrator:main` (single cycle)
- **Deployment:** Docker container via `docker-compose.yml`; loop interval configurable via `CLAYDE_INTERVAL` env var (default 300s)
- **Container layout:** Application code at `/opt/clayde`, data at `/data` (single volume mount from host `./data`)
- **Claude:** Dual backend — Anthropic Python SDK (`api`) or Claude Code CLI (`cli`), selected by `CLAYDE_CLAUDE_BACKEND`
- **Git credential helper:** `gh auth git-credential` (configured globally in the container)
- **Git identity:** configured at container startup from `CLAYDE_GIT_NAME` and `CLAYDE_GIT_EMAIL` env vars

---

## Project Structure

```
# Source repository
pyproject.toml          # hatchling build; console scripts: clayde, clayde-once
CLAUDE.md               # this file — identity + project context
Dockerfile              # Python 3.13-slim image with git, gh, uv
docker-compose.yml      # container deployment config
uv.lock
src/clayde/
  __init__.py
  config.py             # Settings (pydantic-settings), APP_DIR, DATA_DIR,
                        #   get_settings(), get_github_client(), setup_logging()
  state.py              # load_state(), save_state(), get_issue_state(),
                        #   update_issue_state()
  github.py             # PyGitHub wrappers: parse_issue_url(), fetch_issue(),
                        #   fetch_issue_comments(), post_comment(), edit_comment(),
                        #   fetch_comment(), get_default_branch(),
                        #   get_assigned_issues(),
                        #   find_open_pr(), create_pull_request(), is_blocked(),
                        #   add_pr_reviewer(), get_pr_reviews(),
                        #   get_pr_review_comments(), parse_pr_url(),
                        #   get_issue_author(), get_check_runs(),
                        #   get_required_check_names()
  git.py                # ensure_repo() — clone or update repos under REPOS_DIR
  safety.py             # Content filtering & plan approval: is_comment_visible(),
                        #   filter_comments(), is_issue_visible(),
                        #   get_new_visible_comments(), has_visible_content()
  responses.py          # Pydantic response models + parse_response() for structured JSON
  claude.py             # invoke_claude(prompt, repo_path) — dual backend:
                        #   ApiBackend (Anthropic SDK tool-use loop) or
                        #   CliBackend (Claude Code CLI subprocess)
  telemetry.py          # OpenTelemetry tracing: init_tracer(), get_tracer(),
                        #   FileSpanExporter (JSONL)
  orchestrator.py       # main() — single cycle, run_loop() — container entry point
  prompts/
    work.j2             # Jinja2 template for the unified work prompt
    fix_ci.j2           # prompt for diagnosing/fixing a failing PR pipeline
  tasks/
    __init__.py
    work.py             # run(issue_url) — unified: Claude decides next action
                        #   (ask, plan, implement, open PR, or address review)
    fix_ci.py           # run(issue_url, pr_url, branch_name, failed_checks) —
                        #   self-fix a failing CI pipeline on a clayde PR
  webhook/
    __init__.py
    app.py              # FastAPI app, /webhook/pebble, /health, OTel enqueue span
    auth.py             # constant-time bearer-token verification
    notify.py           # send_ntfy + NotificationPayload model
    queue.py            # PebbleJob, JobQueue (in-memory asyncio.Queue), QueueFullError
    runner.py           # invoke_claude_pebble — async CLI subprocess, fresh session
    skills.py           # Skill model, /skills/ discovery, system + user prompt builders
    worker.py           # worker_loop, process_job — pop jobs, OTel process span
  skills_builtin/
    ping.md             # built-in health-check skill (baked into image)

# Container paths
/opt/clayde/            # application code (WORKDIR)
/data/                  # mounted from host ./data
  config.env            # CLAYDE_GITHUB_TOKEN, CLAYDE_CLAUDE_API_KEY, etc.
  state.json            # persisted issue state (keyed by issue HTML URL)
  logs/
    agent.log           # all [clayde.*] log output
    traces.jsonl        # OpenTelemetry spans (JSONL)
  repos/
    {owner}__{repo}/    # cloned repos (naming: owner__repo)
```

---

## Configuration (`data/config.env`)

Plain `KEY=VALUE` file (no shell quoting). All keys use `CLAYDE_` prefix and are loaded by pydantic-settings into the `Settings` class.

| Key | Purpose |
|-----|---------|
| `CLAYDE_GITHUB_TOKEN` | Classic PAT with full `repo` scope |
| `CLAYDE_GITHUB_USERNAME` | The bot account username (e.g. `YourBotName`) |
| `CLAYDE_ENABLED` | Set to `true` to activate; any other value causes immediate exit |
| `CLAYDE_WHITELISTED_USERS` | Comma-separated list of trusted GitHub usernames |
| `CLAYDE_GIT_NAME` | Git commit author name (defaults to `CLAYDE_GITHUB_USERNAME` if not set) |
| `CLAYDE_GIT_EMAIL` | Git commit author email (required) |
| `CLAYDE_CLAUDE_API_KEY` | Anthropic API key for Claude SDK calls (required when backend=`api`) |
| `CLAYDE_CLAUDE_MODEL` | Model to use (default: `claude-opus-4-6`) |
| `CLAYDE_CLAUDE_BACKEND` | `api` (default) or `cli` — selects Anthropic SDK or Claude Code CLI |
| `CLAYDE_CI_FIX_MAX_ATTEMPTS` | Max autonomous CI-fix attempts per PR before giving up and notifying (default 3) |
| `CLAYDE_PEBBLE_ENABLED` | Set to `true` to enable the Pebble webhook |
| `CLAYDE_PEBBLE_TOKEN` | Bearer token the Pebble app sends |
| `CLAYDE_PEBBLE_HOST` | Public hostname for Traefik routing |
| `CLAYDE_PEBBLE_PORT` | Internal HTTP port (default 8080) |
| `CLAYDE_PEBBLE_TIMEOUT` | Per-request CLI timeout seconds (default 300) |
| `CLAYDE_PEBBLE_QUEUE_MAX` | Max queued jobs before 503 (default 100) |
| `CLAYDE_NTFY_TOPIC` | ntfy.sh topic for Pebble outcome notifications |
| `CLAYDE_NTFY_BASE_URL` | ntfy base URL (override for self-host) |
| `CLAYDE_NTFY_TIMEOUT_S` | ntfy POST timeout seconds (default 10) |
| `CLAYDE_KB_PATH` | In-container KB path; Pebble per-request cwd (default `/home/clayde/knowledge_base`) |

Config is loaded via `get_settings()` (singleton). `GH_TOKEN` is exported at startup for the `gh` CLI.

---

## Work Loop (event-driven)

There is no rigid status state machine. Each tick, the orchestrator iterates
the issues assigned to the bot and, for each, decides whether anything has
happened since last cycle. If so, it hands the issue to the unified **work
task**, which lets Claude choose the next action — ask questions, post a
plan, implement, open a PR, or address review comments.

Per-issue state is stored in `state.json` under
`{"issues": {"<html_url>": {...}}}`. Fields written by the current code:

| Field | Meaning |
|-------|---------|
| `owner`, `repo`, `number` | Issue identity |
| `issue_title` | Title (for log labels) |
| `branch_name` | Working branch (`clayde/issue-<N>` by default) |
| `pr_url` | PR opened for this issue, once detected via `find_open_pr()` |
| `in_progress` | `True` while the work task runs; a crash leaves it set so the next cycle retries |
| `last_seen_at` | ISO-UTC timestamp of the last completed cycle; used to detect new activity |
| `ci_fix_attempts` | Number of autonomous CI-fix attempts made for this PR (capped at `ci_fix_max_attempts`) |
| `last_ci_fix_attempt_sha` | PR head SHA of the last CI-fix attempt; prevents re-attempting the same commit |
| `ci_fix_exhausted_notified` | `True` once the operator has been alerted that the attempt budget is spent (avoids re-notifying) |

**Activity detection** (`_handle_issue`): the work task is invoked when any of
— `in_progress` is set (retry), `last_seen_at` is `None` (never processed),
there are new whitelist-visible comments, or there is new PR review activity
(inline comments or a review body). A pure PR approval with no comments does
**not** invoke Claude — it just advances `last_seen_at`.

**CI self-fix**: when there is *no* new human activity but an open PR exists,
`_handle_ci_fix()` checks the PR head commit's check runs (`get_check_runs()`,
filtered to branch-protection-required checks when defined). If a required
check has failed and a fix has not yet been attempted for that head SHA, the
`fix_ci` task is invoked: Claude inspects the failing job logs, pushes a fix to
the PR branch, and a summary is posted as an issue comment. Attempts are capped
per PR by `ci_fix_max_attempts` (default 3); once exhausted, the operator is
notified once via ntfy and Clayde stops attempting. Green/pending CI falls
through to normal review monitoring unchanged.

**Limits & retries**: `UsageLimitError` / `InvocationTimeoutError` from Claude
leave `in_progress=True` so the next cycle retries automatically. Other
exceptions clear `in_progress` and log the error. Closed issues are pruned
from state at the start of each tick.

---

## Safety & Content Filtering

Instead of gatekeeping which issues to work on, content is **filtered** so
the LLM only sees comments and issue bodies that are created by or approved
(👍) by a whitelisted user. Every assigned issue is a candidate for work,
but:

1. **Blocked issues** are skipped — detected via "blocked by #N" / "depends
   on #N" text patterns in the issue body, and via GitHub sub-issue
   relationships (timeline API).
2. **No visible content** → issue is skipped. If the issue body and all
   comments are from non-whitelisted users without any whitelisted 👍, there
   is nothing for the LLM to work with.

Only whitelist-visible content reaches the LLM; Claude decides within the work
task whether it has enough to plan, implement, or must ask first.

Whitelisted users: configured via `CLAYDE_WHITELISTED_USERS` in `data/config.env`.

---

## Claude Invocation (`claude.py`)

```python
invoke_claude(prompt, repo_path)
```

Two backends, selected by `CLAYDE_CLAUDE_BACKEND`:

### API backend (`api`, default)
- Uses the Anthropic Python SDK (`anthropic` package) directly
- Tool-use mode with `bash` and `text_editor` tools (computer-use beta)
- System prompt: CLAUDE.md contents
- Model: configurable via `CLAYDE_CLAUDE_MODEL` (default: `claude-opus-4-6`)
- Tool execution loop: Claude requests tool calls, Python executes them locally (cwd = repo_path), results fed back
- Timeout: 1800 seconds (30 min) for the full tool loop
- Rate/usage limit detection: raises `UsageLimitError` on 429 or 529 status codes
- Token usage and cost tracking via OpenTelemetry spans
- Conversation persistence: full message list saved to JSON for resumption
- Requires: `CLAYDE_CLAUDE_API_KEY`

### CLI backend (`cli`)
- Runs the Claude Code CLI (`claude`) as a subprocess with `--output-format json`
- Claude manages its own tool loop internally
- System prompt: CLAUDE.md contents passed via `--append-system-prompt`
- Session resumption: saves `session_id` from JSON output, resumes via `--resume <session_id>`
- Rate/usage limit detection: text-pattern matching on stdout/stderr
- No per-token cost tracking (returns `cost_eur=0.0`)
- Requires: a dedicated Claude config dir dir-mounted from the host (`~/clayde-claude` → `/home/clayde/.claude`); use a separate `CLAUDE_CONFIG_DIR=~/clayde-claude claude login`. Mount the directory, not the `.credentials.json` file — token refresh renames the file (new inode) and a single-file mount goes stale. See docker-compose.yml / README.

---

## GitHub API (`github.py`)

Uses PyGitHub. All functions accept a `Github` client instance as first argument.

Repo cloning convention: `repos/{owner}__{repo}/` (double underscore separator).
`git.ensure_repo()` clones on first use, then `git checkout <default_branch> && git pull` on subsequent calls.

Key functions:
- `is_blocked(g, owner, repo, number)` — checks body text patterns and timeline API for blocking relationships
- `add_pr_reviewer(g, owner, repo, pr_number, login)` — requests a review on a PR
- `get_pr_reviews()` / `get_pr_review_comments()` — fetch PR review data
- `edit_comment()` — edit an existing issue comment
- `parse_pr_url()` — parse PR URL into (owner, repo, pr_number)
- `get_check_runs(g, owner, repo, ref)` — failed check runs for a commit SHA (name, conclusion, details_url)
- `get_required_check_names(g, owner, repo, branch)` — required status-check names from branch protection (empty set when unprotected)

---

## Safety Gates (`safety.py`)

- `is_comment_visible(comment)` — True if comment author is whitelisted OR has 👍 from whitelisted user.
- `filter_comments(comments)` — returns only visible comments.
- `is_issue_visible(issue)` — True if issue author is whitelisted OR has 👍 from whitelisted user.
- `get_new_visible_comments(comments, last_seen_at)` — visible comments created after `last_seen_at`.
- `has_visible_content(issue, comments)` — True if there is any visible content at all.

---

## Work Task (`tasks/work.py`)

A single `run(issue_url)` handles every phase. There is no separate
plan/implement/review task — Claude decides what to do from the context it is
given.

1. `fetch_issue()` + `get_default_branch()`; `ensure_repo()` resets the clone
   to the latest default branch.
2. Persist issue metadata and `branch_name` to state.
3. Gather context: whitelist-filtered comments, and — if a PR already exists —
   its review bodies and inline review comments.
4. Render `work.j2` with the issue body, labels, comments, review text, repo
   path, `branch_name`, `pr_url`, and `default_branch`.
5. `invoke_claude()` — Claude explores, then takes whatever action fits: post
   a plan/question comment, implement and push, open a PR via `gh pr create`,
   or push fixes addressing review comments. It returns a JSON `{summary}`
   (`WorkResponse`).
6. Post the `summary` as an issue comment (best-effort: raw output snippet if
   JSON parsing fails).
7. Detect a PR via `find_open_pr(branch_name)`. On first detection, **assign
   the issue author as reviewer**; persist `pr_url` to state.

Plans and questions are ordinary issue comments — there is no separate
approval gate or 👍 reaction required to advance. Iteration happens through
the normal comment/review activity-detection loop.

---

## Logging

Format: `[YYYY-MM-DD HH:MM:SS] [clayde.<module>] <message>`
File: `/data/logs/agent.log` (appended)
Logger names: `clayde.orchestrator`, `clayde.tasks.work`, `clayde.github`, `clayde.claude`, `clayde.git`, `clayde.state`

---

## Pebble Webhook

When `CLAYDE_PEBBLE_ENABLED=true`, the container also serves a FastAPI
webhook for a Pebble watch app, alongside the existing GitHub poll loop
(both run on the same asyncio event loop).

- `POST /webhook/pebble` — accepts `{"text": str, "timestamp": int}` with
  `Authorization: Bearer <CLAYDE_PEBBLE_TOKEN>`. Returns 200 with a job id.
- `GET /health` — liveness probe (no auth).

The text is dispatched to the Claude CLI with a system prompt listing
*skills* found under the in-container path `/skills/`. Each skill is a
single markdown file with `name` + `description` frontmatter. Built-in
skills live at `/skills/builtin/` (baked into the image — currently
`ping`); host-mounted skill directories sit alongside (e.g.
`/skills/personal/`, `/skills/shared/`).

Claude is free to use any number of skills per request — there is no
single-skill cap. If no skill fits, Claude uses judgement (typically
capturing into the knowledge base inbox).

Per-request `cwd` is `${CLAYDE_KB_PATH}` (default
`/home/clayde/knowledge_base`), mounted RW from the host
`~/knowledge_base/`. Sync to other devices is handled by Syncthing on
the host — the container performs no `git` operations against the KB.

Every terminal outcome (success, claude-reported failure, timeout, usage
limit, CLI error, auth error, worker exception, queue full) emits an ntfy
notification on `${CLAYDE_NTFY_BASE_URL}/${CLAYDE_NTFY_TOPIC}`. Claude
produces the title/body via a fenced JSON tail in its output; the
framework falls back to a synthetic "no summary" payload when parsing
fails.

Traefik handles TLS (Let's Encrypt) and routes
`https://<CLAYDE_PEBBLE_HOST>/webhook/pebble` over a private docker
network. The `clayde` service is not attached to any externally-reachable
network — the only ingress path is through Traefik.

---

## Testing

Run the test suite after any feature development or bug fix:

```
uv run pytest
```

Always ensure all tests pass before committing changes.
