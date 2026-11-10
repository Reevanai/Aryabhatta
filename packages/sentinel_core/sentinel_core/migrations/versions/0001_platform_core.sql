-- Sentinel platform core and ingestion schema (Phase 2).
-- Run by the owner role. The application never connects as the owner.

DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['sentinel_app', 'sentinel_vault', 'sentinel_relay'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN', r);
    END IF;
  END LOOP;
END $$;

CREATE SCHEMA core;
CREATE SCHEMA vault;
CREATE SCHEMA ingestion;

GRANT USAGE ON SCHEMA core TO sentinel_app, sentinel_vault, sentinel_relay;
GRANT USAGE ON SCHEMA ingestion TO sentinel_app;
GRANT USAGE ON SCHEMA vault TO sentinel_vault;

-- Tenant context helpers -------------------------------------------------

CREATE FUNCTION core.current_tenant() RETURNS uuid
LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$;

CREATE FUNCTION core.branch_scope() RETURNS uuid[]
LANGUAGE sql STABLE AS
$$ SELECT string_to_array(NULLIF(current_setting('app.branch_ids', true), ''), ',')::uuid[] $$;

-- True when the session is institution-wide or the row's branch is in scope.
CREATE FUNCTION core.branch_visible(row_branch uuid) RETURNS boolean
LANGUAGE sql STABLE AS
$$ SELECT core.branch_scope() IS NULL OR row_branch = ANY (core.branch_scope()) $$;

-- Global reference data (no tenant data; no row-level security) ----------

CREATE TABLE core.purpose (
  code        text PRIMARY KEY,
  description text NOT NULL
);

CREATE TABLE core.data_catalogue (
  field_type     text PRIMARY KEY CHECK (field_type ~ '^[a-z]+$'),
  classification text NOT NULL CHECK (classification IN ('direct_identifier', 'indirect_identifier', 'financial')),
  description    text NOT NULL
);

CREATE TABLE core.field_purpose (
  field_type   text NOT NULL REFERENCES core.data_catalogue,
  purpose_code text NOT NULL REFERENCES core.purpose,
  PRIMARY KEY (field_type, purpose_code)
);

-- Maps an OIDC issuer to a tenant. Read before a tenant context exists.
CREATE TABLE core.tenant_issuer (
  issuer    text PRIMARY KEY,
  tenant_id uuid NOT NULL,
  jwks_uri  text NOT NULL
);

-- Tenant-scoped tables ----------------------------------------------------

CREATE TABLE core.tenant (
  id               uuid PRIMARY KEY,
  tenant_id        uuid GENERATED ALWAYS AS (id) STORED,
  name             text NOT NULL,
  institution_type text NOT NULL CHECK (institution_type IN (
    'urban_cooperative_bank', 'state_cooperative_bank', 'district_central_cooperative_bank',
    'small_finance_bank', 'nbfc', 'other')),
  status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'offboarding')),
  created_at       timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE core.tenant_issuer ADD FOREIGN KEY (tenant_id) REFERENCES core.tenant (id);

CREATE TABLE core.branch (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL REFERENCES core.tenant (id),
  code             text NOT NULL CHECK (length(code) BETWEEN 1 AND 32),
  name             text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  parent_branch_id uuid REFERENCES core.branch (id),
  status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, code)
);

CREATE TABLE core.tenant_key (
  tenant_id   uuid NOT NULL REFERENCES core.tenant (id),
  purpose     text NOT NULL CHECK (purpose IN ('pii', 'hmac', 'file', 'audit')),
  version     int  NOT NULL,
  wrapped_key bytea NOT NULL,
  kek_ref     text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, purpose, version)
);

CREATE TABLE core.retention_policy (
  tenant_id    uuid NOT NULL REFERENCES core.tenant (id),
  purpose_code text NOT NULL REFERENCES core.purpose,
  retain_days  int CHECK (retain_days > 0),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, purpose_code)
);

CREATE TABLE core.audit_chain_head (
  tenant_id uuid PRIMARY KEY REFERENCES core.tenant (id),
  sequence  bigint NOT NULL,
  hash      text NOT NULL
);

