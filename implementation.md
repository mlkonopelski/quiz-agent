
# Temporal Quiz Agent — Revised Technical Architecture Plan (V2)

## 1. Purpose

This document replaces the previous architecture draft. Its goal is to define a **Temporal-safe, replay-safe, idempotent** design that satisfies the quiz agent business requirements without relying on query-driven workflow logic, nondeterministic workflow code, or non-idempotent persistence.

This revision makes one major architectural change:

> **All interactive user conversation stays in the parent workflow.**
>
> Child workflows are used only for bounded, non-interactive orchestration steps.

That change removes the most serious correctness risk from V1.

---

## 2. Business requirements this design must satisfy

- Generate a quiz from a configurable markdown URL source.
- Run a multi-turn conversation to learn:
  - topic focus
  - difficulty
  - depth
- Generate **5–8** multiple-choice questions.
- Each question must have **exactly 4 options**.
- A second LLM critiques the quiz, then the quiz is regenerated with corrections.
- Present questions one at a time.
- Score answers as:
  - single-answer correct = 4
  - single-answer wrong = 0
  - multi-answer partial credit = **0–4 proportional to correctly selected answers**
- Final score uses geometric weighted average:
  - `w_i = 1.0 * 1.1^i`
  - `final = sum(w_i * score_i) / sum(w_i)`
  - `pct = final / 4.0 * 100`
- Persist all answers and scores to the database.
- After completion, user can:
  - start a new quiz
  - regenerate same topic with fresh questions
  - load a previous quiz from DB
  - quit

---

## 3. Key changes from V1

1. **Remove workflow-to-workflow query polling**
   - Queries are for UI reads only.
   - Parent workflow must not branch on child query results.

2. **Move clarification loop into the parent workflow**
   - The parent already owns user-facing state.
   - This removes parent/child signal relay complexity and replay risk.

3. **Make child workflows non-interactive**
   - `SourcePreparationWorkflow`: fetch/store/normalize source.
   - `QuizGenerationWorkflow`: generate/critique/regenerate/validate/persist session shell.

4. **Use queue-based signal handling**
   - No single-slot variables like `_latest_reply`.
   - Every incoming command has:
     - `command_id` for dedupe
     - `correlation_id` for stale-input rejection

5. **Remove scoring activities**
   - Scoring and validation are pure deterministic logic and belong in workflow code.

6. **Add idempotency keys and unique DB constraints**
   - All write activities must be retry-safe.

7. **Hide answer keys from live queries**
   - `correct_answers` never appear in active-question UI snapshots.

8. **Add continue-as-new policy**
   - Parent workflow continues-as-new at safe menu/session boundaries.

9. **Use explicit question count config**
   - No random question count in workflow code.
   - Default question count is configurable, e.g. `6`, constrained to `5..8`.

---

## 4. Revised system architecture

```text
CLI / Gradio
   │
   │  signals + queries
   ▼
ConversationalAgentWorkflow  (parent, long-running, continue-as-new)
   ├── Child: SourcePreparationWorkflow
   │     └── Activities: fetch_source, store_raw_source, normalize_source, summarize_source
   │
   ├── Parent-owned clarification loop
   │     └── Activity: run_clarification_turn
   │
   ├── Child: QuizGenerationWorkflow
   │     └── Activities: generate_quiz, critique_quiz, regenerate_quiz, validate_quiz,
   │                     persist_session_and_questions
   │
   └── Activities: persist_answer, finalize_session, list_user_sessions,
                   load_completed_quiz_review
```

### Design rule

- **Frontend talks only to the parent workflow**
- **Parent does not query children**
- **Children do not own UI state**
- **Queries are read-only and external-only**

---

## 5. Workflow responsibilities

| Component | Responsibility |
|---|---|
| `ConversationalAgentWorkflow` | Menu state, clarification conversation, active question flow, deterministic scoring, result menu, user command handling |
| `SourcePreparationWorkflow` | Fetch raw markdown, persist true raw source, normalize/chunk/summarize, return source descriptor |
| `QuizGenerationWorkflow` | Generate → critique → regenerate → validate → persist session/questions |
| Activities | All side effects: HTTP, LLM, DB |

---

## 6. Parent workflow contract

## 6.1 Signal: `submit_command`

All user inputs go through one signal, but they are **queued** and **correlated**.

