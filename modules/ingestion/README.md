# ingestion

Takes parties, accounts, loans, devices and transactions from a bank, validates them, replaces personal data with
tokens, stores canonical records, and emits `ingestion.transaction.received` events.

## Inputs

| Path | How |
|------|-----|
| Batch | `POST /v1/ingestion/batches` with a CSV file. Stored encrypted, queued, processed by the ingest worker. |
| HTTP | `POST /v1/ingestion/events` with up to 500 transactions. Processed in the request. |
| Stream | Kafka topic `sentinel.intake.transaction.v1`: one transaction as JSON per message, header `tenant_id`. |

CSV columns are the field names of `PartyRow`, `AccountRow` and `TransactionInput` in
`domain/models.py`. Transactions use flat `amount` and `currency` columns and an optional
`attributes` column holding a flat JSON object. Load in this order: parties, accounts, loans,
devices, transactions.

## What happens to a row

1. **Validate** against the model. Each rule has a unit test.
2. **Check references**: the customer, branch and account must exist.
3. **Tokenise** identifiers; clear values go to the vault, encrypted.
4. **Store**. Parties, accounts, loans and device links are upserted. Transactions are idempotent on
   `source_reference`: a repeat is counted as a duplicate and ignored.
5. **Emit** one event per new transaction through the outbox.

A row that fails step 1 or 2 is **quarantined**: stored encrypted with the reasons. The reasons
never contain the rejected value. `GET /v1/ingestion/quarantine` lists reasons only.

A file with a bad header, wrong encoding or invalid CSV fails as a whole with a reason.

## Run

The worker is `sentinel-ingest` (`apps/ingest`). In the development stack, `make up` runs it.

## Measured

On the development stack (4 cores, OpenBao key service, single worker), synthetic UPI transactions
with five tokenised fields each:

| File | Result |
|------|--------|
| 20,000 rows | 13.7 s, about 1,460 rows/s |
| 50,000 rows | 33.9 s, about 1,470 rows/s |
| 122,125 rows (generated `demo` data set) | 92.9 s, about 1,310 rows/s |

Reproduce with `uv run python scripts/bench_ingest.py 50000` after `make seed`.

A loan's `loan_id` is stored as given, like a transaction's reference: it is a record key, not
personal data.

## Limits

- **Only `ingestion.transaction.received` is emitted.** `party.upserted` and `account.upserted`
  are added when the graph module needs them (Phase 6).
- **Entity-resolution comparison keys are not produced yet** (Phase 6).
- **One account holder per account.** Joint holders need an `account_holder` table.
- **Quarantined rows cannot be corrected and replayed through the API.** Fix the source and
  resend; status values `corrected` and `discarded` exist but nothing sets them.
- **Stream intake trusts the `tenant_id` header.** Write access to the intake topic must be
  restricted per tenant by broker ACLs.
- **Messages without a usable tenant header are counted, logged without content, and dropped.**
- **A batch is read into memory** (50 MB cap by default).
- **Validation uses Pydantic models**, not Pandera as first planned: per-row errors were needed
  for quarantine.
- **Ingestion needs an institution-wide caller.** Branch-scoped users cannot ingest.
