"""QuizGenerationWorkflow — non-interactive child (spec §8.3).

Generate → validate → critique → regenerate → validate → persist.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from app.activities.db_activities import persist_session_and_questions
    from app.activities.llm_activities import (
        critique_quiz,
        generate_quiz,
        regenerate_quiz,
    )
    from app.models.quiz import (
        CritiqueOutput,
        PersistSessionInput,
        QuizCritiqueInput,
        QuizGenerateInput,
        QuizGenerationInput,
        QuizRegenerateInput,
        QuizRuntimePackage,
        RawQuizOutput,
        RawQuizQuestion,
        RuntimeQuestion,
    )

_LLM_QUEUE = "quiz-llm-activities"
_DB_QUEUE = "quiz-db-activities"


def validate_quiz(output: RawQuizOutput, expected_count: int) -> list[str]:
    """Validate quiz structure per spec §8.3 rules. Returns list of issues."""
    issues: list[str] = []

    if len(output.questions) == 0:
        issues.append("Zero questions generated")
        return issues

    if not 5 <= len(output.questions) <= 8:
        issues.append(
            f"Question count {len(output.questions)} not in 5..8"
        )

    if len(output.questions) != expected_count:
        issues.append(
            f"Expected {expected_count} questions, got {len(output.questions)}"
        )

    for i, q in enumerate(output.questions):
        prefix = f"Q{i + 1}"
        if len(q.options) != 4:
            issues.append(f"{prefix}: has {len(q.options)} options, need exactly 4")
        if not q.question_text.strip():
            issues.append(f"{prefix}: empty question text")
        for idx in q.correct_answers:
            if idx < 0 or idx > 3:
                issues.append(f"{prefix}: invalid answer index {idx}")
        if q.is_multi_answer:
            if len(q.correct_answers) < 2:
                issues.append(
                    f"{prefix}: multi-answer needs >=2 correct, got {len(q.correct_answers)}"
                )
        else:
            if len(q.correct_answers) != 1:
                issues.append(
                    f"{prefix}: single-answer needs exactly 1 correct, got {len(q.correct_answers)}"
                )

    return issues


def _question_hash(q: RawQuizQuestion) -> str:
    """Compute a normalized hash for freshness exclusion."""
    normalized = q.question_text.strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


@workflow.defn
class QuizGenerationWorkflow:
    @workflow.run
    async def run(self, input: QuizGenerationInput) -> QuizRuntimePackage:
        # 1. Generate initial quiz
        raw_output: RawQuizOutput = await workflow.execute_activity(
            generate_quiz,
            QuizGenerateInput(
                topic=input.topic,
                preferences=input.preferences,
                question_count=input.question_count,
            ),
            task_queue=_LLM_QUEUE,
            schedule_to_close_timeout=timedelta(seconds=120),
        )

        # 2. Validate structure
        issues = validate_quiz(raw_output, input.question_count)

        # 3. Critique (even if valid — always get feedback)
        critique: CritiqueOutput = await workflow.execute_activity(
            critique_quiz,
            QuizCritiqueInput(
                topic=input.topic,
                preferences=input.preferences,
                questions=raw_output.questions,
            ),
            task_queue=_LLM_QUEUE,
            schedule_to_close_timeout=timedelta(seconds=120),
        )

        # 4. Regenerate if there are structural issues or critic says so
        if issues or critique.needs_regeneration:
            combined_feedback = critique.feedback
            if issues:
                combined_feedback += "\n\nStructural issues:\n" + "\n".join(
                    f"- {i}" for i in issues
                )

            raw_output = await workflow.execute_activity(
                regenerate_quiz,
                QuizRegenerateInput(
                    topic=input.topic,
                    preferences=input.preferences,
                    original_questions=raw_output.questions,
                    critique_feedback=combined_feedback,
                ),
                task_queue=_LLM_QUEUE,
                schedule_to_close_timeout=timedelta(seconds=120),
            )

            # 5. Validate again — fail if still invalid
            issues = validate_quiz(raw_output, input.question_count)
            if issues:
                raise ApplicationError(
                    f"Quiz still invalid after regeneration: {'; '.join(issues)}",
                    non_retryable=True,
                )

        # 6. Build runtime questions with IDs
        runtime_questions: list[RuntimeQuestion] = []
        for i, q in enumerate(raw_output.questions):
            q_hash = _question_hash(q)
            runtime_questions.append(
                RuntimeQuestion(
                    question_id=f"{input.session_key}:q:{i + 1}",
                    question_text=q.question_text,
                    options=q.options,
                    correct_answers=q.correct_answers,
                    is_multi_answer=q.is_multi_answer,
                    position=i + 1,
                )
            )

        # 7. Persist session + questions (idempotent)
        session_id: str = await workflow.execute_activity(
            persist_session_and_questions,
            PersistSessionInput(
                session_key=input.session_key,
                user_id=input.user_id,
                source_id=input.source_id,
                topic=input.topic,
                preferences=input.preferences,
                questions=runtime_questions,
                workflow_id=workflow.info().workflow_id,
                workflow_run_id=workflow.info().run_id,
            ),
            task_queue=_DB_QUEUE,
            schedule_to_close_timeout=timedelta(seconds=30),
        )

        return QuizRuntimePackage(
            session_id=session_id,
            session_key=input.session_key,
            questions=runtime_questions,
        )
