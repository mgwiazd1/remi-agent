CREATE TABLE IF NOT EXISTS bogwizard_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_type TEXT NOT NULL,
    signal_source TEXT,
    sector TEXT,
    signal_data_json TEXT,
    is_thread INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL,
    aestima_link TEXT,
    llm_used TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP,
    posted_at TIMESTAMP,
    posting_path TEXT,
    tweet_id TEXT,
    aestima_log_id INTEGER,
    error_message TEXT,
    engagement_1h INTEGER,
    engagement_24h INTEGER,
    engagement_checked_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bw_drafts_status ON bogwizard_drafts(status);
CREATE INDEX IF NOT EXISTS idx_bw_drafts_created ON bogwizard_drafts(created_at);
CREATE INDEX IF NOT EXISTS idx_bw_drafts_type_status ON bogwizard_drafts(draft_type, status);

-- Kill switch state table
CREATE TABLE IF NOT EXISTS bogwizard_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Default: auto-composition DISABLED until MG flips via /bog resume
INSERT OR IGNORE INTO bogwizard_state (key, value) VALUES ('auto_compose_enabled', 'false');
