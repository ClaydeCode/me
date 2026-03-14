# Refactoring Issues

Collected from a full read of `src/clayde/`. Grouped by theme, ordered roughly by impact.

---

## 1. Code Duplication

### 1.1 `_collect_discussion` duplicated across tasks
`plan.py:404` (`_collect_discussion_after`) and `implement.py:206` (`_collect_discussion`) are
the same function — iterate comments, find the anchor ID, collect everything after it.
**Fix:** Extract to a shared helper (e.g. `clayde/comments.py` or add to `github.py`).

NOTE: you can also make deeper module structures like `github.comments`, `github.util`, etc. if you think it makes sense.

### 1.2 `_PROMPTS_DIR` defined three times
Defined identically in `plan.py:32`, `implement.py:36`, `review.py:25`.
**Fix:** Define once in a shared location (e.g. `clayde/config.py` or `clayde/prompts.py`).

NOTE: `clayde/prompts.py` seems the right place.

### 1.3 Template loading pattern repeated in every `_build_*` function
Each builder does:
```python
template_src = (_PROMPTS_DIR / "foo.j2").read_text()
return Environment(undefined=StrictUndefined).from_string(template_src).render(...)
```
This repeats in 6 places and also reconstructs a fresh `Environment` each time.
**Fix:** One shared `_render_template(name: str, **ctx) -> str` helper.

### 1.4 Rate-limit/overload handling in `claude.py` duplicated
`invoke_claude` (lines 349–360 and 362–374) handles `RateLimitError` and `APIStatusError(529)`
with nearly identical code (commit WIP, save conversation, calculate partial cost, raise
`UsageLimitError`). Only the log message and error constructor differ.
**Fix:** Extract `_handle_usage_limit(e, ...)` helper called from both except branches.

### 1.5 `_handle_awaiting_preliminary` and `_handle_awaiting_plan` are near-identical
`orchestrator.py:84–127` and `130–171` share the same structure: check new comments → update
plan, else check approval → run next phase. Differ only in comment ID key, update phase name,
and next task.
**Fix:** Merge into one `_handle_awaiting_approval(phase, ...)` function parameterised by those
values.

### 1.6 `g.get_repo(f"{owner}/{repo}")` repeated in every `github.py` function
Every function in `github.py` calls `g.get_repo(f"{owner}/{repo}")` at least once — 15+
occurrences. Two functions (e.g. `fetch_issue_comments`, `get_issue_author`) fetch the issue
object just to access one attribute when the same issue was already fetched by the caller.
**Fix:** Add a small `_get_repo(g, owner, repo)` one-liner helper. (Longer term: pass `repo`
objects instead of `(owner, repo)` strings where the repo is already held.)

---

## 2. Magic Values

### 2.1 Magic numbers in `claude.py`
| Value | Location | Meaning |
|-------|----------|---------|
| `1800` | lines 295, 343 | Tool-loop timeout in seconds |
| `300` | lines 80, 87 | Bash command timeout in seconds |
| `8192` | line 307 | `max_tokens` for API call |

**Fix:** Module-level constants: `_TOOL_LOOP_TIMEOUT_S`, `_BASH_TIMEOUT_S`, `_MAX_TOKENS`.

NOTE: add those values to the settings with the current values as default.

### 2.2 Status strings are bare literals throughout
`"preliminary_planning"`, `"awaiting_plan_approval"`, `"pr_open"`, `"failed"`, `"done"`, etc.
appear as string literals in `orchestrator.py`, `plan.py`, `implement.py`, `review.py`.
A typo would silently break the state machine with no error.
**Fix:** An `IssueStatus` enum (or string constants) in `state.py` or a new `constants.py`.

NOTE: an enum in `state.py` is good.

### 2.3 `_EUR_PER_USD = 0.92` will silently go stale
`claude.py:31`. The comment says "update periodically" but there is no mechanism to flag it.
**Fix:** Keep the constant but add a `# Last updated: YYYY-MM` comment. Consider at minimum
logging a warning if the rate looks implausible (< 0.5 or > 2.0).

NOTE: no warning needed, but a "last updated" comment is good. Update it now and set the comment to today.

### 2.4 Retry limit `3` hardcoded in `implement.py:135`
```python
if retry_count >= 3:
```
**Fix:** `_MAX_RETRIES = 3` at module level.

NOTE: add it to settings with a default of 3.

### 2.5 Magic string `"---UPDATED_PLAN---"` in `plan.py:34`
Used as a separator in parsed Claude output. Fine to keep as a constant, but the name
`_UPDATE_PLAN_SEPARATOR` could be clearer as `_PLAN_SEPARATOR` or `_PLAN_UPDATE_MARKER`.

NOTE: this is alright, no fix needed

---

## 3. Function Complexity / Length

