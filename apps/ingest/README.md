# ingest

Worker process with three roles, run together or separately:

| Role | What it does |
|------|--------------|
| `batches` | Claims queued batch files and loads them. A batch whose worker stopped is retried after 10 minutes, up to three attempts. |
| `intake` | Consumes the Kafka intake topic. Commits offsets after the database transaction. |
| `relay` | Publishes outbox rows to Kafka, at least once, in insertion order. |

```bash
sentinel-ingest --roles batches,intake,relay
```

Limits: the Kafka loops are verified against the running stack and by tests of the functions
they call, not by an automated test of the process itself. No metrics yet.