CREATE TABLE core.audit_event (
  tenant_id     uuid NOT NULL REFERENCES core.tenant (id),
  sequence      bigint NOT NULL,
  id            uuid NOT NULL,
  occurred_at   timestamptz NOT NULL,
  actor_id      text NOT NULL,
  actor_type    text NOT NULL CHECK (actor_type IN ('user', 'service')),
  action        text NOT NULL,
  resource      text NOT NULL,
  purpose       text,
  details       jsonb NOT NULL DEFAULT '{}'::jsonb,
  previous_hash text NOT NULL,
  hash          text NOT NULL,
  PRIMARY KEY (tenant_id, sequence)
);
CREATE INDEX audit_event_time ON core.audit_event (tenant_id, occurred_at);

CREATE TABLE core.audit_anchor (
  tenant_id   uuid NOT NULL REFERENCES core.tenant (id),
  sequence    bigint NOT NULL,
  hash        text NOT NULL,
  signature   text NOT NULL,
  anchored_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, sequence)
);

CREATE FUNCTION core.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege'; END $$;

CREATE TRIGGER audit_event_append_only BEFORE UPDATE OR DELETE ON core.audit_event
  FOR EACH ROW EXECUTE FUNCTION core.reject_mutation();
CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON core.audit_event
  FOR EACH STATEMENT EXECUTE FUNCTION core.reject_mutation();
CREATE TRIGGER audit_anchor_append_only BEFORE UPDATE OR DELETE ON core.audit_anchor
  FOR EACH ROW EXECUTE FUNCTION core.reject_mutation();

CREATE TABLE core.outbox (
  id           uuid PRIMARY KEY,
  tenant_id    uuid NOT NULL REFERENCES core.tenant (id),
  topic        text NOT NULL,
  key          text NOT NULL,
  event        jsonb NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz
);
CREATE INDEX outbox_unpublished ON core.outbox (created_at, id) WHERE published_at IS NULL;

CREATE TABLE vault.pii_token (
  tenant_id   uuid NOT NULL REFERENCES core.tenant (id),
  token       text NOT NULL,
  field_type  text NOT NULL REFERENCES core.data_catalogue,
  ciphertext  bytea NOT NULL,
  key_version int NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, token)
);

-- Ingestion ----------------------------------------------------------------

CREATE TABLE ingestion.batch_job (
  id                  uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL REFERENCES core.tenant (id),
  dataset             text NOT NULL CHECK (dataset IN ('parties', 'accounts', 'transactions')),
  status              text NOT NULL CHECK (status IN (
    'received', 'validating', 'loading', 'completed', 'completed_with_errors', 'failed')),
  file_ref            text NOT NULL,
  file_sha256         text NOT NULL,
  records_total       int NOT NULL DEFAULT 0,
  records_loaded      int NOT NULL DEFAULT 0,
  records_quarantined int NOT NULL DEFAULT 0,
  error               text,
  created_by          text NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  completed_at        timestamptz,
  UNIQUE (tenant_id, dataset, file_sha256)
);

-- Work queue for the ingest worker. Holds ids only, so it has no row-level security:
-- the worker must find work before it knows which tenant to act as.
CREATE TABLE ingestion.batch_queue (
  batch_id    uuid PRIMARY KEY,
  tenant_id   uuid NOT NULL,
  status      text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'claimed', 'done', 'dead')),
  attempts    int NOT NULL DEFAULT 0,
  enqueued_at timestamptz NOT NULL DEFAULT now(),
  claimed_at  timestamptz
);
CREATE INDEX batch_queue_ready ON ingestion.batch_queue (enqueued_at) WHERE status IN ('queued', 'claimed');

CREATE TABLE ingestion.quarantine_record (
  id             uuid PRIMARY KEY,
  tenant_id      uuid NOT NULL REFERENCES core.tenant (id),
  batch_id       uuid REFERENCES ingestion.batch_job (id),
  source         text NOT NULL CHECK (source IN ('batch', 'http', 'stream')),
  dataset        text NOT NULL,
  row_number     int,
  raw_ciphertext bytea NOT NULL,
  key_version    int NOT NULL,
  errors         jsonb NOT NULL,
  status         text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'corrected', 'discarded')),
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX quarantine_list ON ingestion.quarantine_record (tenant_id, created_at, id);

