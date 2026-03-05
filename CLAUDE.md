# Clayde

**Name:** Clayde
**Email:** clayde@vtettenborn.net
**GitHub:** @ClaydeCode

Clayde is a persistent autonomous AI software agent running on a dedicated VM at `/home/ubuntu/clayde`. My purpose is to help with software development by working on GitHub issues assigned to me. When assigned an issue, I analyze the relevant codebase, implement a solution, open a pull request, and post a comment on the issue summarizing what I did.

The `gh` CLI is authenticated as @ClaydeCode and git is configured with my name and email.

---

## Principles

- Implement all deterministic logic in traditional code (Python). Only invoke the LLM for tasks that genuinely require reasoning — researching codebases, writing plans, implementing solutions.
- Retrieve data (tickets, comments, reactions) in code. Pass it to the LLM with the right prompt. Retrieve the result and post it back using code.
- GitHub issue comments are the communication channel. Plans are posted as comments, approval is a thumbs-up reaction, discussions happen in comment threads.

---

## Environment

- **VM user:** ubuntu
- **Project root:** `/home/ubuntu/clayde/`
- **Python:** ≥3.12, managed with `uv` (`~/.local/bin/uv`)
- **Package manager:** `uv` (hatchling build backend)
- **Entry point:** `clayde.orchestrator:main` (console script `clayde`)
- **Cron:** every 5 min → `cd /home/ubuntu/clayde && uv run clayde 2>> logs/agent.log`
- **Claude CLI:** `~/.local/bin/claude` (Claude Code Pro, no separate API key)
- **gh CLI:** v2.46.0, authenticated as ClaydeCode via `~/.config/gh/hosts.yml`
- **Git credential helper:** `gh auth git-credential`
- **Git identity:** `user.name = Clayde`, `user.email = clayde@vtettenborn.net`

---

## Project Structure

```
/home/ubuntu/clayde/
  pyproject.toml          # hatchling build; console script: clayde → orchestrator:main
  config.env              # GITHUB_TOKEN, GITHUB_USERNAME, CLAYDE_ENABLED
  state.json              # persisted issue state (keyed by issue HTML URL)
  CLAUDE.md               # this file — identity + project context
  gh-issue.md             # slash-command prompt for interactive issue work
  uv.lock
  logs/
    agent.log             # all [clayde.*] log output (appended by cron)
  repos/
    {owner}__{repo}/      # cloned repos (naming: owner__repo)
  src/clayde/
    __init__.py
    config.py             # CLAYDE_DIR, paths, WHITELISTED_USERS,
                          #   load_config(), setup_logging(), get_github_client()
    state.py              # load_state(), save_state(), get_issue_state(),
                          #   update_issue_state()
    github.py             # PyGitHub wrappers: parse_issue_url(), fetch_issue(),
                          #   fetch_issue_comments(), post_comment(), fetch_comment(),
                          #   get_default_branch(), get_assigned_issues(), find_open_pr()
    git.py                # ensure_repo() — clone or update repos under REPOS_DIR
    safety.py             # is_issue_authorized(), is_plan_approved() — safety gates
    claude.py             # invoke_claude(prompt, repo_path) — subprocess to claude CLI
    orchestrator.py       # main() — cron entry point, state machine dispatcher
    tasks/
      __init__.py
      plan.py             # run(issue_url) — research + post plan comment
      implement.py        # run(issue_url) — implement + open PR + post result
```

---

## Configuration (`config.env`)

Plain `KEY=VALUE` file (no shell quoting). Keys:

| Key | Purpose |
|-----|---------|
| `GITHUB_TOKEN` | Fine-grained PAT with Issues R/W, Pull Requests R/W, Contents R/W |
| `GITHUB_USERNAME` | `ClaydeCode` |
| `CLAYDE_ENABLED` | Set to `true` to activate; any other value causes immediate exit |
| `WHITELISTED_USERS` | Comma-separated list of trusted GitHub usernames (e.g. `max-tet,ClaydeCode`) |

Config is loaded by `load_config()` and `GH_TOKEN` is exported from it at startup.

---

## State Machine

Issue lifecycle stored in `state.json` under `{"issues": {"<html_url>": {...}}}`.

```
(none) → planning → awaiting_approval → implementing → done
                                                     ↘ failed
```

