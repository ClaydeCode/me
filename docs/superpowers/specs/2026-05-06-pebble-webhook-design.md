# Pebble Webhook + Skill Framework — Design

**Date:** 2026-05-06
**Status:** Approved (design phase)

## Goal

Extend Clayde with an HTTP webhook endpoint that receives speech-to-text
messages from a Pebble watch app and dispatches them to Claude with a
catalog of available *skills*. Provide the framework for skills to be added
later (markdown files mounted from the host); no skills are populated as
part of this work.

## Non-goals

- Skill content (note-taking, calendar, etc.) — deferred.
- Knowledge repo location/format — deferred.
- Google Calendar CLI auth inside container — deferred.
- Reply channel back to the user (Pebble notification, email, etc.).
  Fire-and-forget only.
- Per-request session resumption across messages.
- Retrying on Claude usage limits.
- Persisting queued jobs across container restart.
- Multi-user / multi-tenant.

## Inputs and constraints

- The Pebble app sends `POST` with body `{"text": str, "timestamp": int}`
  and supports a configurable bearer token.
- The app is fire-and-forget; it does not display the webhook response to
  the user. A 200 response is sufficient.
- Cost-sensitive: must use the Claude Code CLI backend, not the Anthropic
  API.
- The text is speech-to-text output and may contain transcription errors.
  Phonetically similar phrases must be considered when matching intent to
  skills.
- Existing GitHub poll loop must continue to function unchanged.

## Architecture

Single container, single Python process. asyncio runtime hosts both:

- the existing poll loop (`run_loop`) as a background asyncio task, and
- a FastAPI app served by uvicorn on an internal port (default 8080).

Traefik runs as a separate compose service. It reads docker labels on the
`clayde` service and routes `https://<host>/webhook/pebble` to
`clayde:8080`. Traefik also handles Let's Encrypt certificate issuance and
renewal via the HTTP-01 challenge. The existing Watchtower sidecar is
unchanged.

### Request flow

```
[Pebble app] ──HTTPS──▶ [Traefik] ──HTTP──▶ [clayde:8080 FastAPI]
                                                 │
                                                 ▼
                                       [in-memory asyncio.Queue]
                                                 │
                                                 ▼
                                       [worker coroutine: claude CLI]
                                                 │
                                                 ▼
                                       [skill execution]
```

The webhook handler verifies the bearer token, validates the payload,
enqueues the job, and returns 200. A single worker coroutine pops jobs and
runs the Claude CLI serially. A single worker (not a pool) avoids
concurrent `claude` processes and git races on shared repos.

## Webhook endpoint

### Routes

| Method | Path              | Auth        | Purpose                  |
|--------|-------------------|-------------|--------------------------|
| POST   | `/webhook/pebble` | Bearer      | Receive Pebble message   |
| GET    | `/health`         | None        | Liveness check (Traefik) |

All other paths return 404. No admin endpoints are exposed.

### Authentication

`Authorization: Bearer <token>` header. The token is compared in
constant time against `CLAYDE_PEBBLE_TOKEN`. Missing or wrong token
returns 401.

### Payload

```python
class PebblePayload(BaseModel):
    text: str
    timestamp: int  # unix seconds
```

Bad shape returns 422 (FastAPI default behavior).

### Responses

| Status | Body                                  | Condition         |
|--------|---------------------------------------|-------------------|
| 200    | `{"queued": true, "id": "<uuid>"}`    | Accepted          |
| 401    | `{"detail": "unauthorized"}`          | Bad/missing token |
| 422    | (FastAPI default)                     | Bad payload       |
| 503    | `{"queued": false, "reason": "full"}` | Queue at capacity |

### Queueing

`asyncio.Queue` with `maxsize = CLAYDE_PEBBLE_QUEUE_MAX` (default 100).
In-memory only; queued jobs are lost on container restart by design.
Enqueue is non-blocking: if `put_nowait` raises `QueueFull`, the handler
returns 503.

## Skill mechanism

### Skill format

A skill is a single markdown file with frontmatter:

```markdown
---
name: add-note
description: Append a markdown note to the knowledge repo. Use when the user
  wants to remember, jot down, or save something.
---

(Body: full instructions for Claude. Paths to use, conventions, examples.)
```

### Discovery

`CLAYDE_SKILL_DIRS` is a colon-separated list of directories inside the
container. At startup AND on each request, every directory is scanned
recursively for `*.md` files. Re-scanning per request is cheap and lets
new skills be hot-added without restarting the container.