CREATE TABLE ingestion.party (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL REFERENCES core.tenant (id),
  party_token       text NOT NULL,
  party_type        text NOT NULL CHECK (party_type IN ('individual', 'organisation')),
  name_token        text NOT NULL,
  pan_token         text,
  phone_token       text,
  email_token       text,
  address_token     text,
  dob_token         text,
  kyc_risk_category text CHECK (kyc_risk_category IN ('low', 'medium', 'high')),
  onboarded_on      date,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, party_token)
);

CREATE TABLE ingestion.account (
  id            uuid PRIMARY KEY,
  tenant_id     uuid NOT NULL REFERENCES core.tenant (id),
  branch_id     uuid NOT NULL REFERENCES core.branch (id),
  account_token text NOT NULL,
  party_id      uuid NOT NULL REFERENCES ingestion.party (id),
  product_type  text NOT NULL CHECK (product_type IN (
    'savings', 'current', 'term_deposit', 'loan', 'overdraft', 'other')),
  status        text NOT NULL CHECK (status IN ('active', 'dormant', 'frozen', 'closed')),
  opened_on     date,
  currency      char(3) NOT NULL DEFAULT 'INR',
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, account_token)
);

CREATE TABLE ingestion.transaction (
  id                         uuid NOT NULL,
  tenant_id                  uuid NOT NULL REFERENCES core.tenant (id),
  branch_id                  uuid NOT NULL,
  source_reference           text NOT NULL,
  channel                    text NOT NULL CHECK (channel IN (
    'upi', 'imps', 'neft', 'rtgs', 'card', 'cash', 'cheque', 'aeps', 'internal', 'other')),
  direction                  text NOT NULL CHECK (direction IN ('debit', 'credit')),
  amount                     numeric(18, 2) NOT NULL CHECK (amount > 0),
  currency                   char(3) NOT NULL,
  booked_at                  timestamptz NOT NULL,
  account_id                 uuid NOT NULL,
  account_token              text NOT NULL,
  party_token                text NOT NULL,
  counterparty_account_token text,
  counterparty_vpa_token     text,
  counterparty_bank_code     text,
  device_token               text,
  ip_token                   text,
  merchant_category          text,
  status                     text NOT NULL CHECK (status IN ('attempted', 'completed', 'failed', 'reversed')),
  attributes                 jsonb NOT NULL DEFAULT '{}'::jsonb,
  ingested_at                timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id, booked_at),
  UNIQUE (tenant_id, source_reference, booked_at)
) PARTITION BY RANGE (booked_at);

CREATE TABLE ingestion.transaction_default PARTITION OF ingestion.transaction DEFAULT;
CREATE INDEX transaction_account_time ON ingestion.transaction (tenant_id, account_id, booked_at);

-- Creates the monthly partition for a date if it does not exist. Partitions get no grants:
-- the application reaches them only through the parent, where row-level security applies.
CREATE FUNCTION ingestion.ensure_transaction_partition(for_day date) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
  first_day date := date_trunc('month', for_day)::date;
  part text := 'transaction_' || to_char(first_day, 'YYYYMM');
BEGIN
  IF to_regclass('ingestion.' || part) IS NULL THEN
    PERFORM pg_advisory_xact_lock(hashtext('ingestion.transaction.partition'));
    IF to_regclass('ingestion.' || part) IS NULL THEN
      EXECUTE format(
        'CREATE TABLE ingestion.%I PARTITION OF ingestion.transaction FOR VALUES FROM (%L) TO (%L)',
        part, first_day, (first_day + interval '1 month')::date);
    END IF;
  END IF;
