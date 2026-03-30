"""Async database service with idempotent upserts (spec §10).

Uses aiosqlite for local development. All write operations are
idempotent via INSERT OR IGNORE / ON CONFLICT patterns.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import aiosqlite

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"
_ID_NAMESPACE = uuid.UUID("4f93fbb6-c9eb-4d3f-b1b9-13f1da7d82c1")


class DatabaseService:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = (
            db_path
            if db_path is not None
            else os.getenv("DATABASE_URL") or "quiz_agent.db"
        )
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._run_migrations()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "DatabaseService not connected"
        return self._db

    async def _run_migrations(self) -> None:
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   name TEXT PRIMARY KEY,
                   applied_at TEXT NOT NULL DEFAULT (datetime('now'))
               )"""
        )
        applied = {
            row[0]
            for row in await (
                await self.db.execute(
                    "SELECT name FROM schema_migrations ORDER BY name"
                )
            ).fetchall()
        }
        for migration in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            if migration.name in applied:
                continue
            await self.db.executescript(migration.read_text())
            await self.db.execute(
                "INSERT INTO schema_migrations(name) VALUES (?)",
                (migration.name,),
            )
            await self.db.commit()

    @staticmethod
    def _stable_id(kind: str, key: str) -> str:
        return str(uuid.uuid5(_ID_NAMESPACE, f"{kind}:{key}"))

    # ── raw_sources ──────────────────────────────────────────────

    async def upsert_raw_source(
        self,
        *,
        source_request_key: str,
        markdown_url: str,
        source_hash: str,
        raw_content: str,
        normalized_content: str | None = None,
        summary: str | None = None,
        topic_candidates: list[str] | None = None,
    ) -> str:
        """Idempotent insert of a raw source. Returns the source id."""
        row = await self._fetch_one(
            "SELECT id FROM raw_sources WHERE source_request_key = ?",
            (source_request_key,),
        )
        if row:
            return cast(str, row[0])

        source_id = self._stable_id("raw-source", source_request_key)
        await self.db.execute(
            """INSERT INTO raw_sources
               (id, source_request_key, markdown_url, source_hash, raw_content)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(source_request_key) DO UPDATE SET
                 markdown_url = excluded.markdown_url,
                 source_hash = excluded.source_hash,
                 raw_content = excluded.raw_content""",
            (
                source_id,
                source_request_key,
                markdown_url,
                source_hash,
                raw_content,
            ),
        )
        await self.db.commit()
        row = await self._fetch_one(
            "SELECT id FROM raw_sources WHERE source_request_key = ?",
            (source_request_key,),
        )
        assert row is not None
        return cast(str, row[0])

    async def persist_prepared_source(
        self,
        source_id: str,
        normalized_content: str,
        summary: str,
        topic_candidates: list[str],
    ) -> None:
        await self.db.execute(
            """UPDATE raw_sources
               SET normalized_content = ?,
                   summary = ?,
                   topic_candidates = ?
               WHERE id = ?""",
            (
                normalized_content,
                summary,
                json.dumps(topic_candidates),
                source_id,
            ),
        )
        await self.db.commit()

    async def load_source_context(self, source_id: str) -> dict | None:
        row = await self._fetch_one(
            "SELECT id, markdown_url, normalized_content, summary, topic_candidates "
            "FROM raw_sources WHERE id = ?",
            (source_id,),
        )
        if not row:
            return None
        return {
            "id": cast(str, row[0]),
            "markdown_url": cast(str, row[1]),
            "normalized_content": cast(str, row[2] or ""),
            "summary": cast(str, row[3] or ""),
            "topic_candidates": (
                json.loads(cast(str, row[4])) if row[4] else []
            ),
        }

    # ── quiz_sessions + quiz_questions ───────────────────────────

    async def upsert_session_and_questions(
        self,
        *,
        session_key: str,
        user_id: str,
        source_id: str,
        topic: str,
        preferences: dict,
        questions: list[dict],
        workflow_id: str,
        workflow_run_id: str,
    ) -> str:
        """Idempotent persist of session + questions in one transaction.

        Returns the session id.
        """
        row = await self._fetch_one(
            "SELECT id FROM quiz_sessions WHERE session_key = ?",
            (session_key,),
        )
        if row is not None:
            session_id = row[0]
        else:
            session_id = self._stable_id("session", session_key)

        try:
            await self.db.execute("BEGIN")
            await self.db.execute(
                """INSERT INTO quiz_sessions
                   (id, session_key, user_id, source_id, workflow_id,
                    workflow_run_id, status, topic, preferences, question_count)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                   ON CONFLICT(session_key) DO NOTHING""",
                (
                    session_id,
                    session_key,
                    user_id,
                    source_id,
                    workflow_id,
                    workflow_run_id,
                    topic,
                    json.dumps(preferences),
                    len(questions),
                ),
            )
            current_row = await self._fetch_one(
                "SELECT id FROM quiz_sessions WHERE session_key = ?",
                (session_key,),
            )
            assert current_row is not None
            actual_session_id = cast(str, current_row[0])
            for q in questions:
                await self.db.execute(
                    """INSERT INTO quiz_questions
                       (id, session_id, position, question_text, options,
                        correct_answers, is_multi_answer, question_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         session_id = excluded.session_id,
                         position = excluded.position,
                         question_text = excluded.question_text,
                         options = excluded.options,
                         correct_answers = excluded.correct_answers,
                         is_multi_answer = excluded.is_multi_answer,
                         question_hash = excluded.question_hash""",
                    (
                        q["question_id"],
                        actual_session_id,
                        q["position"],
                        q["question_text"],
                        json.dumps(q["options"]),
                        json.dumps(q["correct_answers"]),
                        1 if q["is_multi_answer"] else 0,
                        q["question_hash"],
                    ),
                )
        except Exception:
            await self.db.rollback()
            raise
        await self.db.commit()
        return actual_session_id

    # ── quiz_answers ─────────────────────────────────────────────

    async def upsert_answer(
        self,
        *,
        session_key: str,
        question_id: str,
        selected_answers: list[int],
        score: float,
        is_correct: bool,
    ) -> None:
        """Idempotent upsert of a single answer."""
        session_id = await self._get_session_id(session_key)
        answer_id = self._stable_id("answer", f"{session_key}:{question_id}")
        await self.db.execute(
            """INSERT INTO quiz_answers
               (id, session_id, question_id, selected_answers, score, is_correct)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id, question_id) DO UPDATE SET
                 selected_answers = excluded.selected_answers,
                 score = excluded.score,
                 is_correct = excluded.is_correct,
                 answered_at = quiz_answers.answered_at""",
            (
                answer_id,
                session_id,
                question_id,
                json.dumps(selected_answers),
                score,
                1 if is_correct else 0,
            ),
        )
        await self.db.commit()

    # ── finalize ─────────────────────────────────────────────────

    async def finalize_session(
        self,
        *,
        session_key: str,
        final_score: float,
        final_score_pct: float,
    ) -> None:
        await self.db.execute(
            """UPDATE quiz_sessions
               SET status = 'completed',
                   final_score = ?,
                   final_score_pct = ?,
                   completed_at = COALESCE(completed_at, datetime('now'))
               WHERE session_key = ?""",
            (final_score, final_score_pct, session_key),
        )
        await self.db.commit()

    async def mark_session_abandoned(self, session_key: str) -> None:
        await self.db.execute(
            """UPDATE quiz_sessions
               SET status = CASE
                   WHEN status = 'completed' THEN status
                   ELSE 'abandoned'
               END
               WHERE session_key = ?""",
            (session_key,),
        )
        await self.db.commit()

    # ── read queries ─────────────────────────────────────────────

    async def list_user_sessions(self, user_id: str) -> list[dict]:
        cursor = await self.db.execute(
            """SELECT id, session_key, topic, status, final_score_pct, created_at
               FROM quiz_sessions
               WHERE user_id = ? AND status = 'completed'
               ORDER BY created_at DESC""",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "session_id": r[0],
                "session_key": r[1],
                "topic": r[2],
                "status": r[3],
                "final_score_pct": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]

    async def load_completed_quiz_review(
        self, user_id: str, session_id: str
    ) -> dict | None:
        session = await self._fetch_one(
            """SELECT id, topic, final_score, final_score_pct
               FROM quiz_sessions
               WHERE id = ? AND user_id = ? AND status = 'completed'""",
            (session_id, user_id),
        )
        if not session:
            return None

        cursor = await self.db.execute(
            """SELECT id, question_text, options, correct_answers,
                      is_multi_answer, position, question_hash
               FROM quiz_questions WHERE session_id = ?
               ORDER BY position""",
            (session_id,),
        )
        questions = await cursor.fetchall()

        cursor = await self.db.execute(
            """SELECT a.question_id, a.selected_answers, a.score, a.is_correct
               FROM quiz_answers a
               JOIN quiz_questions q ON q.id = a.question_id
               WHERE a.session_id = ?
               ORDER BY q.position""",
            (session_id,),
        )
        answers = await cursor.fetchall()

        return {
            "session_id": session[0],
            "topic": session[1],
            "final_score": session[2],
            "final_score_pct": session[3],
            "questions": [
                {
                    "question_id": q[0],
                    "question_text": q[1],
                    "options": json.loads(q[2]),
                    "correct_answers": json.loads(q[3]),
                    "is_multi_answer": bool(q[4]),
                    "position": q[5],
                    "question_hash": q[6],
                }
                for q in questions
            ],
            "grades": [
                {
                    "question_id": a[0],
                    "selected_answers": json.loads(a[1]),
                    "score": a[2],
                    "is_correct": bool(a[3]),
                }
                for a in answers
            ],
        }

    # ── helpers ───────────────────────────────────────────────────

    async def _get_session_id(self, session_key: str) -> str:
        row = await self._fetch_one(
            "SELECT id FROM quiz_sessions WHERE session_key = ?",
            (session_key,),
        )
        if not row:
            raise ValueError(f"Session not found: {session_key}")
        return cast(str, row[0])

    async def _fetch_one(
        self, sql: str, params: tuple = ()
    ) -> Sequence[object] | None:
        cursor = await self.db.execute(sql, params)
        return await cursor.fetchone()
