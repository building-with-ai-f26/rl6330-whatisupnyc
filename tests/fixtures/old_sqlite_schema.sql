CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    start TEXT NOT NULL,
    venue TEXT NOT NULL,
    city TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    organizer TEXT,
    category TEXT,
    score INTEGER NOT NULL,
    eligible INTEGER NOT NULL,
    score_reason TEXT
);
CREATE TABLE IF NOT EXISTS dropped_events (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscribers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    interests TEXT NOT NULL,
    look_prompt TEXT NOT NULL,
    days TEXT NOT NULL,
    token TEXT
);
CREATE TABLE IF NOT EXISTS sent_events (
    subscriber_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    PRIMARY KEY (subscriber_id, event_id)
);
CREATE TABLE IF NOT EXISTS pastes (
    id INTEGER PRIMARY KEY,
    link TEXT NOT NULL,
    caption TEXT NOT NULL,
    name TEXT NOT NULL,
    start TEXT NOT NULL,
    venue TEXT NOT NULL,
    city TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    organizer TEXT
);
CREATE TABLE IF NOT EXISTS ai_requests (
    day TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS waiting_pastes (
    id INTEGER PRIMARY KEY,
    link TEXT NOT NULL,
    caption TEXT NOT NULL,
    tries INTEGER NOT NULL DEFAULT 0
);