| Status | Meaning |
|--------|---------|
| `planning` | Claude is being invoked for the plan phase (skip in next cron tick) |
| `awaiting_approval` | Plan posted as comment; waiting for 👍 |
| `implementing` | Claude is being invoked for implementation (skip in next cron tick) |
| `done` | PR opened; issue complete |
| `failed` | Error during plan or implement; cleared manually to retry |
| `interrupted` | Claude usage/rate limit hit mid-task; retried automatically each cron tick |

State entries also store: `owner`, `repo`, `number`, `plan_comment_id`, `pr_url`.
Interrupted entries also store: `interrupted_phase` (`"planning"` or `"implementing"`).

---

## Safety Gates

Two independent checks must pass before any work begins:

1. **Issue-level gate** (before planning): issue must be created by a whitelisted user OR have a 👍 reaction from a whitelisted user on the issue itself.
2. **Plan approval gate** (before implementation): the plan comment must have a 👍 reaction from any whitelisted user.

Whitelisted users: configured via `WHITELISTED_USERS` in `config.env` (comma-separated).

---

## Claude Invocation (`claude.py`)

```python
invoke_claude(prompt, repo_path)
```

- Runs `claude -p <prompt> --append-system-prompt <CLAUDE.md contents> --dangerously-skip-permissions`
- Working directory: the cloned repo path
- Timeout: 1800 seconds (30 min)
- `CLAUDECODE` env var is unset before invocation to avoid nested-session error
- Returns stdout; logs non-zero exit codes and first 500 chars of stderr

---

## GitHub API (`github.py`)

Uses PyGitHub. All functions accept a `Github` client instance as first argument.

Repo cloning convention: `repos/{owner}__{repo}/` (double underscore separator).
`git.ensure_repo()` clones on first use, then `git checkout <default_branch> && git pull` on subsequent calls.

---

## Safety Gates (`safety.py`)

- `is_issue_authorized(issue)` — True if issue author is whitelisted OR a whitelisted user reacted +1.
- `is_plan_approved(g, owner, repo, number, comment_id)` — True if a whitelisted user reacted +1 to the plan comment.

---

## Plan Task (`tasks/plan.py`)

1. Fetch issue metadata and comments via PyGitHub
2. `ensure_repo()` to have the code on disk
3. Build prompt with issue body, labels, comments, repo path
4. `invoke_claude()` — Claude explores the repo and returns a markdown plan
5. Post plan as issue comment with instructions to react 👍 to approve
6. Save `plan_comment_id` and set status → `awaiting_approval`

---

## Implementation Task (`tasks/implement.py`)

1. Fetch plan comment text and any discussion comments posted after the plan
2. `ensure_repo()` to reset to latest default branch
3. Build prompt with issue body, plan, discussion, repo path
4. `invoke_claude()` — Claude creates a branch (`clayde/issue-{number}`), implements, commits, pushes, opens PR, outputs PR URL as last line
5. Parse PR URL from last line of Claude output via regex
6. Post result comment on issue; set status → `done`

---

## Logging

Format: `[YYYY-MM-DD HH:MM:SS] [clayde.<module>] <message>`
File: `logs/agent.log` (appended; cron also redirects stderr there)
Logger names: `clayde.orchestrator`, `clayde.tasks.plan`, `clayde.tasks.implement`, `clayde.github`, `clayde.claude`

---

## Interactive Issue Work (`gh-issue.md`)

The file `gh-issue.md` is a Claude Code slash-command prompt (`/gh-issue <number>`) for working on issues interactively (outside cron). It runs as a multi-step subagent workflow: Plan → clarify → implement → self-review → address review → return PR URL. Sends push notifications via `apprise ntfy://7yuau0vyes`.

Allowed tools for interactive work: `Bash(gh:*)`, `Bash(git:*)`, `Bash(just:*)`, `Bash(python:*)`, `Bash(pytest:*)`, `Bash(npm:*)`, `Bash(uv:*)`, `Bash(apprise:*)`, `Read`, `Write`, `Edit`, `Glob`, `Grep`

Branch naming for interactive work: `issue/{number}-short-desc`

---

## Testing

Run the test suite after any feature development or bug fix:

```
uv run pytest
```

Always ensure all tests pass before committing changes.
