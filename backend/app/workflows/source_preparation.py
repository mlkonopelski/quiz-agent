"""SourcePreparationWorkflow — non-interactive child (spec §8.1).

Fetches, stores, normalizes, and summarizes a markdown source.
"""

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.activities.db_activities import persist_prepared_source
    from app.activities.llm_activities import websearch_source
    from app.activities.source_activities import (
        fetch_source,
        normalize_source,
        store_raw_source,
        summarize_source,
    )
    from app.models.source import (
        FetchSourceInput,
        NormalizeSourceInput,
        PersistPreparedSourceInput,
        SourceDescriptor,
        SourcePreparationInput,
        StoreRawSourceInput,
        SummarizeSourceInput,
        WebsearchSourceInput,
    )

# Task queues per spec §14
_HTTP_QUEUE = "quiz-http-activities"
_DB_QUEUE = "quiz-db-activities"
_LLM_QUEUE = "quiz-llm-activities"


@workflow.defn
class SourcePreparationWorkflow:
    @workflow.run
    async def run(self, input: SourcePreparationInput) -> SourceDescriptor:
        # 1. Fetch raw content (URL fetch or websearch)
        if input.markdown_url.startswith("websearch://"):
            fetch_result = await workflow.execute_activity(
                websearch_source,
                WebsearchSourceInput(topic=input.topic),
                task_queue=_LLM_QUEUE,
                schedule_to_close_timeout=timedelta(seconds=90),
            )
        else:
            fetch_result = await workflow.execute_activity(
                fetch_source,
                FetchSourceInput(markdown_url=input.markdown_url),
                task_queue=_HTTP_QUEUE,
                schedule_to_close_timeout=timedelta(seconds=60),
            )

        # 2. Store raw source (idempotent)
        source_id = await workflow.execute_activity(
            store_raw_source,
            StoreRawSourceInput(
                source_request_key=input.session_key,
                markdown_url=input.markdown_url,
                source_hash=fetch_result.source_hash,
                raw_content=fetch_result.raw_content,
            ),
            task_queue=_DB_QUEUE,
            schedule_to_close_timeout=timedelta(seconds=30),
        )

        # 3. Normalize content
        normalized = await workflow.execute_activity(
            normalize_source,
            NormalizeSourceInput(raw_content=fetch_result.raw_content),
            task_queue=_HTTP_QUEUE,
            schedule_to_close_timeout=timedelta(seconds=30),
        )

        # 4. Summarize and extract topic candidates
        summary_result = await workflow.execute_activity(
            summarize_source,
            SummarizeSourceInput(
                normalized_content=normalized.normalized_content,
                topic=input.topic,
            ),
            task_queue=_LLM_QUEUE,
            schedule_to_close_timeout=timedelta(seconds=120),
        )

        # 5. Persist prepared source context for later generation
        await workflow.execute_activity(
            persist_prepared_source,
            PersistPreparedSourceInput(
                source_id=source_id,
                normalized_content=normalized.normalized_content,
                summary=summary_result.summary,
                topic_candidates=summary_result.topic_candidates,
            ),
            task_queue=_DB_QUEUE,
            schedule_to_close_timeout=timedelta(seconds=30),
        )

        return SourceDescriptor(
            source_id=source_id,
            source_hash=fetch_result.source_hash,
            markdown_url=input.markdown_url,
            topic=input.topic,
            summary=summary_result.summary,
            topic_candidates=summary_result.topic_candidates,
        )
