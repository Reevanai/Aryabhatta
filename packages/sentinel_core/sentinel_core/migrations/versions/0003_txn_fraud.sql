-- Transaction fraud: rules, thresholds, scores, labels, backtests (Phase 4).

CREATE SCHEMA txn_fraud;
GRANT USAGE ON SCHEMA txn_fraud TO sentinel_app;

CREATE TABLE txn_fraud.threshold_profile (
  tenant_id  uuid PRIMARY KEY REFERENCES core.tenant (id),
  review     numeric(5, 4) NOT NULL DEFAULT 0.4 CHECK (review > 0 AND review <= 1),
  hold       numeric(5, 4) NOT NULL DEFAULT 0.75 CHECK (hold > 0 AND hold <= 1),
  params     jsonb NOT NULL DEFAULT '{}'::jsonb,
  computed   jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_by text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (hold >= review)
);

CREATE TABLE txn_fraud.rule (
  id         uuid PRIMARY KEY,
  tenant_id  uuid NOT NULL REFERENCES core.tenant (id),
  code       text NOT NULL CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{1,31}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, code)
);

CREATE TABLE txn_fraud.rule_version (
  id           uuid PRIMARY KEY,
  tenant_id    uuid NOT NULL REFERENCES core.tenant (id),
  rule_id      uuid NOT NULL REFERENCES txn_fraud.rule (id),
  version      int NOT NULL CHECK (version >= 1),
  name         text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  description  text,
  expression   jsonb NOT NULL,
  severity     text NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
  weight       numeric(5, 4) NOT NULL CHECK (weight > 0 AND weight <= 1),
  channels     text[] NOT NULL DEFAULT '{}',
  state        text NOT NULL DEFAULT 'draft' CHECK (state IN (
    'draft', 'dry_run', 'pending_approval', 'active', 'retired')),
  author_id    text NOT NULL,
  approver_id  text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  activated_at timestamptz,
  retired_at   timestamptz,
  UNIQUE (rule_id, version),
  CHECK (approver_id IS NULL OR approver_id <> author_id)
);
-- At most one active version per rule.
CREATE UNIQUE INDEX rule_version_one_active ON txn_fraud.rule_version (rule_id) WHERE state = 'active';

-- The definition of a rule version never changes once written; only its lifecycle does.
CREATE FUNCTION txn_fraud.rule_version_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.expression, NEW.severity, NEW.weight, NEW.channels, NEW.name, NEW.rule_id, NEW.version, NEW.author_id)
     IS DISTINCT FROM
     (OLD.expression, OLD.severity, OLD.weight, OLD.channels, OLD.name, OLD.rule_id, OLD.version, OLD.author_id) THEN
    RAISE EXCEPTION 'a rule version is immutable; create a new version' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER rule_version_immutable BEFORE UPDATE ON txn_fraud.rule_version
  FOR EACH ROW EXECUTE FUNCTION txn_fraud.rule_version_immutable();

-- A snapshot of which rule versions were live, taken whenever that set changes.
CREATE TABLE txn_fraud.ruleset_version (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL REFERENCES core.tenant (id),
  rule_version_ids uuid[] NOT NULL,
  created_by       text NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ruleset_version_latest ON txn_fraud.ruleset_version (tenant_id, created_at DESC);

CREATE TABLE txn_fraud.score (
  id                 uuid PRIMARY KEY,
  tenant_id          uuid NOT NULL REFERENCES core.tenant (id),
  branch_id          uuid,
  transaction_id     uuid,
  source_reference   text NOT NULL,
  account_token      text NOT NULL,
  mode               text NOT NULL CHECK (mode IN ('realtime', 'stream')),
  score              numeric(5, 4) NOT NULL,
  recommendation     text NOT NULL CHECK (recommendation IN ('allow', 'review', 'hold_for_review')),
  ruleset_version_id uuid REFERENCES txn_fraud.ruleset_version (id),
  degraded           boolean NOT NULL DEFAULT false,
  reasons            jsonb NOT NULL DEFAULT '[]'::jsonb,
  latency_ms         real,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX score_stream_once ON txn_fraud.score (tenant_id, transaction_id) WHERE mode = 'stream';
CREATE INDEX score_flagged ON txn_fraud.score (tenant_id, created_at) WHERE recommendation <> 'allow';

-- Ground truth for measuring rules: from the synthetic generator now, from human decisions later.
CREATE TABLE txn_fraud.label (
  tenant_id        uuid NOT NULL REFERENCES core.tenant (id),
  source_reference text NOT NULL,
  label_group      text NOT NULL,
  is_fraud         boolean NOT NULL,
  origin           text NOT NULL CHECK (origin IN ('synthetic', 'decision')),
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, source_reference)
);

CREATE TABLE txn_fraud.backtest_run (
  id                     uuid PRIMARY KEY,
  tenant_id              uuid NOT NULL REFERENCES core.tenant (id),
  rule_version_id        uuid NOT NULL REFERENCES txn_fraud.rule_version (id),
  status                 text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed', 'failed')),
  from_ts                timestamptz NOT NULL,
  to_ts                  timestamptz NOT NULL,
  transactions_evaluated int,
  hits                   int,
  true_positives         int,
  false_positives        int,
  false_negatives        int,
  precision_value        real,
  recall_value           real,
  alerts_per_day         real,
  breakdown              jsonb,
  error                  text,
  created_by             text NOT NULL,
  created_at             timestamptz NOT NULL DEFAULT now(),
  completed_at           timestamptz,
  CHECK (to_ts > from_ts)
);

-- Id-only work queue (see ingestion.batch_queue for why it has no row-level security).
CREATE TABLE txn_fraud.backtest_queue (
  backtest_id uuid PRIMARY KEY,
  tenant_id   uuid NOT NULL,
  status      text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'claimed', 'done')),
  enqueued_at timestamptz NOT NULL DEFAULT now(),
  claimed_at  timestamptz
);

DO $$
DECLARE t record;
BEGIN
  FOR t IN
    SELECT c.oid::regclass AS rel FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'txn_fraud' AND c.relkind = 'r' AND c.relname <> 'backtest_queue'
  LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t.rel);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t.rel);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %s USING (tenant_id = core.current_tenant()) '
      'WITH CHECK (tenant_id = core.current_tenant())', t.rel);
  END LOOP;
END $$;

CREATE POLICY branch_scope ON txn_fraud.score AS RESTRICTIVE
  USING (branch_id IS NULL AND core.branch_scope() IS NULL OR core.branch_visible(branch_id));

GRANT SELECT, INSERT, UPDATE ON txn_fraud.threshold_profile, txn_fraud.rule_version,
  txn_fraud.backtest_run, txn_fraud.backtest_queue, txn_fraud.label TO sentinel_app;
GRANT SELECT, INSERT ON txn_fraud.rule, txn_fraud.ruleset_version, txn_fraud.score TO sentinel_app;