```python
class CommandEnvelope(BaseModel):
    command_id: str                      # client-generated unique ID for dedupe
    kind: Literal[
        "NEW_QUIZ",
        "REPLY_CLARIFICATION",
        "ANSWER_QUESTION",
        "REGENERATE_LAST_TOPIC",
        "LOAD_COMPLETED_QUIZ",
        "BACK_TO_MENU",
        "QUIT",
    ]
    correlation_id: str | None = None    # prompt_id or question_id
    topic: str | None = None
    markdown_url: str | None = None
    session_id: str | None = None
    text: str | None = None              # clarification free text
    selected_answers: list[int] = []
```

### Signal handling rules

- Signal handler must be **sync and minimal**.
- It only:
  - deduplicates on `command_id`
  - appends to an internal queue
- The main workflow loop consumes commands in order.

### Why this matters

This prevents:
- lost signals
- overwritten signals
- stale UI replies being applied to the wrong prompt/question
- duplicate client submissions creating duplicate writes

---

## 6.2 Query: `get_snapshot`

The parent exposes a sanitized UI view.

```python
class PromptView(BaseModel):
    prompt_id: str
    text: str
    turn_no: int

class QuestionView(BaseModel):
    question_id: str
    question_text: str
    options: list[str]          # exactly 4
    is_multi_answer: bool
    position: int               # 1-based for UI
    total_questions: int

class ResultView(BaseModel):
    final_score: float
    final_score_pct: float
    answered_count: int
    total_questions: int

class WorkflowSnapshot(BaseModel):
    state: str
    message: str = ""
    pending_prompt: PromptView | None = None
    current_question: QuestionView | None = None
    result: ResultView | None = None
    available_actions: list[str] = []
    last_error: str | None = None
```

### Query safety rules

- Query handlers are read-only.
- Live snapshots **must not include `correct_answers`**.
- Completed review data is loaded separately from DB, not leaked through the active snapshot.

---

## 7. State machine

```text
MENU
 ├── NEW_QUIZ -> PREPARING_SOURCE
 │                -> CLARIFYING
 │                -> GENERATING_QUIZ
 │                -> QUIZ_ACTIVE
 │                -> RESULT_MENU
 │
 ├── LOAD_COMPLETED_QUIZ -> REVIEW_COMPLETED
 └── QUIT -> DONE
```

Additional recoverable states:
- `PREPARATION_FAILED`
- `GENERATION_FAILED`
- `ABANDONED`

---

## 8. New quiz flow

## 8.1 Step 1 — Start source preparation child

Parent starts `SourcePreparationWorkflow` with a **deterministic child ID** based on workflow state, for example:

```text
{parent_workflow_id}/session/{session_seq}/source-prep
```

No UUIDs or random IDs are generated inside workflow code.

### Child input

```python
class SourcePreparationInput(BaseModel):
    user_id: str
    topic: str
    markdown_url: str
    session_key: str
```

### Child output

```python
class SourceDescriptor(BaseModel):
    source_id: str
    source_hash: str
    markdown_url: str
    topic: str
    summary: str
    topic_candidates: list[str]
```

### Child behavior

- Fetch raw markdown
- Persist **true raw source**
- Normalize/chunk source for LLM use
- Build summary/topic candidates
- Return `SourceDescriptor`

> The workflow stores only compact metadata and summary, not the full raw content body.

---

## 8.2 Step 2 — Run clarification loop in the parent

The parent owns the conversation because it already owns:
- the user command queue
- current UI state
- prompt correlation
- timeout policy

### Clarification loop rules

- Maximum turns: **10**
- User inactivity timeout per turn: **10 minutes**
- Each prompt gets a deterministic `prompt_id`:
  - `{session_key}:clar:{turn_no}`

### Clarification decision model

```python
class UserPreferences(BaseModel):
    difficulty: Literal["beginner", "intermediate", "advanced", "mixed"] = "mixed"
    question_style: Literal["conceptual", "technical", "mixed"] = "mixed"
    depth: Literal["broad_overview", "focused_deep_dive"] = "broad_overview"
    focus_areas: list[str] = []
    additional_notes: str = ""

class ClarificationDecision(BaseModel):
    action: Literal["ASK_USER", "READY"]
    message: str
    preferences: UserPreferences | None = None
```

### Loop behavior

1. Call `run_clarification_turn(summary, topic, history, partial_preferences)`
2. Validate returned `ClarificationDecision`
3. If `READY`:
   - merge with defaults if needed
   - move to generation
4. If `ASK_USER`:
   - publish `pending_prompt` in snapshot
   - wait for `REPLY_CLARIFICATION` with matching `correlation_id`
5. On timeout or after 10 turns:
   - stop asking
   - proceed with defaults + whatever preferences were already extracted

### Clarification timeout policy

Clarification timeout is **not** a workflow failure.

