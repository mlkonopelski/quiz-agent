"""QuizGenerationWorkflow — non-interactive child (spec §8.3).

Generate → validate → critique → regenerate → validate → persist.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from app.activities.db_activities import (
        load_source_context,
        persist_session_and_questions,
    )
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
    from app.models.source import LoadSourceContextInput, SourceContext

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
        if any(not option.strip() for option in q.options):
            issues.append(f"{prefix}: blank option text")
        normalized_options = [option.strip().lower() for option in q.options]
        if len(normalized_options) != len(set(normalized_options)):
            issues.append(f"{prefix}: duplicate options")
        if len(q.correct_answers) != len(set(q.correct_answers)):
            issues.append(f"{prefix}: duplicate correct answer indexes")
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
    payload = {
        "question_text": q.question_text.strip().lower(),
        "options": [option.strip().lower() for option in q.options],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()[:16]


def _build_runtime_questions(
    raw_questions: list[RawQuizQuestion],
    session_key: str,
) -> list[RuntimeQuestion]:
    runtime_questions: list[RuntimeQuestion] = []
    for i, question in enumerate(raw_questions):
        question_hash = _question_hash(question)
        if not question_hash:
            raise ApplicationError(
                f"Q{i + 1}: missing question hash",
                non_retryable=True,
            )
        runtime_questions.append(
            RuntimeQuestion(
                question_id=f"{session_key}:q:{i + 1}",
                question_hash=question_hash,
                question_text=question.question_text,
                options=question.options,
                correct_answers=question.correct_answers,
                is_multi_answer=question.is_multi_answer,
                position=i + 1,
            )
        )
    return runtime_questions


@workflow.defn
class QuizGenerationWorkflow:
    @workflow.run
    async def run(self, input: QuizGenerationInput) -> QuizRuntimePackage:
        excluded_hashes = set(input.exclude_question_hashes)
        source_context: SourceContext = await workflow.execute_activity(
            load_source_context,
            LoadSourceContextInput(source_id=input.source_id),
            task_queue=_DB_QUEUE,
            schedule_to_close_timeout=timedelta(seconds=30),
        )
        source_summary = (
            source_context.summary
            or source_context.normalized_content[:4000]
        )

        # 1. Generate initial quiz
        raw_output: RawQuizOutput = await workflow.execute_activity(
            generate_quiz,
            QuizGenerateInput(
                topic=input.topic,
                preferences=input.preferences,
                question_count=input.question_count,
                source_summary=source_summary,
                topic_candidates=source_context.topic_candidates,
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
                source_summary=source_summary,
                topic_candidates=source_context.topic_candidates,
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
                    question_count=input.question_count,
                    source_summary=source_summary,
                    topic_candidates=source_context.topic_candidates,
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

        # 6. Enforce freshness exclusions with one focused retry
        runtime_questions = _build_runtime_questions(
            raw_output.questions,
            input.session_key,
        )
        overlapping_questions = [
            question
            for question in runtime_questions
            if question.question_hash in excluded_hashes
        ]
        if overlapping_questions:
            overlap_feedback = (
                "Replace every overlapping question with fresh wording and "
                "different concepts. Avoid repeating these questions:\n"
                + "\n".join(
                    f"- {question.question_text}"
                    for question in overlapping_questions
                )
            )
            raw_output = await workflow.execute_activity(
                regenerate_quiz,
                QuizRegenerateInput(
                    topic=input.topic,
                    preferences=input.preferences,
                    original_questions=raw_output.questions,
                    critique_feedback=overlap_feedback,
                    question_count=input.question_count,
                    source_summary=source_summary,
                    topic_candidates=source_context.topic_candidates,
                    avoid_question_texts=[
                        question.question_text
                        for question in overlapping_questions
                    ],
                ),
                task_queue=_LLM_QUEUE,
                schedule_to_close_timeout=timedelta(seconds=120),
            )
            issues = validate_quiz(raw_output, input.question_count)
            if issues:
                raise ApplicationError(
                    f"Quiz invalid after freshness regeneration: {'; '.join(issues)}",
                    non_retryable=True,
                )
            runtime_questions = _build_runtime_questions(
                raw_output.questions,
                input.session_key,
            )
            remaining_overlap = [
                question.question_hash
                for question in runtime_questions
                if question.question_hash in excluded_hashes
            ]
            if remaining_overlap:
                raise ApplicationError(
                    "Quiz regeneration still overlaps previous quiz content",
                    non_retryable=True,
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
                parent_workflow_id=input.parent_workflow_id,
                parent_workflow_run_id=input.parent_workflow_run_id,
            ),
            task_queue=_DB_QUEUE,
            schedule_to_close_timeout=timedelta(seconds=30),
        )

        return QuizRuntimePackage(
            session_id=session_id,
            session_key=input.session_key,
            questions=runtime_questions,
        )