### 3.1 `_execute_tool` in `claude.py` (lines 69–138, ~70 lines)
One function handles bash execution and all four text-editor commands (`view`, `create`,
`str_replace`, `undo_edit`). Each branch is independent.
**Fix:** Split into `_run_bash(block, cwd)` and `_run_editor(block, cwd)` (or per-command helpers
inside `_run_editor`).

### 3.2 `invoke_claude` in `claude.py` (lines 232–395, ~164 lines)
Mixes: setup, conversation resume, the API loop, tool dispatch, rate-limit recovery, and
metrics recording.
**Fix:** Extract the inner loop into `_run_tool_loop(client, model, messages, tools, cwd,
deadline)` returning `(output, input_tokens, output_tokens)`.

NOTE: see if other elements can also be extracted to private functions with good names.

### 3.3 `implement.run()` (lines 39–149, ~111 lines)
Handles: resume check, state setup, repo prep, prompt build, Claude invocation, PR creation,
retry logic, and reviewer assignment. Each of those is a distinct concern.
**Fix:** Extract `_create_or_find_pr(...)` and `_handle_no_pr(...)` at minimum; the resume path
and normal path could also be split.

NOTE: move as many elements as possible to private functions with good names.

### 3.4 `_has_blocking_sub_issue_parents` in `github.py` (lines 157–204, ~48 lines)
Makes HTTP request, parses JSON, and then pattern-matches bodies inside a loop.
**Fix:** Extract `_fetch_timeline_events(token, owner, repo, number)` and
`_is_sub_issue_of_open_parent(events, owner, repo, number)`.

NOTE: remove the pattern matching completely and make the blocking decision only through connected issues.

---

## 4. Naming Issues

### 4.1 `_collect_discussion_after` vs `_collect_discussion` — same thing, different names
See duplication item 1.1. Pick one clear name: `collect_comments_after(comments, anchor_id)`.

### 4.2 `found_plan` flag variable in `implement.py:_collect_discussion`
```python
found_plan = False
for c in all_comments:
    if c.id == plan_comment_id:
        found_plan = True
```
`found_plan` is a generic boolean flag. Rename to `past_plan_comment` or restructure with
`itertools.dropwhile`.

### 4.3 `run()` in `plan.py` is a misleading alias
`plan.run()` (line 300) is described as "backward-compatible entry point" but just calls
`run_preliminary`. This is never called from anywhere meaningful now that the orchestrator uses
`run_preliminary` directly. If it's truly only there for the `interrupted` retry path, the
orchestrator's `task_map` should reference `run_preliminary` directly.
**Fix:** Remove `run()` from `plan.py` and update `orchestrator.py:199` to call
`plan.run_preliminary` directly.

### 4.4 `cwd = repo_path` pointless alias in `_commit_wip` (line 148)
`repo_path` is already a `str`. `cwd` is assigned to it immediately but `repo_path` is not used
again. Just use `repo_path` directly.

### 4.5 `patterns[0]` / `patterns[1]` index access in `_has_blocking_references`
`github.py:130,142` accesses a list by index right after defining it:
```python
patterns = [r"...", r"..."]
for m in re.finditer(patterns[0], ...):
for m in re.finditer(patterns[1], ...):
```
**Fix:** Named variables: `SAME_REPO_BLOCKED_PATTERN` / `CROSS_REPO_BLOCKED_PATTERN`.

NOTE: blocked decision by pattern matching should be removed anyway.

### 4.6 `entry` used for both issue state dict and loop variable
In `orchestrator.py:271` `entry = issues_state.get(url, {})`. The name `entry` is vague;
`issue_state` would match what all the task functions call it.

---

## 5. Missing Type Hints

### 5.1 `state.py` — no return types on any public function
```python
def load_state():          # returns dict
def save_state(state):     # returns None
def get_issue_state(issue_url):     # returns dict
def update_issue_state(issue_url, updates):  # returns None
```
**Fix:** Add proper annotations (`-> dict`, `-> None`, etc.).

### 5.2 `github.py` — `fetch_issue`, `fetch_issue_comments`, `fetch_comment` lack return types
Returns are untyped PyGitHub objects; at minimum note `-> Issue` / `-> list[IssueComment]`.

### 5.3 `orchestrator.py` — handler functions untyped
`_handle_new_issue(g, issue, url: str)` — `g` and `issue` have no annotations.

### 5.4 `plan.py` — `_build_*` prompt builders have incomplete signatures
`_build_thorough_prompt(g, issue, owner, repo, number, repo_path, ...)` — `g` and `issue` are
untyped.

---

## 6. Bugs / Logic Issues

