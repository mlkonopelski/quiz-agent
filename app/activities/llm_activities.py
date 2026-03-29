"""LLM activities using OpenRouter (spec §12).

All LLM calls go through OpenRouter exclusively.
"""

from __future__ import annotations

import json

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.models.preferences import ClarificationDecision, UserPreferences
from app.models.quiz import (
    ClarificationTurnInput,
    CritiqueOutput,
    QuizCritiqueInput,
    QuizGenerateInput,
    QuizRegenerateInput,
    RawQuizOutput,
    RawQuizQuestion,
)
from app.services.openrouter_client import OpenRouterClient, get_model

# ── Prompt templates ─────────────────────────────────────────────

_CLARIFICATION_SYSTEM = """\
You are a quiz configuration assistant. Your job is to understand what the user \
wants from their quiz so we can generate the best possible questions.

You have a source document summary and topic. Through conversation, determine:
- difficulty level (beginner/intermediate/advanced/mixed)
- question style (conceptual/technical/mixed)
- depth (broad_overview/focused_deep_dive)
- specific focus areas within the topic
- any additional preferences

If you have enough information to configure the quiz, respond with action "READY" \
and fill in the preferences. Otherwise respond with action "ASK_USER" and a \
natural follow-up question.

Respond ONLY with valid JSON matching this schema:
{
  "action": "ASK_USER" | "READY",
  "message": "<your message to the user>",
  "preferences": {  // only when action is READY
    "difficulty": "beginner"|"intermediate"|"advanced"|"mixed",
    "question_style": "conceptual"|"technical"|"mixed",
    "depth": "broad_overview"|"focused_deep_dive",
    "focus_areas": ["area1", "area2"],
    "additional_notes": ""
  }
}"""

_GENERATE_SYSTEM = """\
You are a quiz question generator. Generate exactly {question_count} multiple-choice \
questions about the topic "{topic}" based on the provided source material.

User preferences:
- Difficulty: {difficulty}
- Style: {question_style}
- Depth: {depth}
- Focus areas: {focus_areas}

Rules:
- Each question MUST have exactly 4 options.
- For single-answer questions, exactly 1 correct answer index.
- For multi-answer questions, 2 or more correct answer indexes.
- Indexes are 0-based (0, 1, 2, 3).
- Mix single and multi-answer questions.

Respond ONLY with valid JSON:
{{
  "questions": [
    {{
      "question_text": "...",
      "options": ["A", "B", "C", "D"],
      "correct_answers": [0],
      "is_multi_answer": false
    }}
  ]
}}"""

_CRITIQUE_SYSTEM = """\
You are a quiz quality critic. Review the following quiz questions and provide feedback.

Evaluate each question for:
- Accuracy of correct answers
- Clarity of question text
- Quality and distinctness of options (no overlapping/ambiguous choices)
- Appropriate difficulty for the stated level
- Relevance to the topic

Respond ONLY with valid JSON:
{{
  "feedback": "<overall assessment>",
  "issues": ["issue 1", "issue 2"],
  "needs_regeneration": true|false
}}"""

_REGENERATE_SYSTEM = """\
You are a quiz question generator. Regenerate the quiz incorporating the critic's feedback.

Original quiz had these issues:
{critique_feedback}

Regenerate exactly {question_count} questions following the same rules as before. \
Fix all identified issues while maintaining topic relevance.

Rules:
- Each question MUST have exactly 4 options.
- For single-answer questions, exactly 1 correct answer index.
- For multi-answer questions, 2 or more correct answer indexes.
- Indexes are 0-based (0, 1, 2, 3).

Respond ONLY with valid JSON:
{{
  "questions": [
    {{
      "question_text": "...",
      "options": ["A", "B", "C", "D"],
      "correct_answers": [0],
      "is_multi_answer": false
    }}
  ]
}}"""


# ── Activities ───────────────────────────────────────────────────


