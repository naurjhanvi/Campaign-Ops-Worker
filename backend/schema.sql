CREATE TABLE IF NOT EXISTS campaigns (
  campaign_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  source_code TEXT NOT NULL UNIQUE,
  channel TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_deliveries (
  id BIGSERIAL PRIMARY KEY,
  delivery_id TEXT NOT NULL UNIQUE,
  idempotency_key TEXT NOT NULL,
  campaign_id TEXT REFERENCES campaigns(campaign_id),
  source_campaign_code TEXT NOT NULL,
  event_type TEXT NOT NULL,
  user_id TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  received_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL DEFAULT 'accepted' CHECK (status IN ('accepted', 'quarantined')),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS event_deliveries_campaign_day_idx
  ON event_deliveries (campaign_id, occurred_at);
CREATE INDEX IF NOT EXISTS event_deliveries_idempotency_idx
  ON event_deliveries (idempotency_key, occurred_at);

CREATE TABLE IF NOT EXISTS runbooks (
  runbook_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  body TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  run_id UUID PRIMARY KEY,
  task TEXT NOT NULL,
  status TEXT NOT NULL,
  messages JSONB NOT NULL DEFAULT '[]'::jsonb,
  summary TEXT,
  pending_tool_call_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS run_events (
  event_id BIGSERIAL PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  tool_name TEXT,
  details JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS remediation_actions (
  action_id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  action_type TEXT NOT NULL,
  campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
  event_date DATE NOT NULL,
  preview JSONB NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'applied', 'rejected')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  executed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS runtime_flags (
  flag_name TEXT PRIMARY KEY,
  flag_value INTEGER NOT NULL DEFAULT 0
);
