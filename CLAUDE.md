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
                        #   get_issue_author()
  git.py                # ensure_repo() — clone or update repos under REPOS_DIR
  safety.py             # Content filtering & plan approval: is_comment_visible(),
                        #   filter_comments(), is_issue_visible(),
                        #   has_visible_content(), is_plan_approved()
  responses.py          # Pydantic response models + parse_response() for structured JSON
  claude.py             # invoke_claude(prompt, repo_path) — dual backend:
                        #   ApiBackend (Anthropic SDK tool-use loop) or
                        #   CliBackend (Claude Code CLI subprocess)
  telemetry.py          # OpenTelemetry tracing: init_tracer(), get_tracer(),
                        #   FileSpanExporter (JSONL)
  orchestrator.py       # main() — single cycle, run_loop() — container entry point
  prompts/
    preliminary_plan.j2 # Jinja2 template for short preliminary plan
    thorough_plan.j2    # Jinja2 template for detailed thorough plan
    update_plan.j2      # Jinja2 template for updating a plan on new comments
    implement.j2        # Jinja2 template for implement prompt
    address_review.j2   # Jinja2 template for addressing PR review comments
    plan.j2             # Legacy template (kept for reference)
  tasks/
    __init__.py
    plan.py             # run_preliminary(url), run_thorough(url), run_update(url, phase)
    implement.py        # run(issue_url) — implement + open PR + assign reviewer
    review.py           # run(issue_url) — address PR review comments

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

Config is loaded via `get_settings()` (singleton). `GH_TOKEN` is exported at startup for the `gh` CLI.

---

## State Machine

Issue lifecycle stored in `state.json` under `{"issues": {"<html_url>": {...}}}`.

```
(none) → preliminary_planning → awaiting_preliminary_approval
       → planning → awaiting_plan_approval → implementing → pr_open → done
                                                                    ↘ failed
```

New comments in `awaiting_preliminary_approval` or `awaiting_plan_approval`
trigger plan updates (edit existing plan comment + post change summary).

PR reviews in `pr_open` trigger `addressing_review` → back to `pr_open`.

| Status | Meaning |
|--------|---------|
| `preliminary_planning` | Claude is producing a short preliminary plan |
| `awaiting_preliminary_approval` | Preliminary plan posted; waiting for 👍 |
| `planning` | Claude is producing a thorough implementation plan |
| `awaiting_plan_approval` | Thorough plan posted; waiting for 👍 |
| `implementing` | Claude is implementing the approved plan |
| `pr_open` | PR exists; monitoring for review comments |
| `addressing_review` | Claude is addressing review comments |
| `done` | PR approved or complete; issue finished |
| `failed` | Error during any phase; cleared manually to retry |
| `interrupted` | Claude usage/rate limit hit mid-task; retried automatically |

State entries store: `owner`, `repo`, `number`, `preliminary_comment_id`,
`plan_comment_id`, `pr_url`, `branch_name`, `last_seen_comment_id`,
`last_seen_review_id`.

Interrupted entries also store: `interrupted_phase` (`"preliminary_planning"`,
`"planning"`, `"implementing"`, or `"addressing_review"`).

Backward compatibility: old `awaiting_approval` status is mapped to
`awaiting_plan_approval`.

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
3. **Plan approval gates** remain: preliminary plan needs 👍 to proceed to
   thorough plan; thorough plan needs 👍 to proceed to implementation.

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
- Requires: OAuth credentials mounted from host `~/.claude/.credentials.json` (see docker-compose.yml)

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

---

## Safety Gates (`safety.py`)

- `is_comment_visible(comment)` — True if comment author is whitelisted OR has 👍 from whitelisted user.
- `filter_comments(comments)` — returns only visible comments.
- `is_issue_visible(issue)` — True if issue author is whitelisted OR has 👍 from whitelisted user.
- `has_visible_content(issue, comments)` — True if there is any visible content at all.
- `is_plan_approved(g, owner, repo, number, comment_id)` — True if a whitelisted user reacted +1 to the plan comment.

---

## Plan Task (`tasks/plan.py`)

Two-phase planning with update support:

### Phase 1: Preliminary Plan (`run_preliminary`)
1. Fetch issue metadata and filtered comments
2. `ensure_repo()` to have the code on disk
3. Build prompt with filtered issue body, labels, visible comments, repo path
4. `invoke_claude()` — Claude explores the repo and returns a short overview with questions
5. Post preliminary plan as issue comment
6. Set status → `awaiting_preliminary_approval`

### Phase 2: Thorough Plan (`run_thorough`)
1. Fetch preliminary plan comment and discussion after it
2. Build prompt including preliminary plan + discussion
3. `invoke_claude()` — Claude produces the full detailed plan
4. Post thorough plan as issue comment
5. Set status → `awaiting_plan_approval`

### Plan Updates (`run_update`)
Triggered when new visible comments are detected in `awaiting_preliminary_approval`
or `awaiting_plan_approval` states:
1. Fetch new visible comments since `last_seen_comment_id`
2. Build update prompt with current plan + new comments
3. `invoke_claude()` — Claude produces summary + updated plan
4. **Edit** the existing plan comment AND **post** a new comment with change summary

---

## Implementation Task (`tasks/implement.py`)

1. Fetch plan comment text and filtered discussion comments after the plan
2. `ensure_repo()` to reset to latest default branch
3. Build prompt with issue body, plan, discussion, repo path
4. `invoke_claude()` — Claude creates a branch, implements, commits, and pushes
5. Python code creates PR via PyGitHub or finds an existing one
6. **Assign the issue author as PR reviewer** via `add_pr_reviewer()`
7. Post result comment on issue; set status → `pr_open`

---

## Review Task (`tasks/review.py`)

Handles PR review comments after implementation:

1. Fetch PR reviews and review comments via PyGitHub
2. Filter to new reviews since `last_seen_review_id`, ignoring own reviews
3. If reviews have comments/body: invoke Claude with `address_review.j2` prompt
4. Claude makes changes and pushes to the existing branch
5. Post summary comment on issue; update `last_seen_review_id`; status stays `pr_open`
6. If a review is "APPROVED" with no comments: set status → `done`

---

## Logging

Format: `[YYYY-MM-DD HH:MM:SS] [clayde.<module>] <message>`
File: `/data/logs/agent.log` (appended)
Logger names: `clayde.orchestrator`, `clayde.tasks.plan`, `clayde.tasks.implement`, `clayde.tasks.review`, `clayde.github`, `clayde.claude`

---

## Testing

Run the test suite after any feature development or bug fix:

```
uv run pytest
```

Always ensure all tests pass before committing changes.