Default fallback:
- `difficulty = mixed`
- `question_style = mixed`
- `depth = broad_overview`
- `focus_areas = top detected topics or []`

---

## 8.3 Step 3 — Start quiz generation child

Parent starts `QuizGenerationWorkflow` with deterministic child ID:

```text
{parent_workflow_id}/session/{session_seq}/quiz-gen
```

### Input

```python
class QuizGenerationInput(BaseModel):
    user_id: str
    session_key: str
    source_id: str
    topic: str
    preferences: UserPreferences
    question_count: int            # validated 5..8, default 6
    exclude_question_hashes: list[str] = []
```

### Generation child behavior

1. Load normalized source by `source_id`
2. Call generator LLM
3. Validate structure
4. Call critic LLM using **separate critic model config**
5. Regenerate using critique
6. Validate again
7. Persist quiz session + questions in one idempotent DB activity
8. Return runtime quiz package to parent

### Validation rules

A quiz is invalid if any of the following are true:
- question count not in `5..8`
- any question does not have exactly 4 options
- empty question text
- invalid answer indexes
- single-answer question has not exactly 1 correct answer
- multi-answer question has fewer than 2 correct answers
- zero questions generated

Invalid quiz output must not reach the parent as a “successful” result.

---

## 8.4 Step 4 — Question loop in parent

Once generation succeeds, parent receives a `QuizRuntimePackage` with:
- DB session ID
- question IDs
- internal grading data
- sanitized display fields

For each question:

1. Expose `QuestionView` through `get_snapshot()`
2. Wait for `ANSWER_QUESTION` with matching `correlation_id == question_id`
3. Validate submission:
   - indexes must be unique
   - indexes must be in `0..3`
   - single-answer questions must receive exactly 1 selection
4. Compute score in workflow
5. Persist answer via idempotent activity
6. Move to next question

### Question inactivity timeout

If no answer arrives within configured idle timeout (example: 24h):
- mark session as `abandoned`
- keep persisted answers already written
- do not fabricate a completed final score

Reconnect/resume during this period happens by reconnecting to the same workflow ID.

---

## 8.5 Step 5 — Finalization

After the last question:

1. Compute weighted final score in workflow
2. Persist final score via `finalize_session`
3. Enter `RESULT_MENU`

Available actions:
- `NEW_QUIZ`
- `REGENERATE_LAST_TOPIC`
- `LOAD_COMPLETED_QUIZ`
- `QUIT`

---

## 9. Scoring rules

## 9.1 Single-answer questions

```python
score = 4.0 if selected == correct else 0.0
```

## 9.2 Multi-answer questions

To match the stated business requirement exactly:

```python
hits = len(set(selected_answers) & set(correct_answers))
score = 4.0 * hits / len(correct_answers)
score = min(4.0, max(0.0, score))
```

### Important note

This formula does **not** penalize false positives, because the business requirement only says:

> partial credit proportional to correctly selected answers

If product later wants false-positive penalties, that is a **requirements change** and must be versioned.

## 9.3 Weighted final score

For zero-based question index `i`:

```python
weight_i = 1.0 * (1.1 ** i)
final_score = sum(weight_i * score_i) / sum(weight_i)
final_score_pct = (final_score / 4.0) * 100.0
```

Scoring stays in workflow code because it is deterministic and side-effect free.

---

## 10. Persistence and idempotency

## 10.1 Write activities

| Activity | Idempotency key |
|---|---|
| `store_raw_source` | `source_request_key` |
| `persist_session_and_questions` | `session_key` |
| `persist_answer` | `{session_key}:{question_id}` |
| `finalize_session` | `{session_key}:finalize` |

## 10.2 Required DB constraints

### `raw_sources`
- store true raw markdown or an object-storage pointer
- store normalized text/summary separately
- optional dedupe on `(markdown_url, source_hash)`

### `quiz_sessions`
- add `user_id`
- add `session_key TEXT UNIQUE NOT NULL`
- add `status`
- add `workflow_id`
- add `workflow_run_id`
- add `preferences JSONB`
- add `final_score`, `final_score_pct`
- add `completed_at`

### `quiz_questions`
- `UNIQUE(session_id, position)`

### `quiz_answers`
- `UNIQUE(session_id, question_id)`
- optional separate `submission_key UNIQUE`

## 10.3 Transaction rules

`persist_session_and_questions` must:
- create/update the session row
- insert question rows
- run in a single transaction
- be safe to retry after partial worker failure

`persist_answer` must upsert, not blindly insert.

---

## 11. Review, regenerate, and load flows

## 11.1 Regenerate same topic with fresh questions