END $$;
REVOKE ALL ON FUNCTION ingestion.ensure_transaction_partition(date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ingestion.ensure_transaction_partition(date) TO sentinel_app;

-- Row-level security -------------------------------------------------------
-- Every table with a tenant_id column gets a forced tenant policy, except the two
-- id-only lookup tables documented above.

DO $$
DECLARE t record;
BEGIN
  FOR t IN
    SELECT c.oid::regclass AS rel
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
    WHERE n.nspname IN ('core', 'vault', 'ingestion')
      AND c.relkind IN ('r', 'p')
      AND NOT c.relispartition
      AND c.oid NOT IN ('core.tenant_issuer'::regclass, 'ingestion.batch_queue'::regclass)
  LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t.rel);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t.rel);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %s USING (tenant_id = core.current_tenant()) '
      'WITH CHECK (tenant_id = core.current_tenant())', t.rel);
  END LOOP;
END $$;

-- Branch scope: restrictive policies narrow what a branch-scoped session can see.
CREATE POLICY branch_scope ON core.branch AS RESTRICTIVE
  USING (core.branch_visible(id));
CREATE POLICY branch_scope ON ingestion.account AS RESTRICTIVE
  USING (core.branch_visible(branch_id));
CREATE POLICY branch_scope ON ingestion.transaction AS RESTRICTIVE
  USING (core.branch_visible(branch_id));

-- The relay publishes for all tenants. It can see the outbox and nothing else.
CREATE POLICY relay_all ON core.outbox FOR ALL TO sentinel_relay USING (true) WITH CHECK (true);

-- Grants ----------------------------------------------------------------------

GRANT SELECT ON core.purpose, core.data_catalogue, core.field_purpose, core.tenant_issuer TO sentinel_app;
GRANT SELECT ON core.data_catalogue TO sentinel_vault;
GRANT SELECT ON core.tenant, core.tenant_key, core.retention_policy TO sentinel_app;
GRANT SELECT, INSERT, UPDATE ON core.branch TO sentinel_app;
GRANT SELECT, UPDATE ON core.audit_chain_head TO sentinel_app;
GRANT SELECT, INSERT ON core.audit_event, core.audit_anchor TO sentinel_app;
GRANT SELECT, INSERT ON core.outbox TO sentinel_app;
GRANT SELECT, UPDATE, DELETE ON core.outbox TO sentinel_relay;
GRANT SELECT, INSERT ON vault.pii_token TO sentinel_vault;
GRANT SELECT, INSERT, UPDATE ON ingestion.batch_job, ingestion.batch_queue,
  ingestion.quarantine_record, ingestion.party, ingestion.account TO sentinel_app;
GRANT SELECT, INSERT ON ingestion.transaction TO sentinel_app;

-- Reference data ----------------------------------------------------------------

INSERT INTO core.purpose (code, description) VALUES
  ('fraud_prevention', 'Detecting and investigating fraud'),
  ('aml_compliance', 'Anti-money-laundering monitoring and reporting'),
  ('kyc', 'Customer identification and due diligence'),
  ('security_monitoring', 'Monitoring systems and channels for security events'),
  ('service_operations', 'Operating and supporting the platform');

INSERT INTO core.data_catalogue (field_type, classification, description) VALUES
  ('cust', 'direct_identifier', 'Customer identifier in the source system'),
  ('name', 'direct_identifier', 'Name of a person or organisation'),
  ('pan', 'direct_identifier', 'Permanent Account Number'),
  ('phone', 'direct_identifier', 'Phone number'),
  ('email', 'direct_identifier', 'Email address'),
  ('addr', 'direct_identifier', 'Postal address'),
  ('dob', 'indirect_identifier', 'Date of birth or incorporation'),
  ('acct', 'financial', 'Account number'),
  ('vpa', 'financial', 'Virtual payment address'),
  ('dev', 'indirect_identifier', 'Device identifier'),
  ('ip', 'indirect_identifier', 'IP address');

INSERT INTO core.field_purpose (field_type, purpose_code)
SELECT c.field_type, p.code
FROM core.data_catalogue c
CROSS JOIN core.purpose p
WHERE p.code IN ('fraud_prevention', 'aml_compliance')
   OR (p.code = 'kyc' AND c.field_type IN ('cust', 'name', 'pan', 'phone', 'email', 'addr', 'dob'))
   OR (p.code = 'security_monitoring' AND c.field_type IN ('dev', 'ip'));
