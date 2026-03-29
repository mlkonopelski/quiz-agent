"""Async database service with idempotent upserts (spec §10).

Uses aiosqlite for local development. All write operations are
idempotent via INSERT OR IGNORE / ON CONFLICT patterns.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import aiosqlite

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


class DatabaseService:
    def __init__(self, db_path: str = "quiz_agent.db") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        migration = _MIGRATIONS_DIR / "001_create_tables.sql"
        await self._db.executescript(migration.read_text())

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "DatabaseService not connected"
        return self._db

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
            return row[0]

        source_id = str(uuid.uuid4())
        await self.db.execute(
            """INSERT OR IGNORE INTO raw_sources
               (id, source_request_key, markdown_url, source_hash, raw_content,
                normalized_content, summary, topic_candidates)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source_id,
                source_request_key,
                markdown_url,
                source_hash,
                raw_content,
                normalized_content,
                summary,
                json.dumps(topic_candidates) if topic_candidates else None,
            ),
        )
        await self.db.commit()
        return source_id

    async def update_source_normalized(
        self, source_id: str, normalized_content: str
    ) -> None:
        await self.db.execute(
            "UPDATE raw_sources SET normalized_content = ? WHERE id = ?",
            (normalized_content, source_id),
        )
        await self.db.commit()

    async def update_source_summary(
        self,
        source_id: str,
        summary: str,
        topic_candidates: list[str],
    ) -> None:
        await self.db.execute(
            "UPDATE raw_sources SET summary = ?, topic_candidates = ? WHERE id = ?",
            (summary, json.dumps(topic_candidates), source_id),
        )
        await self.db.commit()

    async def get_raw_source(self, source_id: str) -> dict | None:
        row = await self._fetch_one(
            "SELECT id, source_request_key, markdown_url, source_hash, "
            "raw_content, normalized_content, summary, topic_candidates "
            "FROM raw_sources WHERE id = ?",
            (source_id,),
        )
        if not row:
            return None
        return {
            "id": row[0],
            "source_request_key": row[1],
            "markdown_url": row[2],
            "source_hash": row[3],
            "raw_content": row[4],
            "normalized_content": row[5],
            "summary": row[6],
            "topic_candidates": json.loads(row[7]) if row[7] else [],
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
        if row:
            return row[0]

        session_id = str(uuid.uuid4())
        async with self.db.execute("BEGIN"):
            await self.db.execute(
                """INSERT OR IGNORE INTO quiz_sessions
                   (id, session_key, user_id, source_id, workflow_id,
                    workflow_run_id, status, topic, preferences, question_count)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
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
            for q in questions:
                await self.db.execute(
                    """INSERT OR IGNORE INTO quiz_questions
                       (id, session_id, position, question_text, options,
                        correct_answers, is_multi_answer, question_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        q["question_id"],
                        session_id,
                        q["position"],
                        q["question_text"],
                        json.dumps(q["options"]),
                        json.dumps(q["correct_answers"]),
                        1 if q["is_multi_answer"] else 0,
                        q.get("question_hash"),
                    ),
                )
        await self.db.commit()
        return session_id

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
        await self.db.execute(
            """INSERT INTO quiz_answers
               (id, session_id, question_id, selected_answers, score, is_correct)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id, question_id) DO UPDATE SET
                 selected_answers = excluded.selected_answers,
                 score = excluded.score,
                 is_correct = excluded.is_correct,
                 answered_at = datetime('now')""",
            (
                str(uuid.uuid4()),
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
                   completed_at = datetime('now')
               WHERE session_key = ?""",
            (final_score, final_score_pct, session_key),
        )
        await self.db.commit()

    async def mark_session_abandoned(self, session_key: str) -> None:
        await self.db.execute(
            "UPDATE quiz_sessions SET status = 'abandoned' WHERE session_key = ?",
            (session_key,),
        )
        await self.db.commit()

    # ── read queries ─────────────────────────────────────────────

    async def list_user_sessions(self, user_id: str) -> list[dict]:
        cursor = await self.db.execute(
            """SELECT id, session_key, topic, status, final_score_pct, created_at
               FROM quiz_sessions
               WHERE user_id = ?
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
                      is_multi_answer, position
               FROM quiz_questions WHERE session_id = ?
               ORDER BY position""",
            (session_id,),
        )
        questions = await cursor.fetchall()

        cursor = await self.db.execute(
            """SELECT question_id, selected_answers, score, is_correct
               FROM quiz_answers WHERE session_id = ?""",
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
        return row[0]

    async def _fetch_one(
        self, sql: str, params: tuple = ()
    ) -> tuple | None:
        cursor = await self.db.execute(sql, params)
        return await cursor.fetchone()
