# Scheduled Tasks — File-Driven Scheduler — Design

**Date:** 2026-09-20 (revised 2026-09-21)
**Status:** Proposed — awaiting review

**Changes in this revision:** dropped the per-task `notify` field in favour of
prompt-driven notification plus framework failure-notify; mount the whole KB
skill library and adjust discovery to the directory-based `SKILL.md` format;
switch the shared runner from `--dangerously-skip-permissions` to auto
permission mode.

## Goal

Give Clayde a scheduler that runs prompts on a schedule, independent of any
interactive session or the Pebble watch. Each task is one markdown file in a
host-mounted directory: frontmatter says *when* to run (a one-off timestamp or
a recurring cron expression), the body is the prompt. Due tasks are dispatched
through the existing job pipeline — a fresh Claude CLI session with `/skills/`
available, cwd = KB.

Primary uses: reminders to self, recurring maintenance prompts, and the
credential keep-warm ping that motivated this work (a recurring task whose CLI
run refreshes the container's OAuth token so its login never lapses from
disuse).

## Non-goals

- **Crash-safe delivery.** The job queue is in-memory. A restart in the window
  between enqueue and execution loses that one run (the Pebble path has the
  same property). Recurring tasks self-heal on the next tick; a fired one-off
  would sit in `done/` un-run. A worker→scheduler completion callback would fix
  this and is deliberately out of scope for v1.
- **Backfilling missed occurrences.** After downtime an overdue task fires
  once, never once-per-missed-tick.
- **Authoring tasks from other devices.** The task directory is a dedicated
  host dir, not the synced knowledge base. Tasks are created on the VM.
- **Sub-minute schedules.** Cron granularity is one minute.
- **A separate reminders feature.** A reminder is just a task whose prompt asks
  the agent to notify.
- **Per-task silence-on-failure.** Scheduler failures always notify (below).
  There is no knob to silence a failing task.

## Refactor: neutral `service/` package

The job type and execution machinery are no longer Pebble-specific once the
scheduler feeds the same queue. Lift the shared core out of `webhook/` into a
new `src/clayde/service/` package (behaviour-preserving move):

| New path | From | Contents |
|----------|------|----------|
| `service/queue.py` | `webhook/queue.py` | `Job` (renamed from `PebbleJob`), `JobQueue`, `QueueFullError` |
| `service/worker.py` | `webhook/worker.py` | `worker_loop`, `process_job` |
| `service/runner.py` | `webhook/runner.py` | `invoke_claude`, `extract_notification_payload` |
| `service/notify.py` | `webhook/notify.py` | `send_ntfy`, `NotificationPayload` |
| `service/skills.py` | `webhook/skills.py` | skill discovery + prompt builders |

`webhook/` keeps only the HTTP producer: `app.py`, `auth.py`. A new
`scheduler/` package is the second producer. `orchestrator.py` and
`webhook/__init__.py` update their imports. Test files move to
`tests/service/` accordingly; `tests/webhook/` keeps the app/auth tests.

Telemetry: the worker span `clayde.pebble.process` becomes `clayde.job.process`
with an `origin` attribute (`pebble` | `scheduler`). The enqueue span in
`webhook/app.py` stays `clayde.pebble.enqueue` — that endpoint really is Pebble.

### `Job` model

```python
@dataclass(frozen=True)
class Job:
    id: str
    text: str            # the prompt
    timestamp: int        # epoch seconds at enqueue
    origin: str = "pebble"          # "pebble" | "scheduler"
```

`origin` selects prompt framing and the success-notification behaviour. It
defaults to Pebble, so `webhook/app.py` constructs `Job` unchanged.

## Task file format

A dedicated host dir `~/clayde-tasks/`, mounted **read-write** at `/tasks`
(read-write so fired one-offs can be moved to `done/`; the dir is owned by
`ubuntu`/1000, the uid the container runs as). One markdown file per task:

```markdown
---
cron: "0 8 * * *"        # recurring, 5-field cron
# at: 2026-09-21T08:00    # one-off, ISO-8601 local datetime (mutually exclusive with cron)
tz: Europe/Berlin         # optional; default CLAYDE_SCHEDULER_TZ
enabled: true             # optional; default true
title: keep-warm          # optional; label for logs only
---
Run a trivial health check and confirm you are alive.
```

Rules:

- Exactly one of `cron` / `at` is required. Two distinct keys (not one
  overloaded `schedule:`) so a malformed cron can't be misread as a timestamp.
- `cron` is a standard 5-field expression, parsed by `croniter`.
- `at` is an ISO-8601 local datetime, interpreted in `tz`.
- `tz` is an IANA name resolved via stdlib `zoneinfo`; default from
  `CLAYDE_SCHEDULER_TZ`.
