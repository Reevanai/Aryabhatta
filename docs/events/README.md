# Event contracts

JSON Schema (draft 2020-12). Every event is the envelope in `envelope.schema.json` with a typed
`payload`. Rules are in `ARCHITECTURE.md` section 8.

| Event type | Topic | Payload schema | Phase |
|-----------|-------|----------------|-------|
| `ingestion.transaction.received` | `sentinel.ingestion.transaction.v1` | `transaction.received.schema.json` | 2 |
| `txn_fraud.score.computed` | `sentinel.txn_fraud.score.v1` | `score.computed.schema.json` | 4 |
| `<module>.alert.raised` | `sentinel.cases.alert.v1` | `alert.raised.schema.json` | 4 |
| `cases.case.updated` | `sentinel.cases.case.v1` | `case.updated.schema.json` | 5 |
| `cases.decision.recorded` | `sentinel.cases.decision.v1` | `decision.recorded.schema.json` | 5 |

Events named in the module map but not listed here get their schema in the phase that builds the
module. Payloads carry tokens only, never clear personal data.

The `$id` host `sentinel.invalid` is a placeholder, not a resolvable address.
