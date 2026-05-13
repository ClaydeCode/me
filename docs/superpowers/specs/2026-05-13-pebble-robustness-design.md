# Pebble Robustness, ntfy Feedback, Multi-Skill, KB Default — Design

**Date:** 2026-05-13
**Status:** Approved (design phase)
**Supersedes (partially):** `2026-05-06-pebble-webhook-design.md` — that spec is now the historical record of phase 1 (merged in PR #69). This doc covers phase 2 deltas.

## Goal

Make the Pebble webhook reliable enough to trust day-to-day. Three deltas on top of the phase-1 spec:

1. **ntfy completion notification on every call.** Success or fail, every webhook call ends with one notification on `https://ntfy.sh/<topic>`. Claude emits the title/body content in a structured JSON tail. Pre-Claude failures (queue full, worker dead) also notify, with framework-built content.
2. **Multi-skill freedom.** The single-skill cap from phase 1 is removed. Claude composes any number of skills in one CLI session.
3. **Knowledge base = default working target.** `~/knowledge_base/` is mounted RW into the container and is the per-request `cwd`. Voice commands without an explicit skill match are handled by Claude's judgement — typically captured into the KB inbox.

## Non-goals

- ntfy auth / topic privacy. The default topic `7yuau0vyes` is public on ntfy.sh; anyone with the string can read transcripts. Accepted.
- Reply channel back to the Pebble watch. Still fire-and-forget from Pebble's side; ntfy is the asynchronous reply channel.
- Retry on Claude usage limits or any other failure. Single attempt per call; fail notification only.
- Git inside the container. Knowledge base sync is handled by Syncthing running on the host. The container performs no `git` operations against the KB.
- Per-message Claude session resumption. Each call is a fresh CLI session.
- Job ID echo in the notification (deliberately omitted — operators rely on traces.jsonl when needed).

## Inherited from phase 1, unchanged

FastAPI app on port 8080, Traefik in front terminating TLS, bearer-token auth, asyncio queue with `maxsize = CLAYDE_PEBBLE_QUEUE_MAX` (default 100), a single worker coroutine, the Claude Code CLI backend (mandatory — cost), OpenTelemetry spans `clayde.pebble.enqueue` and `clayde.pebble.process`.

## Notification dispatch

### Module

New `src/clayde/webhook/notify.py`. Public surface:

```python
class NotificationPayload(BaseModel):
    title: str = Field(..., max_length=40)
    body: str = Field(..., max_length=300)
    success: bool

async def send_ntfy(*, title: str, body: str, success: bool) -> None
```

### Transport

`httpx.AsyncClient`, POST to `{CLAYDE_NTFY_BASE_URL}/{CLAYDE_NTFY_TOPIC}`. Message body is the plain text. Headers:

- `Title: <title>`
- `Priority: 3` on success, `5` on failure
- `Tags: white_check_mark` on success, `rotating_light` on failure

Timeout: `CLAYDE_NTFY_TIMEOUT_S` seconds (default 10). Best-effort: failure of the ntfy POST is logged and OTel-annotated, but never raised — notification is feedback, not transactional.

### When notifications fire

Notifications fire on **terminal events only**. One notification per webhook call.

| Site | Trigger | Title | Body |
|------|---------|-------|------|
| Worker, CLI success + JSON parsed | normal completion | `payload.title` | `payload.body` |
| Worker, CLI success + JSON missing/malformed | parse fallback | `Pebble: done (no summary)` | first 300 chars of CLI `result` |
| Worker, CLI returns `success: false` JSON | claude-reported failure | `payload.title` | `payload.body` (fail priority/tags) |
| Worker, `InvocationTimeoutError` | hard timeout (asyncio.wait_for) | `Pebble: timeout` | `ran <timeout>s+` |
| Worker, `UsageLimitError` | Anthropic rate-limit | `Pebble: rate-limited` | `try again later` |
| Worker, `CliInvocationError` (new) | CLI non-zero exit, not auth/limit | `Pebble: failed` | stderr tail (≤300 chars) |
| Worker, `RuntimeError` from auth | CLI authentication failed | `Pebble: auth error` | `claude CLI auth` |
| Worker, unexpected exception | worker error | `Pebble: failed` | exception class name |
| FastAPI handler, `QueueFull` | queue saturated (503) | `Pebble: queue full` | text snippet + queued count |
| FastAPI handler, worker dead | worker task crashed | `Pebble: worker dead` | exception class name |

Notifications do **not** fire on:

- `401 Unauthorized` — abuse / flood vector. ntfy.sh has rate limits; an attacker spamming bad-token requests must not generate notifications.
- `422 Unprocessable Entity` — Pebble-side bug or unrelated client. Not a voice command.
- `404 Not Found` — unknown route.

### Worker boundary invariant

Every code path through the worker emits **exactly one** `send_ntfy` call. Enforced by structuring `worker._handle_job` as a top-level try/except wrapping the entire job:

```python
try:
    payload = await self._run_one(job)
    await send_ntfy(title=payload.title, body=payload.body, success=payload.success)
except UsageLimitError:
    await send_ntfy(title="Pebble: rate-limited", body="try again later", success=False)
except InvocationTimeoutError:
    await send_ntfy(title="Pebble: timeout", body=f"ran {settings.pebble_timeout}s+", success=False)
except CliInvocationError as exc:
    await send_ntfy(title="Pebble: failed", body=_tail(exc.stderr, 300), success=False)
except RuntimeError as exc:
    # auth failures raise RuntimeError from existing runner
    await send_ntfy(title="Pebble: auth error", body=str(exc)[:300], success=False)
except Exception as exc:
    log.exception("worker error")
    await send_ntfy(title="Pebble: failed", body=type(exc).__name__, success=False)
```

### Runner change required

Phase-1 `invoke_claude_pebble` does not raise on CLI non-zero exit unless the stderr matches an auth or usage-limit pattern — it logs and returns the output text. Phase 2 introduces a new exception `CliInvocationError(stderr: str)` in `clayde.claude` and modifies the runner to raise it when `proc.returncode != 0 or is_error` and the error is not recognized as auth/limit. This is required so the worker can distinguish CLI failure from success — otherwise the JSON-tail parser would treat a failed run as a "no summary" success.

The worker loop itself catches everything and survives — same invariant as phase 1.

## Claude invocation: prompt and JSON contract

### System prompt (replaces the phase-1 single-skill section)

```
You are Clayde, executing a voice command from the user via a Pebble watch.

The text is speech-to-text output. It MAY contain transcription errors.
Consider phonetically similar words and the most likely intent — e.g.
"calendar" might arrive as "colander".

Default working target: /home/clayde/knowledge_base (mounted RW, synced via Syncthing).
If the command implies "remember this", "note", "save", "log", "capture",
write a file there. No git operations — Syncthing handles sync.

Available skills (read the full file before using):

- <name>: <description>          → <path>
- (one line per discovered skill)

Skills are suggestions, not constraints. Use as many as the command needs,
in any order. If no skill fits, use your judgement — capture into the
knowledge base inbox or answer directly.

When done, your LAST output MUST be a single fenced JSON block:

```json
{"title": "<short, ≤40 chars>", "body": "<message, ≤300 chars>", "success": true|false}
```

`success: false` only if you could not carry out the user's intent.
Anything before the JSON block is your working narrative and is ignored.

User said (timestamp <ts>):
<text>
```

CLAUDE.md identity prompt remains bypassed (Pebble runs are not "Clayde the GitHub agent").

### Skill catalog

Recursive walk of `/skills/`. Discovery rules from phase 1 unchanged:

- Alphabetical-by-path order, first-wins on duplicate `name`, warning logged.
- One markdown file per skill with `name` + `description` frontmatter.

New: built-in skills baked into the image at `/skills/builtin/`. Phase 2 ships a single built-in:

- `ping`: trivial health-check skill. Claude responds with a friendly pong; notification title `pong`, body shows container uptime if `/proc/uptime` readable, else `alive`. Lets the user verify the full Pebble → Traefik → Clayde → ntfy chain from the watch.

### Working directory

Per-request `cwd` = `CLAYDE_KB_PATH` (default `/home/clayde/knowledge_base`). The per-request scratch directory from phase 1 is **removed**; the KB is the workspace. Skills that need to operate elsewhere `cd` themselves per their own instructions.

### Output parser

`src/clayde/webhook/runner.py`:

1. Capture CLI stdout via `--output-format json` (existing). Extract the `result` field.
2. Find the **last** ` ```json ... ``` ` fenced block in `result`. Parse via `NotificationPayload`.
3. On parse failure or missing block: return `NotificationPayload(title="Pebble: done (no summary)", body=result[:300], success=True)`. The call still completed — only the summary is missing.
4. On `CalledProcessError` / `TimeoutExpired` / `UsageLimitError`: parser is not reached; failure-path branches in the worker fire.

## Configuration

### New env vars

| Key | Default | Purpose |
|-----|---------|---------|
| `CLAYDE_NTFY_TOPIC` | `7yuau0vyes` | ntfy.sh topic |
| `CLAYDE_NTFY_BASE_URL` | `https://ntfy.sh` | override for future self-host |
| `CLAYDE_NTFY_TIMEOUT_S` | `10` | best-effort POST timeout |
| `CLAYDE_KB_PATH` | `/home/clayde/knowledge_base` | in-container KB path; Pebble `cwd` |

### Changed defaults

| Key | Old | New | Reason |
|-----|-----|-----|--------|
| `CLAYDE_PEBBLE_TIMEOUT` | `600` | `300` | Pocket-dial / runaway protection. KB tasks finish in seconds; multi-skill chains fit comfortably. |

`config.env.template` updated with all new keys.

## Deployment

### docker-compose.yml delta

`clayde` service volumes:

```yaml
volumes:
  - ./data:/data
  - ~/.claude/.credentials.json:/home/clayde/.claude/.credentials.json
  - ~/knowledge_base:/home/clayde/knowledge_base   # RW, no :ro
  - ~/skills/personal:/skills/personal:ro
  - ~/skills/shared:/skills/shared:ro
```

No Syncthing service is added to compose. Syncthing runs on the host and already keeps `~/knowledge_base/` in sync with other devices. The container only sees the local mount.

UID/GID alignment: the host `~/knowledge_base/` must be writable by the in-container `clayde` user. Verified during step 9 of the rollout.

### Dockerfile delta

```dockerfile
COPY src/clayde/skills_builtin/ /skills/builtin/
```

Built-in skills are part of the image, read-only by virtue of the image layer. No additional mount required.

## Code layout

### New files

```
src/clayde/webhook/
  notify.py              # send_ntfy(), NotificationPayload model
src/clayde/skills_builtin/
  ping.md                # baked-in built-in skill
tests/
  test_webhook_notify.py
  test_pebble_e2e.py     # in-process FastAPI + fake CLI + fake ntfy (respx)
```

### Modified files

```
src/clayde/claude.py           # new CliInvocationError exception class
src/clayde/webhook/runner.py   # raise CliInvocationError on rc!=0; KB cwd; no scratch dir;
                               # new extract_notification_payload(result) helper
src/clayde/webhook/skills.py   # builtin path discovery; new system-prompt builder
src/clayde/webhook/worker.py   # send_ntfy on every terminal outcome (calls runner parser)
src/clayde/webhook/app.py      # send_ntfy on QueueFull / worker-dead
src/clayde/config.py           # new Settings fields
config.env.template            # document new env vars
docker-compose.yml             # KB volume mount
Dockerfile                     # COPY skills_builtin
CLAUDE.md                      # KB default, multi-skill, ntfy
README.md                      # same
```

No new top-level dependency: `httpx` is already pulled by the FastAPI/uvicorn stack.

## Observability

`clayde.pebble.process` gains a new attribute:

- `pebble.outcome` (string enum): `success | claude_fail | parse_fallback | timeout | rate_limited | cli_error | worker_error`

`clayde.pebble.notify` events attached to the process span:

- `notify.success` (bool)
- `notify.http_status` (int, when reachable)

The single `pebble.outcome` enum classifies every terminal path, which is the diagnostic anchor for "works only sometimes" — operators trace by outcome distribution rather than spelunking logs.

## Tests

### New

- `test_webhook_notify.py`
  - `send_ntfy` builds correct URL, headers (Title, Priority 3 vs 5, Tags white_check_mark vs rotating_light), body.
  - httpx network error → swallowed, OTel marked, no raise.
- `test_webhook_runner_parse.py`
  - Last JSON block extracted from multi-block stdout.
  - Truncated / malformed JSON → fallback payload `Pebble: done (no summary)`, `success: True`.
  - `success: false` JSON honored (parsed payload propagated).
  - Length limits clamp via pydantic.
- `test_skills_builtin.py`
  - `/skills/builtin/ping.md` discovered alongside host mounts.
  - Builtin + host duplicate name → alphabetical winner, warning logged.
- `test_pebble_e2e.py` (integration)
  - `httpx.AsyncClient` against in-process app, fake CLI returning canned stdout with JSON tail, fake ntfy server (`respx`) captures the POST.
  - End-to-end: POST `/webhook/pebble` → queue → worker → fake CLI → fake ntfy POST received with correct headers.

### Modified

- `test_webhook_worker.py` — every terminal branch asserts `send_ntfy` called once with expected title prefix and `success` flag. Fake `send_ntfy` via fixture.
- `test_webhook_app.py` — `QueueFull` path asserts `send_ntfy` fired before 503 returned.
- `test_webhook_skills.py` — system-prompt builder no longer contains "AT MOST ONE skill"; contains "as many as the command needs"; contains the KB default-target line; contains the JSON-tail contract verbatim.

Existing GitHub-loop tests continue to pass unchanged.

## Implementation order

1. Bump `CLAYDE_PEBBLE_TIMEOUT` default 600→300. Add `CLAYDE_NTFY_*` + `CLAYDE_KB_PATH` to `Settings`. Update `config.env.template`.
2. Add `webhook/notify.py` + `NotificationPayload` model + unit tests. No call sites yet.
3. Rewrite system-prompt builder in `skills.py`: drop single-skill cap, add KB-default line, add JSON-tail contract. Update tests.
4. Add JSON-tail parser + fallback in `runner.py`. Unit tests.
5. Wire `send_ntfy` into `worker.py` terminal branches. Update worker tests.
6. Wire `send_ntfy` into `app.py` (queue-full / worker-dead). Update app tests.
7. Switch runner `cwd` to `CLAYDE_KB_PATH`. Remove scratch-dir code.
8. Add `skills_builtin/ping.md`, Dockerfile `COPY`, builtin discovery test.
9. Update `docker-compose.yml`: add `~/knowledge_base:/home/clayde/knowledge_base` mount. Verify UID/GID on host KB.
10. Update `CLAUDE.md` + `README.md`: new env vars, KB default, multi-skill, ntfy.
11. End-to-end integration test.
12. Open PR to `main` from `clayde/pebble-robustness`.

## Risks

- **JSON-tail contract drift.** Future Claude versions could alter formatting. Mitigated by the parser fallback (still notifies, still records `success: True`) and by `pebble.outcome = parse_fallback` surfacing the rate in OTel — operators see drift before it becomes silent breakage.
- **KB mount permissions.** Host UID/GID must align with the container `clayde` user, else Claude can't write. Caught explicitly in rollout step 9.
- **Pocket-dial cost.** Reduced timeout (300s) caps a runaway run. No token-level cap; CLI backend cost-tracking returns `0.0` today, so a billing-level guardrail is out of scope.

## Success criteria

1. A `ping` voice command from the watch produces a `pong` notification within the timeout, every time.
2. A "remember X" voice command writes a markdown file under `~/knowledge_base/inbox/` and produces a success notification naming the file.
3. A queue-full condition (forced in tests) emits an ntfy notification before the 503 response.
4. A simulated CLI timeout emits a `Pebble: timeout` notification.
5. ntfy.sh outage does not crash the worker — the call still records `clayde.pebble.process` with `outcome=success` and `notify.success=false`.
6. `pebble.outcome` is set on every `clayde.pebble.process` span; outcome distribution is queryable from `traces.jsonl`.
7. Existing GitHub poll loop and tests unaffected.
