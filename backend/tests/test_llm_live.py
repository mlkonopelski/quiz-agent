"""Live integration tests for LLM activities via OpenRouter.

These tests make real API calls. Run with:
    uv run pytest tests/test_llm_live.py -v

Requires OPENROUTER_API_KEY and model env vars to be set (loaded from .env).
"""

import pytest
from dotenv import load_dotenv

from app.models.preferences import ClarificationDecision
from app.models.quiz import (
    CritiqueOutput,
    RawQuizOutput,
    RawQuizQuestion,
)
from app.services.openrouter_client import OpenRouterJsonGateway, get_model

load_dotenv()

_TOPIC = "Python programming"
_SUMMARY = (
    "Python is a high-level, general-purpose programming language. "
    "It supports multiple paradigms including procedural, object-oriented, "
    "and functional programming. Python emphasizes code readability with "
    "significant indentation."
)
_FOCUS_AREAS = ["syntax basics", "data types", "control flow"]


@pytest.fixture
async def gateway():
    gw = OpenRouterJsonGateway()
    yield gw
    await gw.close()


async def test_clarification_returns_valid_schema(gateway: OpenRouterJsonGateway):
    """The clarification model must return a ClarificationDecision, not plain text."""
    model = get_model("OPENROUTER_CLARIFICATION_MODEL")
    messages = [
        {
            "role": "system",
            "content": (
                "You are a quiz configuration assistant. "
                "Respond ONLY with valid JSON matching the provided schema."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Topic: {_TOPIC}\n"
                f"Source summary: {_SUMMARY}\n"
                f"Suggested focus areas: {_FOCUS_AREAS}\n"
                f"Current preferences: {{}}"
            ),
        },
        {"role": "assistant", "content": "What difficulty level would you like?"},
        {"role": "user", "content": "Beginner"},
        {"role": "assistant", "content": "Should questions be conceptual or technical?"},
        {"role": "user", "content": "Conceptual"},
    ]

    result = await gateway.request_model(
        model=model,
        messages=messages,
        response_type=ClarificationDecision,
    )

    assert result.action in ("ASK_USER", "READY")
    assert isinstance(result.message, str)
    assert len(result.message) > 0


async def test_clarification_multi_turn_stays_json(gateway: OpenRouterJsonGateway):
    """Even after several conversation turns, the model must return structured JSON."""
    model = get_model("OPENROUTER_CLARIFICATION_MODEL")
    messages = [
        {
            "role": "system",
            "content": (
                "You are a quiz configuration assistant. "
                "Respond ONLY with valid JSON matching the provided schema."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Topic: {_TOPIC}\n"
                f"Source summary: {_SUMMARY}\n"
                f"Suggested focus areas: {_FOCUS_AREAS}\n"
                f"Current preferences: {{}}"
            ),
        },
        {"role": "assistant", "content": "What difficulty?"},
        {"role": "user", "content": "Beginner"},
        {"role": "assistant", "content": "Conceptual or technical?"},
        {"role": "user", "content": "Conceptual"},
        {"role": "assistant", "content": "Broad overview or deep dive?"},
        {"role": "user", "content": "Broad overview"},
        {"role": "assistant", "content": "Any specific focus areas?"},
        {"role": "user", "content": "Just the basics"},
    ]

    result = await gateway.request_model(
        model=model,
        messages=messages,
        response_type=ClarificationDecision,
    )

    assert result.action in ("ASK_USER", "READY")
    assert isinstance(result.message, str)


async def test_quiz_generation_returns_valid_schema(gateway: OpenRouterJsonGateway):
    """The generator model must return a RawQuizOutput with valid questions."""
    model = get_model("OPENROUTER_GENERATOR_MODEL")
    question_count = 3

    messages = [
        {
            "role": "system",
            "content": (
                f"Generate exactly {question_count} multiple-choice questions "
                f"about {_TOPIC}. Each must have 4 options. "
                "Respond ONLY with valid JSON matching the provided schema."
            ),
        },
        {"role": "user", "content": f"Generate the quiz about: {_TOPIC}"},
    ]

    result = await gateway.request_model(
        model=model,
        messages=messages,
        response_type=RawQuizOutput,
    )

    assert len(result.questions) == question_count
    for q in result.questions:
        assert len(q.options) == 4
        assert len(q.correct_answers) >= 1
        assert all(0 <= idx <= 3 for idx in q.correct_answers)


async def test_critique_returns_valid_schema(gateway: OpenRouterJsonGateway):
    """The critic model must return a CritiqueOutput."""
    model = get_model("OPENROUTER_CRITIC_MODEL")

    sample_questions = [
        RawQuizQuestion(
            question_text="What is Python?",
            options=[
                "A snake",
                "A programming language",
                "A database",
                "An operating system",
            ],
            correct_answers=[1],
            is_multi_answer=False,
        ),
    ]

    questions_json = "\n".join(q.model_dump_json(indent=2) for q in sample_questions)
    messages = [
        {
            "role": "system",
            "content": (
                f"Review the following quiz questions about {_TOPIC}. "
                "Respond ONLY with valid JSON matching the provided schema."
            ),
        },
        {"role": "user", "content": f"Questions:\n{questions_json}"},
    ]

    result = await gateway.request_model(
        model=model,
        messages=messages,
        response_type=CritiqueOutput,
    )

    assert isinstance(result.feedback, str)
    assert isinstance(result.issues, list)
    assert isinstance(result.needs_regeneration, bool)
