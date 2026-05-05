CREATE TABLE IF NOT EXISTS media_sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type     TEXT NOT NULL,
    name            TEXT NOT NULL,
    url             TEXT NOT NULL,
    tier            INTEGER DEFAULT 2,
    clusters        TEXT,
    last_checked    TIMESTAMP,
    active          INTEGER DEFAULT 1,
    added_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS media_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       INTEGER,
    media_type      TEXT NOT NULL,
    media_url       TEXT NOT NULL,
    title           TEXT,
    duration_secs   INTEGER,
    transcript_text TEXT,
    status          TEXT DEFAULT 'pending',
    error_msg       TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at    TIMESTAMP,
    content_hash    TEXT UNIQUE
);
