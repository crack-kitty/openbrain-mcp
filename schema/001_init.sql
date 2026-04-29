-- OpenBrain schema (typed memory tables — designed per shep-engineering v2 lessons)
-- Idempotent: safe to run on every server startup.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

DO $$ BEGIN
  CREATE TYPE rule_severity AS ENUM ('BLOCKER', 'PATTERN', 'DEPRECATED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE task_status AS ENUM ('open', 'blocked', 'done', 'stale');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE memory_kind AS ENUM ('rule', 'fact', 'incident', 'task');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE audit_action AS ENUM ('INSERT', 'SUPERSEDE', 'UPDATE', 'DELETE', 'PRUNE');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS rules (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  headline        text NOT NULL,
  body            text NOT NULL,
  severity        rule_severity NOT NULL DEFAULT 'PATTERN',
  project         text NOT NULL DEFAULT 'default',
  tags            text[] NOT NULL DEFAULT '{}',
  embedding       vector(768),
  pinned          boolean NOT NULL DEFAULT false,
  skill_trigger   jsonb,
  superseded_by   uuid REFERENCES rules(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS facts (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  headline          text NOT NULL,
  body              text NOT NULL,
  project           text NOT NULL DEFAULT 'default',
  source            text,
  people            text[] NOT NULL DEFAULT '{}',
  topics            text[] NOT NULL DEFAULT '{}',
  tags              text[] NOT NULL DEFAULT '{}',
  embedding         vector(768),
  access_count      int NOT NULL DEFAULT 0,
  last_accessed_at  timestamptz,
  decay_score       double precision NOT NULL DEFAULT 1.0,
  active            boolean NOT NULL DEFAULT true,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS incidents (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  headline    text NOT NULL,
  body        text NOT NULL,
  project     text NOT NULL DEFAULT 'default',
  severity    text,
  tags        text[] NOT NULL DEFAULT '{}',
  embedding   vector(768),
  archived    boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tasks (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  headline      text NOT NULL,
  body          text NOT NULL,
  project       text NOT NULL DEFAULT 'default',
  status        task_status NOT NULL DEFAULT 'open',
  priority      int NOT NULL DEFAULT 3,
  tags          text[] NOT NULL DEFAULT '{}',
  embedding     vector(768),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  completed_at  timestamptz
);

CREATE TABLE IF NOT EXISTS memory_index (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind          memory_kind NOT NULL,
  ref_id        uuid NOT NULL,
  headline      text NOT NULL,
  headline_tsv  tsvector,
  embedding     vector(768),
  project       text NOT NULL DEFAULT 'default',
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (kind, ref_id)
);

CREATE TABLE IF NOT EXISTS sessions (
  session_id    text PRIMARY KEY,
  source        text,
  project       text NOT NULL DEFAULT 'default',
  summary       text,
  handoff_note  text,
  started_at    timestamptz NOT NULL DEFAULT now(),
  ended_at      timestamptz,
  active        boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS audit_log (
  id          serial PRIMARY KEY,
  kind        text NOT NULL,
  ref_id      uuid,
  action      audit_action NOT NULL,
  snapshot    jsonb,
  session_id  text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memory_index_embedding_hnsw
  ON memory_index USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_memory_index_tsv
  ON memory_index USING gin (headline_tsv);

CREATE INDEX IF NOT EXISTS idx_memory_index_project ON memory_index (project);
CREATE INDEX IF NOT EXISTS idx_memory_index_kind ON memory_index (kind);
CREATE INDEX IF NOT EXISTS idx_memory_index_created_at ON memory_index (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_rules_project ON rules (project);
CREATE INDEX IF NOT EXISTS idx_rules_severity ON rules (severity);
CREATE INDEX IF NOT EXISTS idx_rules_pinned ON rules (pinned) WHERE pinned;
CREATE INDEX IF NOT EXISTS idx_rules_active ON rules (id) WHERE superseded_by IS NULL;

CREATE INDEX IF NOT EXISTS idx_facts_project ON facts (project);
CREATE INDEX IF NOT EXISTS idx_facts_active ON facts (active);
CREATE INDEX IF NOT EXISTS idx_facts_decay ON facts (decay_score DESC);

CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks (project);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status);

CREATE INDEX IF NOT EXISTS idx_incidents_project ON incidents (project);
CREATE INDEX IF NOT EXISTS idx_incidents_archived ON incidents (archived);

CREATE INDEX IF NOT EXISTS idx_audit_log_kind_ref ON audit_log (kind, ref_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_session ON audit_log (session_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log (created_at DESC);

CREATE OR REPLACE FUNCTION memory_index_tsv_trigger() RETURNS trigger AS $$
BEGIN
  NEW.headline_tsv := to_tsvector('english', coalesce(NEW.headline, ''));
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_memory_index_tsv ON memory_index;
CREATE TRIGGER trg_memory_index_tsv
  BEFORE INSERT OR UPDATE OF headline ON memory_index
  FOR EACH ROW EXECUTE FUNCTION memory_index_tsv_trigger();