### 6.1 Wrong variable in log message — `_has_blocking_references` (`github.py:135`)
```python
log.info("Issue %s/%s#%d is blocked by #%d (open)", owner, repo, ref_number, ref_number)
```
The first `%d` should be the *current issue's* number, not `ref_number`. So it would log
"Issue owner/repo#42 is blocked by #42 (open)" when the issue is actually, say, #99.
**Fix:** Pass `number` as the third argument.

### 6.2 `_format_reviews` misses header for reviews without body but with inline comments
`review.py:146–149`:
```python
header = f"Review by @{review.user.login} (state: {review.state}):"
if review.body and review.body.strip():
    parts.append(f"{header}\n{review.body}")
```
If a review has only inline comments (no body), the reviewer identity and state are never added
to `parts`. The inline comments that follow appear without attribution.
**Fix:** Always append `header`, then conditionally append body, then append inline comments.

### 6.3 `run_update` — interrupted_phase ternary is fragile (`plan.py:265`)
```python
"interrupted_phase": f"{phase}_planning" if phase == "preliminary" else "planning",
```
If `phase` is something unexpected, this silently falls through to `"planning"`.
**Fix:** Map explicitly or raise on unknown phase.

NOTE: raise on unexpected state

### 6.4 `setup_logging()` called twice when running `run_loop`
`run_loop()` (line 318) calls `setup_logging()`, then calls `main()` (line 323), which also
calls `setup_logging()` (line 244). Logging is initialised twice per cycle.
**Fix:** Remove the `setup_logging()` call from `main()` and document that callers must set up
logging first, or have `setup_logging()` be a true no-op on repeat calls (it already guards with
`_logging_initialized` in `config.py`, so this is harmless but confusing).

NOTE: `steup_logging` should be called exactly once, early, before the loop starts. No need to make it idempotent then, that is just confusing.

---

## 7. Structure / Style

### 7.1 Inline imports inside functions (`plan.py:128`, `plan.py:210`)
```python
from clayde.state import get_issue_state   # inside run_thorough()
from clayde.state import get_issue_state   # inside run_update()
```
Violates PEP 8 and the project's own coding convention (see memory note). `get_issue_state` is
already imported at the top of `implement.py` and `review.py`.
**Fix:** Move both to the top-level imports of `plan.py`.

### 7.2 `_commit_wip` creates new Anthropic client-unrelated subprocess boilerplate
`claude.py:141–181` is a git-operation function sitting inside the Claude invocation module.
**Fix:** Move to `git.py` (which already owns git operations via `ensure_repo`).

### 7.3 Jinja2 `Environment` recreated on every template render
`Environment(undefined=StrictUndefined)` in each `_build_*` function. Shared helper (see 1.3)
fixes this naturally.

### 7.4 `_save_conversation` / `_load_conversation` create the parent dir inside the function
`claude.py:212`: `conversation_path.parent.mkdir(parents=True, exist_ok=True)`.
Meanwhile `implement.py:89` and `review.py:105` also call `conv_path.parent.mkdir(...)` before
passing `conv_path` to `invoke_claude`. The directory is created twice.
**Fix:** Let the caller own directory creation, or always do it in `invoke_claude` only.

NOTE: also do not abbreviate the term "conversation" since it is not clear. Always use the full term.

### 7.5 `run_loop` reads `CLAYDE_INTERVAL` directly with `os.environ.get` instead of via settings
`orchestrator.py:316`:
```python
interval = int(os.environ.get("CLAYDE_INTERVAL", "300"))
```
All other config is read through `get_settings()`. Add `interval` as a `Settings` field.

### 7.6 `_has_new_comments` in `orchestrator.py` vs new-comment logic in `run_update` of `plan.py`
Both filter comments and check `c.id > last_seen and c.user.login != github_username`. The
orchestrator uses the result as a gate; `run_update` re-does the same filtering internally.
This is fine for safety, but a shared `get_new_visible_comments(...)` helper would make the
intent explicit and avoid drift.

### 7.7 `implement.run()` fetches `issue` twice
Lines 68 and 115 both call `fetch_issue(g, owner, repo, number)`. The second fetch is inside a
`try` block ostensibly in case the first fetch hadn't happened, but the first is unconditional.
**Fix:** Reuse the `issue` object from line 68 at line 115.

### 7.8 `_serialize_messages` — redundant else branch
`claude.py:204–205`: `else: serialized.append(msg)` handles user messages. The comment says
"user messages are already plain dicts", which is always true. The assistant branch is the only
interesting case; the else is noise.
(Minor style issue, not a bug.)

### 7.9 Span name `"clayde.handle_issue"` shared across all five handler functions
`orchestrator.py:51,87,132,177,195` all use the span name `"clayde.handle_issue"`. The handler
type is set via an attribute, so traces are distinguishable — but span names are the primary
grouping key in most trace UIs.
**Fix:** Use distinct names: `"clayde.handle_issue.new"`, `"clayde.handle_issue.awaiting_preliminary"`, etc.
