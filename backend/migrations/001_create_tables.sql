-- Quiz Agent V2 schema (spec §10.2)
-- Compatible with SQLite (dev) and PostgreSQL (prod)

CREATE TABLE IF NOT EXISTS raw_sources (
    id              TEXT PRIMARY KEY,
    source_request_key TEXT UNIQUE NOT NULL,
    markdown_url    TEXT NOT NULL,
    source_hash     TEXT NOT NULL,
    raw_content     TEXT NOT NULL,
    normalized_content TEXT,
    summary         TEXT,
    topic_candidates TEXT,  -- JSON array
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS quiz_sessions (
    id              TEXT PRIMARY KEY,
    session_key     TEXT UNIQUE NOT NULL,
    user_id         TEXT NOT NULL,
    source_id       TEXT REFERENCES raw_sources(id),
    workflow_id     TEXT,
    workflow_run_id TEXT,
    status          TEXT NOT NULL DEFAULT 'created',
    topic           TEXT,
    preferences     TEXT,  -- JSON object
    question_count  INTEGER,
    final_score     REAL,
    final_score_pct REAL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS quiz_questions (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES quiz_sessions(id),
    position        INTEGER NOT NULL,
    question_text   TEXT NOT NULL,
    options         TEXT NOT NULL,  -- JSON array
    correct_answers TEXT NOT NULL,  -- JSON array
    is_multi_answer INTEGER NOT NULL DEFAULT 0,
    question_hash   TEXT,
    UNIQUE(session_id, position)
);

CREATE TABLE IF NOT EXISTS quiz_answers (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES quiz_sessions(id),
    question_id     TEXT NOT NULL REFERENCES quiz_questions(id),
    selected_answers TEXT NOT NULL,  -- JSON array
    score           REAL NOT NULL,
    is_correct      INTEGER NOT NULL DEFAULT 0,
    answered_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(session_id, question_id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON quiz_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_questions_session_id ON quiz_questions(session_id);
CREATE INDEX IF NOT EXISTS idx_answers_session_id ON quiz_answers(session_id);
