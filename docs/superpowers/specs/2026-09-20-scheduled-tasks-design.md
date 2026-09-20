# Scheduled Tasks — File-Driven Scheduler — Design

**Date:** 2026-09-20
**Status:** Proposed — awaiting review

## Goal

Give Clayde a scheduler that runs prompts on a schedule, independent of any
interactive session or the Pebble watch. Each task is one markdown file in a
host-mounted directory: frontmatter says *when* to run (a one-off timestamp or
a recurring cron expression), the body is the prompt. Due tasks are dispatched
through the existing job pipeline — a fresh Claude CLI session with `/skills/`
available, cwd = KB — and notify (or not) per the task's own policy.

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
  host dir, not the synced knowledge base. Tasks are created on the VM. (A
  future voice/webhook path that writes task files is out of scope.)
- **Sub-minute schedules.** Cron granularity is one minute.
- **A separate reminders feature.** A reminder is just a task; delivery is the
  normal outcome ntfy carrying Claude's summary.

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
    notify: str = "always"          # "always" | "on-failure" | "never" | "agent"
```

`origin` selects prompt framing. `notify` carries the per-job notification
policy (§ Notification policy). Both default to today's Pebble behaviour, so
`webhook/app.py` constructs `Job` unchanged.

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
notify: always            # optional; default "always" (see Notification policy)
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
- `notify` ∈ {`always`, `on-failure`, `never`, `agent`}; anything else → the
  file is treated as malformed.
- `enabled: false` parks a task without deleting it.
- Malformed files (missing/both schedule keys, bad cron, unknown `notify`,
  unterminated frontmatter) are logged at WARNING and skipped — same policy as
  skill discovery. No ntfy on a malformed file (would spam every tick).

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
3. For each due task, builds a `Job(origin="scheduler", notify=<task.notify>,
   text=<prompt>)` and enqueues it into the shared `JobQueue`. If the queue is
   full, log and leave dedup uncommitted so it retries next tick.
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

## Notification policy

`notify` on the `Job` controls whether the worker emits an ntfy for that job's
outcome. Pebble jobs are always `always`. Scheduler jobs take the value from
frontmatter.

| Value | Success | Failure (timeout / CLI / auth / worker error) |
|-------|---------|-----------------------------------------------|
| `always` (default) | notify | notify |
| `on-failure` | silent | notify |
| `never` | silent | silent |
| `agent` | Claude decides (see below) | notify |

`agent` semantics: the invoked Claude may include an optional `"notify"` bool
in its final JSON block:

```json
{"title": "...", "body": "...", "success": true, "notify": false}
```

- On success, the worker honours `payload.notify`.
- If the run succeeds but omits the field, default to **notify** (a lost
  decision must not silence the user).
- If the run fails before producing JSON, **notify** regardless — the agent
  can't decide if it never finished.

The `"notify"` field is documented in the system prompt **only** when the job's
policy is `agent`; for other policies any `notify` the model emits is ignored.

### Worker changes

`process_job` is refactored so each outcome branch produces a
`(title, body, success)` triple and a single guarded notify runs at the end,
applying the policy table, instead of the scattered `_notify` calls it has now.
`extract_notification_payload` / `NotificationPayload` gain an optional
`notify: bool | None` parsed from the JSON tail.

## Prompt framing

`service/skills.py` prompt builders are parametrised by origin:

- **System prompt** opening line: "executing a scheduled task" for
  `origin="scheduler"`, unchanged "request from a Pebble watch" for `pebble`.
  The skills catalogue, timeout budget, and JSON-tail requirement are shared.
  When policy is `agent`, the scheduler system prompt additionally documents
  the optional `"notify"` field.
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

`docker-compose.yml`: add `- ~/clayde-tasks:/tasks` (read-write) to the
`clayde` service.

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
    worker.py         #   worker_loop, process_job (+ notify policy)
    runner.py         #   invoke_claude, extract_notification_payload
    notify.py         #   send_ntfy, NotificationPayload (+ optional notify field)
    skills.py         #   discover_skills, build_system_prompt(origin,...), build_user_prompt
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

`uv run pytest`. New coverage:

- **tasks.py:** valid cron / valid at / both keys (reject) / neither (reject) /
  bad cron / unknown notify / bad tz / `enabled: false` / body extraction.
- **state.py:** load/save round-trip, missing file, dedup key by relative path.
- **loop.py:** recurring first-encounter baseline (no fire); recurring fires
  once per occurrence; recurring single-fire after simulated downtime; one-off
  fires and moves to `done/`; one-off not re-fired; lateness annotation present
  when late and absent when on time; queue-full leaves dedup uncommitted.
- **worker.py:** notify policy table — each of `always` / `on-failure` /
  `never` / `agent` × (success / failure) asserts notify-called-or-not;
  `agent` success with `notify:false`, with field omitted, and failure path.
- **Regression:** moved Pebble tests still pass under `tests/service/`;
  `origin="pebble"` framing and always-notify unchanged.

## Bootstrapping caveat

The scheduler presumes the CLI login is established. The keep-warm task keeps
the lineage alive once running, but the login must be created once and not left
to lapse before the first keep-warm tick. Document this in the README alongside
the scheduler setup.