The compose file mounts host skill directories read-only:

```yaml
volumes:
  - ./data:/data
  - ~/skills/personal:/skills/personal:ro
  - ~/skills/shared:/skills/shared:ro
environment:
  - CLAYDE_SKILL_DIRS=/skills/personal:/skills/shared
```

### Conflict resolution

If two skill files share a `name` field:

1. Log a warning naming both paths.
2. The first-discovered skill wins.
3. Discovery order is deterministic: directories in `CLAYDE_SKILL_DIRS`
   order, then alphabetical filename order within each directory.

### System prompt construction

A fresh system prompt is built per request and looks like:

```
You are Clayde, acting on a voice command from the user via a Pebble watch.

The text you receive is speech-to-text output. It MAY contain transcription
errors. Consider phonetically similar words and the most likely intent —
e.g. "calendar" might arrive as "colander". Use judgement.

Available skills:

- add-note: Append a markdown note to the knowledge repo. Use when the user
  wants to remember, jot, or save something.
- add-calendar-event: ...
- (one line per discovered skill)

To use a skill, read the full file at the path noted, then follow it.
Skill files:

- add-note: /skills/personal/add-note.md
- add-calendar-event: /skills/shared/add-calendar-event.md

If no skill matches, respond with exactly "No matching skill" and stop. Do
not invent or improvise.

User said (timestamp <ts>):
<text>
```

The Pebble flow uses this prompt instead of the standard CLAUDE.md
identity prompt; the Pebble run is not "Clayde the GitHub agent", it is
"Clayde executing a voice command".

### Working directory

Per-request scratch directory at `/tmp/clayde-pebble-<jobid>` (mkdtemp,
cleaned after the run). Skills that need to operate in a specific repo or
filesystem location `cd` themselves per their own instructions. The
framework makes no assumption about repo context.

## Claude invocation

A new helper extends the existing CLI backend:

```python
def invoke_claude_pebble(system_prompt: str, user_text: str, cwd: str) -> str
```

Differences from the existing CLI invocation used for issue tasks:

- System prompt is the freshly built skill catalog, passed via
  `--append-system-prompt` along with a base prompt that overrides the
  issue-handling identity. CLAUDE.md is *not* injected.
- Always a fresh CLI session. No `--resume` flag. (One-shot per request.)
- Working directory is the per-request scratch directory.
- Same `UsageLimitError` detection as today.
- Same OTel cost tracking (returns `cost_eur=0.0` for the CLI backend).
- Timeout: `CLAYDE_PEBBLE_TIMEOUT` seconds (default 600).

stdout/stderr are captured and attached to the process span. The CLI's
final `result` text is parsed from the JSON output. If the result is
exactly `"No matching skill"`, this is recorded as
`pebble.skill = none`, `pebble.success = true` — an intentional no-op,
not a failure.

### Failure modes

| Cause                | Handling                                       |
|----------------------|------------------------------------------------|
| Timeout              | Log + OTel error; no retry                     |
| `UsageLimitError`    | Log + OTel error; no retry                     |
| Skill execution fail | Claude reports it in stdout; logged + OTel err |
| Worker exception     | Caught at worker boundary; loop survives       |

The user can simply press the Pebble button again to retry.

## Observability

OpenTelemetry spans (exported to existing `traces.jsonl` via
`FileSpanExporter`):

- `clayde.pebble.enqueue` — attributes: `pebble.job_id`,
  `pebble.timestamp`, `pebble.text`, `pebble.text_len`,
  `http.status_code`.
- `clayde.pebble.process` — attributes: `pebble.job_id` (cross-ref),
  `pebble.skill`, `pebble.duration_ms`, `pebble.success`,
  `error.type`/`error.message` if applicable. The full `pebble.text` is
  also included on the process span for self-contained traces.

Standard logging includes `job_id` in every line for log↔trace
cross-reference. Logger name: `clayde.webhook` and `clayde.webhook.worker`.
Per the user's preference, full text is logged (this deployment is
single-user; no privacy redaction needed).

## Configuration

New environment variables in `data/config.env`:

| Key                        | Purpose                              | Default                     |
|----------------------------|--------------------------------------|-----------------------------|
| `CLAYDE_PEBBLE_ENABLED`    | Mount webhook routes / start uvicorn | `false`                     |
| `CLAYDE_PEBBLE_TOKEN`      | Bearer token for `/webhook/pebble`   | (required when enabled)     |
| `CLAYDE_PEBBLE_PORT`       | Internal HTTP port                   | `8080`                      |
| `CLAYDE_PEBBLE_TIMEOUT`    | CLI timeout (seconds)                | `600`                       |
| `CLAYDE_PEBBLE_QUEUE_MAX`  | Queue capacity                       | `100`                       |
| `CLAYDE_SKILL_DIRS`        | Colon-separated skill dirs           | (required when enabled)     |
| `CLAYDE_PEBBLE_HOST`       | Public hostname (Traefik routing)    | (required when enabled)     |

`config.env.template` is updated with the new keys.

## Deployment

`docker-compose.yml` gains a Traefik service and routing labels on the
`clayde` service:

```yaml
services:
  traefik:
    image: traefik:v3
    restart: unless-stopped
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --entrypoints.websecure.address=:443
      - --entrypoints.web.address=:80
      - --certificatesresolvers.le.acme.email=${CLAYDE_GIT_EMAIL}
      - --certificatesresolvers.le.acme.storage=/letsencrypt/acme.json
      - --certificatesresolvers.le.acme.httpchallenge=true
      - --certificatesresolvers.le.acme.httpchallenge.entrypoint=web
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./data/letsencrypt:/letsencrypt
    labels:
      - "com.centurylinklabs.watchtower.enable=true"

  clayde:
    # ...existing config...
    expose:
      - "8080"
    volumes:
      - ./data:/data
      - ~/.claude/.credentials.json:/home/clayde/.claude/.credentials.json
      - ~/skills:/skills:ro
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
      - "traefik.enable=true"
      - "traefik.http.routers.clayde.rule=Host(`${CLAYDE_PEBBLE_HOST}`) && PathPrefix(`/webhook`)"
      - "traefik.http.routers.clayde.entrypoints=websecure"
      - "traefik.http.routers.clayde.tls.certresolver=le"
      - "traefik.http.services.clayde.loadbalancer.server.port=8080"
```

The `clayde` console entry point is updated. When
`CLAYDE_PEBBLE_ENABLED=true`, the entry point composes the existing loop
coroutine and a uvicorn server using `asyncio.gather`. When the flag is
false (or unset), behavior is identical to today.

## Code layout

New files:

```
src/clayde/webhook/
  __init__.py
  app.py        # FastAPI app factory; routes; pydantic models
  auth.py       # bearer token verification (constant-time)
  queue.py      # asyncio.Queue wrapper + worker coroutine
  skills.py     # discover skills, build catalog and system prompt
  runner.py     # invoke_claude_pebble() — CLI invocation
```

Modified files:

- `src/clayde/claude.py` — extract a reusable CLI invocation primitive
  used by both existing tasks and the new pebble runner. No behavior
  change for existing callers.
- `src/clayde/orchestrator.py` — `run_loop` becomes async; new entry
  point composes loop coroutine + uvicorn server when
  `CLAYDE_PEBBLE_ENABLED=true`.
- `src/clayde/config.py` — add new pebble settings to `Settings`.
- `pyproject.toml` — add `fastapi`, `uvicorn[standard]` dependencies.
- `docker-compose.yml` — add Traefik service and routing labels.
- `config.env.template` — document new env vars.
- `CLAUDE.md`, `README.md` — document the endpoint, skill format, env
  vars.

## Tests

Unit and integration tests under `tests/`:

- `test_webhook_auth.py` — bearer token accept/reject, missing header.
- `test_webhook_queue.py` — enqueue under cap, reject (503) over cap.
- `test_webhook_skills.py` — skill discovery, duplicate-name handling,
  catalog and system prompt construction.
- `test_webhook_app.py` — end-to-end with a mocked
  `invoke_claude_pebble`.

Existing GitHub-loop tests must continue to pass unchanged.

## Success criteria

1. `POST /webhook/pebble` with valid token + payload returns 200, job
   queued.
2. Bad token → 401. Bad payload → 422. Queue full → 503.
3. A test skill (e.g. `echo-skill.md` that writes the input text to a
   file) executes end-to-end in a dev environment.
4. OTel spans show `clayde.pebble.enqueue` and `clayde.pebble.process`
   cross-referenced by the same `pebble.job_id`.
5. The existing GitHub poll loop and its tests are unaffected.
6. Traefik issues a Let's Encrypt certificate and the HTTPS endpoint is
   reachable from the public internet.
