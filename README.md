# quiz-agent

## Install

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/)
2. Clone the repo and install dependencies:

```bash
git clone <repo-url> && cd quiz-agent
uv sync
```

# Deployment

1. Create a local environment file:

```bash
cp .env.example .env
```

2. Fill in the required values in `.env`:

```bash
OPENROUTER_API_KEY=your-openrouter-api-key
OPENROUTER_CLARIFICATION_MODEL=google/gemini-2.0-flash-001
OPENROUTER_GENERATOR_MODEL=anthropic/claude-3.5-sonnet
OPENROUTER_CRITIC_MODEL=openai/gpt-4.1
DATABASE_URL=quiz_agent.db
QUIZ_DEFAULT_QUESTION_COUNT=6
```

`TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, and `TEMPORAL_API_KEY` are optional for local development. The app defaults to `localhost:7233`.

3. Start Temporal locally if it is not already running:

```bash
temporal server start-dev
```

You can verify it with:

```bash
temporal operator cluster health
```

4. Start the workflow worker in its own terminal:

```bash
uv run python -m app.workers.workflow_worker
```

5. Start the HTTP activity worker in a second terminal:

```bash
uv run python -m app.workers.http_worker
```

6. Start the LLM activity worker in a third terminal:

```bash
uv run python -m app.workers.llm_worker
```

7. Start the DB activity worker in a fourth terminal:

```bash
uv run python -m app.workers.db_worker
```

8. Start the FastAPI app in a fifth terminal:

```bash
uv run python -m app.starter
```

9. The system is live when all five Python processes above are running and Temporal is healthy. The API will be available at `http://localhost:8000`, and interactive API docs will be available at `http://localhost:8000/docs`.

No separate database migration command is needed. The SQLite database is created automatically and migrations are applied by the DB layer on first use.

# Example run

The example below uses this source URL:

```json
{
  "markdown_url": "https://github.com/pipecat-ai/pipecat/blob/main/README.md"
}
```

1. Create a workflow session:

```bash
curl -s -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "demo-user"
  }' | python -m json.tool
```

Copy the returned `workflow_id`.

2. Start a new quiz with the Pipecat README:

```bash
curl -s -X POST http://localhost:8000/sessions/<workflow_id>/commands \
  -H "Content-Type: application/json" \
  -d '{
    "command_id": "cmd-new-quiz-1",
    "kind": "NEW_QUIZ",
    "topic": "Pipecat",
    "markdown_url": "https://github.com/pipecat-ai/pipecat/blob/main/README.md"
  }' | python -m json.tool
```

3. Poll the workflow snapshot:

```bash
curl -s http://localhost:8000/sessions/<workflow_id>/snapshot | python -m json.tool
```

4. If the snapshot contains `pending_prompt`, reply with a clarification command using `pending_prompt.prompt_id` as the `correlation_id`:

```bash
curl -s -X POST http://localhost:8000/sessions/<workflow_id>/commands \
  -H "Content-Type: application/json" \
  -d '{
    "command_id": "cmd-clarification-1",
    "kind": "REPLY_CLARIFICATION",
    "correlation_id": "<pending_prompt.prompt_id>",
    "text": "Intermediate difficulty, mixed conceptual and technical questions, focused on pipelines, transports, and real-time voice agents."
  }' | python -m json.tool
```

5. Poll again until `current_question` is present. Then answer the question using `current_question.question_id` as the `correlation_id`:

```bash
curl -s -X POST http://localhost:8000/sessions/<workflow_id>/commands \
  -H "Content-Type: application/json" \
  -d '{
    "command_id": "cmd-answer-1",
    "kind": "ANSWER_QUESTION",
    "correlation_id": "<current_question.question_id>",
    "selected_answers": [0]
  }' | python -m json.tool
```

For multi-answer questions, send more than one option index, for example `"selected_answers": [0, 2]`.

6. Repeat the snapshot and answer steps until the workflow reaches `RESULT_MENU`.

7. When you are finished, stop the workflow cleanly:

```bash
curl -s -X POST http://localhost:8000/sessions/<workflow_id>/commands \
  -H "Content-Type: application/json" \
  -d '{
    "command_id": "cmd-quit-1",
    "kind": "QUIT"
  }' | python -m json.tool
```