@activity.defn
async def run_clarification_turn(
    input: ClarificationTurnInput,
) -> ClarificationDecision:
    """Call the clarification model to decide next step in conversation."""
    client = OpenRouterClient()
    model = get_model("OPENROUTER_CLARIFICATION_MODEL")

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _CLARIFICATION_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Topic: {input.topic}\n"
                f"Source summary: {input.summary}\n"
                f"Current preferences: {input.partial_preferences.model_dump_json()}"
            ),
        },
    ]
    for turn in input.history:
        messages.append({"role": turn["role"], "content": turn["content"]})

    try:
        response = await client.chat_completion(model=model, messages=messages)
        content = client.get_content(response)
        parsed = json.loads(content)
        return ClarificationDecision.model_validate(parsed)
    except (json.JSONDecodeError, KeyError) as exc:
        raise ApplicationError(
            f"Invalid clarification response: {exc}", non_retryable=True
        ) from exc
    finally:
        await client.close()


@activity.defn
async def generate_quiz(input: QuizGenerateInput) -> RawQuizOutput:
    """Generate initial quiz questions via the generator model."""
    client = OpenRouterClient()
    model = get_model("OPENROUTER_GENERATOR_MODEL")

    prefs = input.preferences
    system_msg = _GENERATE_SYSTEM.format(
        question_count=input.question_count,
        topic=input.topic,
        difficulty=prefs.difficulty,
        question_style=prefs.question_style,
        depth=prefs.depth,
        focus_areas=", ".join(prefs.focus_areas) or "general",
    )

    user_content = f"Generate the quiz about: {input.topic}"
    if input.source_summary:
        user_content += f"\n\nSource material summary:\n{input.source_summary}"

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_content},
    ]

    try:
        response = await client.chat_completion(model=model, messages=messages)
        content = client.get_content(response)
        parsed = json.loads(content)
        return RawQuizOutput.model_validate(parsed)
    except (json.JSONDecodeError, KeyError) as exc:
        raise ApplicationError(
            f"Invalid quiz generation response: {exc}", non_retryable=True
        ) from exc
    finally:
        await client.close()


@activity.defn
async def critique_quiz(input: QuizCritiqueInput) -> CritiqueOutput:
    """Critique quiz questions using the critic model."""
    client = OpenRouterClient()
    model = get_model("OPENROUTER_CRITIC_MODEL")

    questions_json = json.dumps(
        [q.model_dump() for q in input.questions], indent=2
    )
    messages = [
        {"role": "system", "content": _CRITIQUE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Topic: {input.topic}\n"
                f"Difficulty: {input.preferences.difficulty}\n\n"
                f"Questions:\n{questions_json}"
            ),
        },
    ]

    try:
        response = await client.chat_completion(model=model, messages=messages)
        content = client.get_content(response)
        parsed = json.loads(content)
        return CritiqueOutput.model_validate(parsed)
    except (json.JSONDecodeError, KeyError) as exc:
        raise ApplicationError(
            f"Invalid critique response: {exc}", non_retryable=True
        ) from exc
    finally:
        await client.close()


@activity.defn
async def regenerate_quiz(input: QuizRegenerateInput) -> RawQuizOutput:
    """Regenerate quiz questions incorporating critic feedback."""
    client = OpenRouterClient()
    model = get_model("OPENROUTER_GENERATOR_MODEL")

    prefs = input.preferences
    system_msg = _REGENERATE_SYSTEM.format(
        critique_feedback=input.critique_feedback,
        question_count=len(input.original_questions),
    )

    original_json = json.dumps(
        [q.model_dump() for q in input.original_questions], indent=2
    )
    messages = [
        {"role": "system", "content": system_msg},
        {
            "role": "user",
            "content": (
                f"Topic: {input.topic}\n"
                f"Preferences: difficulty={prefs.difficulty}, "
                f"style={prefs.question_style}, depth={prefs.depth}\n\n"
                f"Original questions:\n{original_json}"
            ),
        },
    ]

    try:
        response = await client.chat_completion(model=model, messages=messages)
        content = client.get_content(response)
        parsed = json.loads(content)
        return RawQuizOutput.model_validate(parsed)
    except (json.JSONDecodeError, KeyError) as exc:
        raise ApplicationError(
            f"Invalid regeneration response: {exc}", non_retryable=True
        ) from exc
    finally:
        await client.close()
