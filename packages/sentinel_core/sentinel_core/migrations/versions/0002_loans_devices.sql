-- Loans and customer devices (Phase 3).

ALTER TABLE ingestion.batch_job DROP CONSTRAINT batch_job_dataset_check;
ALTER TABLE ingestion.batch_job ADD CONSTRAINT batch_job_dataset_check
  CHECK (dataset IN ('parties', 'accounts', 'loans', 'devices', 'transactions'));

CREATE TABLE ingestion.loan (
  id                   uuid PRIMARY KEY,
  tenant_id            uuid NOT NULL REFERENCES core.tenant (id),
  branch_id            uuid NOT NULL REFERENCES core.branch (id),
  loan_ref             text NOT NULL,
  party_id             uuid NOT NULL REFERENCES ingestion.party (id),
  account_id           uuid NOT NULL REFERENCES ingestion.account (id),
  product              text NOT NULL CHECK (product IN (
    'gold', 'personal', 'two_wheeler', 'agri_kcc', 'msme', 'housing', 'vehicle', 'other')),
  principal            numeric(18, 2) NOT NULL CHECK (principal > 0),
  annual_interest_rate numeric(6, 3) NOT NULL CHECK (annual_interest_rate BETWEEN 0 AND 100),
  tenure_months        int NOT NULL CHECK (tenure_months BETWEEN 1 AND 480),
  disbursed_on         date NOT NULL,
  emi_amount           numeric(18, 2) CHECK (emi_amount >= 0),
  status               text NOT NULL CHECK (status IN ('active', 'closed', 'overdue', 'written_off')),
  days_past_due        int NOT NULL DEFAULT 0 CHECK (days_past_due >= 0),
  currency             char(3) NOT NULL DEFAULT 'INR',
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, loan_ref)
);
CREATE INDEX loan_party ON ingestion.loan (tenant_id, party_id);

-- Which customers have used which device. A device seen on several customers is a signal.
CREATE TABLE ingestion.device_link (
  id           uuid PRIMARY KEY,
  tenant_id    uuid NOT NULL REFERENCES core.tenant (id),
  device_token text NOT NULL,
  party_id     uuid NOT NULL REFERENCES ingestion.party (id),
  platform     text NOT NULL CHECK (platform IN ('android', 'ios', 'web', 'other')),
  os_version   text,
  rooted       boolean NOT NULL DEFAULT false,
  first_seen   timestamptz NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, device_token, party_id)
);
CREATE INDEX device_link_device ON ingestion.device_link (tenant_id, device_token);

DO $$
DECLARE t regclass;
BEGIN
  FOREACH t IN ARRAY ARRAY['ingestion.loan'::regclass, 'ingestion.device_link'::regclass] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %s USING (tenant_id = core.current_tenant()) '
      'WITH CHECK (tenant_id = core.current_tenant())', t);
  END LOOP;
END $$;

CREATE POLICY branch_scope ON ingestion.loan AS RESTRICTIVE USING (core.branch_visible(branch_id));

GRANT SELECT, INSERT, UPDATE ON ingestion.loan, ingestion.device_link TO sentinel_app;