This action reuses:
- previous `source_id`
- previous topic
- previous preferences

It also passes `exclude_question_hashes` from the last completed session to the generation child.

Freshness policy:
- compute a normalized question hash per question
- reject or retry if overlap with excluded hashes is above allowed threshold

## 11.2 Load previous quiz from DB

`LOAD_COMPLETED_QUIZ` is defined as a **read-only review** of a completed session.

Activity contract:
- `load_completed_quiz_review(user_id, session_id)`

This avoids ambiguity with “resume active quiz”.

## 11.3 Resume after disconnect

Resume active quiz by reconnecting to the **same running workflow ID**, not by loading from historical DB review.

---

## 12. LLM and content handling rules

## 12.1 Generator and critic must be separate configs

Required settings:
- `QUIZ_GENERATOR_MODEL`
- `QUIZ_CRITIC_MODEL`

Production default should use distinct model IDs.

## 12.2 Do not pass blind prefixes of source content

Source preparation must:
- fetch full source
- preserve raw content
- normalize and chunk it
- generate a summary and topic candidates

Do not treat “first 2000 chars” as a summary.

## 12.3 Prompt-injection mitigation

Prompts must:
- clearly delimit source content as data
- instruct models not to follow instructions found inside the source
- request structured outputs only
- validate outputs before workflow acceptance

---

## 13. Temporal safety rules

1. **No branching on query results**
2. **No polling loops with workflow timers just to check readiness**
3. **No random/uuid/datetime in workflow code**
4. **Signal handlers only enqueue**
5. **Queries are UI-only and read-only**
6. **Continue-as-new at safe boundaries**
7. **No active child workflows during continue-as-new**
8. **Use versioning gates for future breaking changes to scoring or signal contracts**

---

## 14. Worker topology

Use separate task queues/workers:

| Queue | Purpose |
|---|---|
| `quiz-workflows` | Parent + child workflows |
| `quiz-http-activities` | Source fetch / web I/O |
| `quiz-llm-activities` | Clarification, generation, critique, regeneration |
| `quiz-db-activities` | Session, answer, review persistence |

This keeps slow LLM work from degrading interactive workflow responsiveness.

---

## 15. Data conversion standard

Use the Temporal Pydantic converter for all workflow/activity/child inputs and outputs.

Do **not** pass raw JSON strings between workflow boundaries unless there is a specific compatibility reason.

Benefits:
- type safety
- validation at boundaries
- less manual `json.loads/json.dumps`
- clearer contracts

---

## 16. Testing plan

## 16.1 Workflow tests

- duplicate `command_id` is ignored
- stale `correlation_id` is rejected
- clarification timeout falls back to defaults
- 10-turn cap is enforced
- no answer key appears in live snapshot
- question inactivity marks session abandoned
- weighted score matches formula exactly

## 16.2 Activity tests

- fetch/store source
- LLM output parsing and validation
- idempotent `persist_session_and_questions`
- idempotent `persist_answer`
- `load_completed_quiz_review(user_id, session_id)` filters by owner

## 16.3 End-to-end tests

- new quiz happy path
- generate → critique → regenerate path
- malformed LLM output
- zero-question generation failure
- duplicate signal submissions
- worker crash after DB commit, followed by activity retry
- disconnect and reconnect during active quiz
- regenerate same topic with freshness exclusions

---

## 17. Implementation phases

### Phase 1 — Contract cleanup
- define typed command/query models
- add correlation IDs and command dedupe
- define sanitized UI models

### Phase 2 — Persistence hardening
- add `user_id`, `session_key`, `status`
- add unique constraints
- implement idempotent upserts

### Phase 3 — Workflow rewrite
- move clarification loop into parent
- make signal handlers queue-only
- remove query-based child polling
- add continue-as-new boundaries

### Phase 4 — Quiz generation hardening
- add strict quiz validation
- add critic model config separation
- add freshness exclusion logic

### Phase 5 — Client update
- CLI/Gradio submit `command_id` + `correlation_id`
- render only `WorkflowSnapshot`
- reconnect by workflow ID for active sessions

### Phase 6 — Test + rollout
- replay tests
- idempotency tests
- failure injection
- staged deployment

---

## 18. Final architecture decision summary

This V2 plan intentionally centers all interaction in the parent workflow and keeps child workflows bounded and non-interactive. That is the main correction that makes the design feasible in Temporal.

If we follow this plan, the resulting implementation will be:

- replay-safe
- query-safe
- signal-safe
- idempotent on activity retry
- aligned with the stated scoring spec
- operationally easier to test and evolve