- `enabled: false` parks a task without deleting it.
- Malformed files (missing/both schedule keys, bad cron, unterminated
  frontmatter) are logged at WARNING and skipped — same policy as skill
  discovery. No ntfy on a malformed file (would spam every tick).

### Directory & state layout

```
~/clayde-tasks/
  keep-warm.md          # recurring
  call-dentist.md       # one-off
  done/                 # fired one-offs moved here (timestamp-prefixed)
```

Run-state lives in the container's own volume at `/data/scheduler_state.json`,
never in the task dir. It tracks only recurring tasks:

```json
{"recurring": {"keep-warm.md": {"last_fired_at": "2026-09-20T08:00:00+02:00"}}}
```

One-offs need no state entry — moving the file to `done/` is their dedup.
State is keyed by filename relative to the task dir.

## Scheduler loop

A new `scheduler_loop()` coroutine (in `scheduler/loop.py`) joins the existing
`asyncio.gather` in `orchestrator._run_with_pebble()`, gated by
`CLAYDE_SCHEDULER_ENABLED`. Every `CLAYDE_SCHEDULER_INTERVAL_S` (default 30) it:

1. Discovers and parses `/tasks/*.md` (`scheduler/tasks.py`); skips `done/`.
2. Evaluates due-ness (below).
3. For each due task, builds a `Job(origin="scheduler", text=<prompt>)` and
   enqueues it into the shared `JobQueue`. If the queue is full, log and leave
   dedup uncommitted so it retries next tick.
4. Commits dedup **at enqueue time**: recurring → write `last_fired_at`;
   one-off → move file to `done/<epoch>-<name>`.

### Due-ness, missed runs, lateness

- **Recurring:** each tick, compute the most recent scheduled occurrence ≤ now
  via `croniter` in the task's tz. Fire iff that occurrence is later than the
  stored `last_fired_at`; then set `last_fired_at` to it. Fires each occurrence
  exactly once, never backfills.
  - **First encounter (no state):** baseline `last_fired_at` to the last past
    occurrence *without* firing. A "daily 08:00" task created at 15:00 first
    fires the next day, not immediately.
- **One-off:** fire when `now ≥ at` and the file is still in the active set.
- **Missed while down:** both conditions above stay true after downtime, so an
  overdue task fires once on the first tick after startup. No backfill.
- **Lateness annotation:** when the fire time is more than 60 s past the
  scheduled time, prepend to the prompt text:
  `[This task was scheduled for <local time> and is running <duration> late.]`
  So Claude can phrase a late reminder appropriately. Applies to both one-off
  overdue firing and a late recurring tick.

## Notification

No per-task notify field. Behaviour is by origin:

- **Scheduler job, success:** the framework emits **no** ntfy. Any intentional
  notification is the task's own responsibility — its prompt asks the agent to
  notify (e.g. "notify me: ..."), which the agent does via the mounted
  `ntfy-ping` skill. keep-warm, whose prompt says nothing about notifying, is
  therefore silent on success.
- **Scheduler job, failure** (timeout, usage limit, CLI error, auth error,
  worker crash): the framework emits its ntfy, because a run that didn't finish
  cannot self-report. keep-warm thus stays silent day-to-day but shouts when
  the login lapses (an auth error) — the one signal it exists to surface.
- **Pebble job:** unchanged — framework ntfy on every outcome.

Implementation: `process_job` skips the **success-branch** `_notify` when
`job.origin == "scheduler"`; every failure branch notifies as it does today.
No changes to `Job`, `NotificationPayload`, or `extract_notification_payload`
for notification purposes.

Dependency: an intentional notification needs the `ntfy-ping` skill reachable
(satisfied by the whole-library mount below) and pointed at the right topic —
`ntfy-ping`'s topic must be reconciled with `CLAYDE_NTFY_TOPIC`, or the task
prompt must target the correct topic. Verify during implementation.

## Skill exposure

Mount the whole personal skill library so scheduled tasks and Pebble commands
can use it. Add to `docker-compose.yml`, `clayde` service:

```
- ~/knowledge_base/skills:/skills/kb:ro
```

**Discovery change** (`service/skills.py`): KB skills are directory-based
(`<skill>/SKILL.md` plus reference/example `.md` files); discovery currently
treats every `*.md` under `/skills` as a skill candidate. Change
`discover_skills` to consider only `SKILL.md` files and flat top-level `*.md`
(the builtin `ping.md` format), silently ignoring other `.md`. Without this,
discovery logs the ~47 reference files as malformed on every job tick. Name
de-duplication and builtin-override ordering are unchanged.

**Safety:** each skill dir carries its own credentials (api-email, api-gcal,
api-azure, api-ionos, and others), so the whole tree becoming reachable is a
real capability grant. It is gated by the auto permission mode (below), not by
omission. This is an accepted, classifier-mediated risk, not a hard boundary.

## Permission mode

Replace `--dangerously-skip-permissions` in the shared runner
(`service/runner.py`, `invoke_claude`) with:

```
--permission-mode auto --permission-prompts none
```

Auto mode's classifier auto-approves safe actions and denies dangerous ones
(credential reads, destructive commands); `--permission-prompts none` means any
action the classifier would escalate to a human is auto-denied, since headless
runs have no one to ask. Verified against Claude CLI 2.1.278
(`--permission-mode` choices include `auto`; `--permission-prompts` includes
`none`).

Applies to **both** scheduler and Pebble jobs (shared runner) — a hardening of
the existing webhook, at the cost that some commands that ran under
skip-permissions may now be denied. It is a classifier, not a sandbox: it
lowers blast radius but does not hard-guarantee against a mutating action
disguised as benign.

## Prompt framing

`service/skills.py` prompt builders are parametrised by origin:

- **System prompt** opening line: "executing a scheduled task" for
  `origin="scheduler"`, unchanged "request from a Pebble watch" for `pebble`.
  The skills catalogue, timeout budget, and JSON-tail requirement are shared.
- **User prompt:** scheduler builds `<lateness note?>\n<body>` rather than the
  Pebble `(timestamp N)\n<text>`.

## Settings (new, `CLAYDE_` prefix)

| Key | Default | Purpose |
|-----|---------|---------|
| `CLAYDE_SCHEDULER_ENABLED` | `false` | Activate the scheduler loop |
| `CLAYDE_SCHEDULER_DIR` | `/tasks` | In-container task directory |
| `CLAYDE_SCHEDULER_INTERVAL_S` | `30` | Tick interval |
| `CLAYDE_SCHEDULER_TZ` | `Europe/Berlin` | Default timezone for tasks |
| `CLAYDE_SCHEDULER_TIMEOUT` | `300` | Per-run wall-clock budget (mirrors `pebble_timeout`) |

`docker-compose.yml`: add `- ~/clayde-tasks:/tasks` (read-write) and
`- ~/knowledge_base/skills:/skills/kb:ro` to the `clayde` service.

## Dependency

Add `croniter` to `[project.dependencies]` in `pyproject.toml`. Chosen over a
hand-rolled cron parser: standard, small, correct on edge cases (DST, day/dow
interplay). `zoneinfo` is stdlib.

## Module & file layout

```
src/clayde/
  service/            # NEW — shared job execution (moved from webhook/)
    __init__.py       #   re-exports Job, JobQueue, QueueFullError, worker_loop
    queue.py          #   Job, JobQueue, QueueFullError
    worker.py         #   worker_loop, process_job (scheduler success = no ntfy)
    runner.py         #   invoke_claude (auto permission mode), extract_notification_payload
    notify.py         #   send_ntfy, NotificationPayload
    skills.py         #   discover_skills (SKILL.md-aware), build_system_prompt(origin,...), build_user_prompt
  webhook/
    __init__.py
    app.py            #   HTTP endpoint only (imports Job/JobQueue from service)
    auth.py
  scheduler/          # NEW
    __init__.py
    tasks.py          #   ScheduledTask model, parse_task_file, discover_tasks
    state.py          #   load_state, save_state, dedup helpers
    loop.py           #   scheduler_loop

tests/
  service/            # moved webhook execution tests
  scheduler/          # NEW — tasks, state, loop
  webhook/            # app + auth tests
```

## Testing

`uv run pytest`. New / changed coverage:

- **tasks.py:** valid cron / valid at / both keys (reject) / neither (reject) /
  bad cron / bad tz / `enabled: false` / body extraction.
- **state.py:** load/save round-trip, missing file, dedup key by relative path.
- **loop.py:** recurring first-encounter baseline (no fire); recurring fires
  once per occurrence; recurring single-fire after simulated downtime; one-off
  fires and moves to `done/`; one-off not re-fired; lateness annotation present
  when late and absent when on time; queue-full leaves dedup uncommitted.
- **worker.py:** `origin="scheduler"` success emits **no** ntfy; each scheduler
  failure branch (timeout / usage limit / CLI error / auth / worker crash)
  **does** emit ntfy; `origin="pebble"` still notifies on every outcome.
- **skills.py discovery:** `SKILL.md` under a skill dir is matched; reference
  `.md` files are ignored without a WARNING; flat builtin `ping.md` matched;
  name collision de-dup and builtin-override ordering unchanged.
- **runner.py:** the CLI argv contains `--permission-mode auto` and
  `--permission-prompts none`, and no longer contains
  `--dangerously-skip-permissions`.
- **Regression:** moved Pebble tests still pass under `tests/service/`;
  `origin="pebble"` framing and always-notify unchanged.

## Bootstrapping caveat

The scheduler presumes the CLI login is established. The keep-warm task keeps
the lineage alive once running, but the login must be created once and not left
to lapse before the first keep-warm tick. Document this in the README alongside
the scheduler setup.
