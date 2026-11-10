# Sentinel — Phase 0 research

Research date: **2026-11-10**. All "last push" and star figures are GitHub API values read on that date.

## 0. How to read this document

### Verification levels

Every factual claim carries one of these tags. Nothing here is written from memory.

| Tag | Meaning |
|-----|---------|
| **[R]** | Read directly from the project's own repository file or page (LICENSE, README, docs) on 2026-11-10. |
| **[G]** | GitHub API metadata (licence SPDX id, stars, last push, language, archived flag) on 2026-11-10. |
| **[S]** | Content of an official-domain page (rbi.org.in, pib.gov.in, cert-in.org.in, fiuindia.gov.in, vendor docs) as returned by web search. The page itself was not opened in full. |
| **[2]** | Secondary source only (law-firm, compliance-vendor or news commentary). **Must be confirmed against the primary text before any code or document relies on it.** |
| **[U]** | Could not verify. Treated as a TODO. |

### Limits of this research pass

1. **`capabilities.txt` was not available** in the repository or the session, so the 185-row
   `capability_matrix.csv` could not be produced. See `capability_matrix.csv` (header only) and
   `capability_matrix_provisional.csv` (decisions for every capability named in the project brief).
2. **Direct access to most primary sites was blocked by the session's network policy** (rbi.org.in,
   fiuindia.gov.in, meity.gov.in, cert-in.org.in, abuse.ch, openphish.com, phishtank.org, neo4j.com,
   memgraph.com). GitHub pages were readable. Regulatory findings are therefore **[S]** or **[2]**,
   never **[R]**. Section 14 lists exactly what still needs a primary read.
3. No candidate was installed or benchmarked. "Maturity" is judged from activity, age, adoption and
   documentation only.

### Headline findings

1. **The regulatory baseline in the brief is out of date.** RBI's Department of Supervision reissued its
   instructions entity-wise on **31 July 2026**. The July 2024 fraud Master Directions and the 2019 UCB
   cyber framework both appear to have been replaced (section 13). Sentinel must map to the 2026
   instruments, and every clause reference must be re-derived from them.
2. **Several baseline-stack components are not open source.** Redpanda (BSL), Vault (BUSL), Memgraph
   (BSL), Marble (Elastic 2.0), FalkorDB (SSPL), and the Enterprise parts of OpenCTI are
   source-available with field-of-use limits. Details and substitutes are in `LICENCES.md`.
3. **Graph database: neither Neo4j Community nor Memgraph is a clean fit.** Memgraph's licence forbids
   distributing or embedding it for third parties, which is what a vendor shipping to banks does.
   Neo4j Community is GPLv3 and single-database with no RBAC, which conflicts with per-tenant
   isolation. Recommendation in section 5.
4. **No transaction-monitoring engine is worth adopting whole.** Tazama is the only permissively
   licensed one, and it is a Node.js/NATS system for ISO 20022 payment switches. Build the rule engine;
   borrow Tazama's rule → typology → decision structure as a design idea.
5. **Free threat feeds are not free for this product.** URLhaus, OpenPhish community and PhishTank all
   restrict or gate commercial use. Feed adapters ship disabled; the bank brings its own licence.
6. **Open-source liveness and deepfake models are not adequate or not licensable** for production KYC.
   These are INTEGRATE-VENDOR with a reference implementation for demo only.

---

## 1. Transaction monitoring and decision engines (groups 3, 4)

| Candidate | What it is | Licence | Last push | Stars | Lang | Verdict |
|-----------|-----------|---------|-----------|-------|------|---------|
| [Tazama](https://github.com/tazama-lf) | Linux Foundation real-time transaction monitoring. Pipeline of rule processors → typology processor → decision processor. ISO 20022 API over HTTP; NATS message bus; PostgreSQL; Node.js processors [R]. Ships a rule studio and a case-management system as separate repos [G]. | Apache-2.0 [R] (docs repo; `typology-processor` has no SPDX id in the API [G]) | 2026-11 (org active) [G] | 57 (docs), 4–26 per code repo [G] | TypeScript | **Reference design only.** |
| [Marble](https://github.com/checkmarble/marble) | Real-time and batch decision engine for fraud/AML with a scenario builder and case manager. | **Elastic License 2.0** on both repos [R] | 2026-11-10 (backend) [G] | 619 / 52 [G] | Go | **Do not embed.** |
| [Jube](https://github.com/jube-home/aml-fraud-transaction-monitoring) | Rules, velocity, sanctions, supervised and unsupervised ML, case management, multi-tenant [R]. | **AGPL-3.0** [R] | 2026-10-27 [G] | 116 [G] | C# | **Do not use.** |
| [GoRules Zen](https://github.com/gorules/zen) | Embeddable business-rules engine (decision tables, expressions) with Python bindings. | MIT [G] | 2026-11-10 [G] | 2,118 [G] | Rust | **Evaluate in Phase 1** as the expression evaluator. |
| [Ballerine](https://github.com/ballerine-io/ballerine) | KYC/KYB workflow and risk-decisioning platform. | Mixed: Elastic 2.0 default, some MIT, chosen per file [R] | 2026-10-09 [G] | 2,451 [G] | TypeScript | **Do not embed.** |

Notes:

- Tazama's low star counts understate it (Linux Foundation, Gates Foundation backing [S]) but the code
  repositories are small and its NATS deployment has no authentication by default [R]. Adopting it would
  mean running a second language stack and a second bus beside the baseline. It also has no tenant model
  matching bank → branch.
- Marble's licence bars providing the software to third parties "as a hosted or managed service" [R].
  A multi-tenant Sentinel deployment for several co-operative banks is exactly that.
- AGPL (Jube) would oblige Sentinel to publish its own source to every bank user if the code were linked
  or modified. Not compatible with a commercial product.

**Decision: BUILD** the rule engine, velocity counters, scoring service and mule scoring in Python.
Rules are stored as a versioned JSON expression tree (no arbitrary code), which gives dry-run and
backtest for free. Zen is the only dependency candidate; decide in an ADR after a latency spike.

---

## 2. Sanctions and PEP screening (group 7)

| Candidate | What it is | Licence | Last push | Stars | Lang | Verdict |
|-----------|-----------|---------|-----------|-------|------|---------|
| [yente](https://github.com/opensanctions/yente) | Screening and matching API over OpenSanctions-format data. Needs Elasticsearch or OpenSearch [R]. | MIT (code) [R] | 2026-11-10 [G] | 185 [G] | Python | **ADOPT** behind an adapter. |
| [OpenSanctions data](https://www.opensanctions.org/docs/commercial/faq) | Consolidated sanctions, PEP and watchlist dataset. | **CC BY-NC 4.0**; any use inside a business, including compliance screening, needs a paid data licence [S] | n/a | 873 (pipeline repo) [G] | — | **INTEGRATE-VENDOR** (data licence is the bank's or ours to buy). |
| [Moov Watchman](https://github.com/moov-io/watchman) | Sanctions search service. Built-in loaders for OFAC SDN and non-SDN, US CSL, FinCEN 311, EU consolidated, UK and UN lists; also ingests CSV and OpenSanctions Senzing-format files [R]. | Apache-2.0 [R] | 2026-11-10 [G] | 519 [G] | Go | **ADOPT** as the default, licence-free path. |

Notes:

- Watchman has no built-in authentication and is not meant to be internet-facing [R]. It runs on the
  internal network only, behind the Sentinel screening service.
- Watchman's built-in lists are public government lists, so no data licence is needed for them.
  It has **no PEP data**. PEP coverage needs OpenSanctions (licensed) or another vendor.
- India-specific lists (UAPA-designated individuals and entities, and the lists RBI's KYC Directions
  require screening against) were **not verified** in this pass **[U]**. They are loaded through
  Watchman's CSV path once the official sources are confirmed.

**Decision:** a `ScreeningProvider` interface with two adapters: Watchman (default, public lists) and
yente (when a data licence exists). Tests run against a small synthetic list. No OpenSanctions data is
committed to the repo.

---

## 3. Entity resolution (group 5)

| Candidate | What it is | Licence | Last push | Stars | Lang | Verdict |
|-----------|-----------|---------|-----------|-------|------|---------|
| [Splink](https://github.com/moj-analytical-services/splink) | Probabilistic record linkage (Fellegi-Sunter) executed as SQL. Backends: DuckDB (default), Spark, PostgreSQL. README announces Splink 5 [R]. | MIT [R] | 2026-11-09 [G] | 2,570 [G] | Python | **ADOPT.** |
| [Zingg](https://github.com/zinggAI/zingg) | ML-based entity resolution on Spark. | **AGPL-3.0** [G] | 2026-11-03 [G] | 1,269 [G] | Java | **Do not use.** |

Splink fits the stack: it needs no Spark at co-operative-bank scale, produces match weights that can be
shown to an investigator, and is maintained by the UK Ministry of Justice. Use the DuckDB backend for
batch linkage over tokenised attributes, and write clusters back to PostgreSQL.

---

## 4. Anomaly detection and graph ML (group 9)

| Candidate | Licence | Last push | Stars | Verdict |
|-----------|---------|-----------|-------|---------|
| [PyOD](https://github.com/yzhao062/pyod) | BSD-2-Clause [G] | 2026-10-28 [G] | 10,085 [G] | **ADOPT** (unsupervised detectors). |
| [PyTorch Geometric](https://github.com/pyg-team/pytorch_geometric) | MIT [G] | 2026-11-06 [G] | 24,139 [G] | **ADOPT** (graph ML, phase 8). |
| [DGL](https://github.com/dmlc/dgl) | Apache-2.0 [G] | **2025-07-31** [G] | 14,289 [G] | **Reject.** No pushes for 15 months. |
| [NetworkX](https://github.com/networkx/networkx) | BSD-3-Clause [R] | 2026-11-04 [G] | 17,392 [G] | **ADOPT** (in-process graph algorithms). |
| [scikit-learn](https://github.com/scikit-learn/scikit-learn) | BSD-3-Clause [G] | 2026-11-10 [G] | 68,123 [G] | **ADOPT.** |
| [LightGBM](https://github.com/lightgbm-org/LightGBM) | MIT [G] | 2026-11-10 [G] | 18,962 [G] | **ADOPT.** |
| [SHAP](https://github.com/shap/shap) | MIT [G] | 2026-11-08 [G] | 25,854 [G] | **ADOPT.** |
| [MLflow](https://github.com/mlflow/mlflow) | Apache-2.0 [G] | 2026-11-10 [G] | 28,745 [G] | **ADOPT.** |
| [Evidently](https://github.com/evidentlyai/evidently) | Apache-2.0 [G] | 2026-09-29 [G] | 8,053 [G] | **ADOPT** (drift). |
| [python-igraph](https://github.com/igraph/python-igraph) | **GPL-2.0** [G] | 2026-11-03 [G] | 1,465 [G] | **Avoid** (copyleft, linked in-process). |
| [leidenalg](https://github.com/vtraag/leidenalg) | **GPL-3.0** [G] | 2026-11-02 [G] | 804 [G] | **Avoid** (same reason). |

Notes:

- The GitHub API returned LightGBM under the `lightgbm-org` organisation, not `microsoft`. The repo path
  is recorded as observed. Pin the PyPI package, not a repo URL.
- Community detection uses NetworkX's Louvain implementation. Leiden is better quality but the only
  maintained Python implementations are GPL. If Louvain proves inadequate on the synthetic rings, run
  Leiden inside the graph database instead of linking a GPL library.

---

## 5. Graph database (group 5)

| Candidate | Licence | Key limits | Last push | Stars | Verdict |
|-----------|---------|-----------|-----------|-------|---------|
| [Neo4j Community](https://github.com/neo4j/neo4j) | GPL-3.0 [G] | Single instance, one standard database, no clustering, no online backup, no role-based access control [S]. | 2026-11-06 [G] | 17,421 [G] | **Optional adapter.** |
| [Neo4j GDS](https://github.com/neo4j/graph-data-science) | GPL-3.0 unless a commercial agreement applies [R] | Community GDS reported capped at four CPU cores (Neo4j community forum, not official docs) [2]. | 2026-11-06 [G] | 826 [G] | Optional. |
| [Memgraph](https://github.com/memgraph/memgraph) | **BSL 1.1** with additional use grant [R] | Production use for internal business purposes only. **Not allowed: embedding or distributing to third parties, giving third parties access as a service, DBaaS** [R]. Change licence Apache-2.0; the change date is printed as "2030-21-10", which is not a valid date [R]. | 2026-11-10 [G] | 4,657 [G] | **Reject for a product we ship.** |
| [Memgraph MAGE](https://github.com/memgraph/mage) | Apache-2.0, **archived** [G] | Being folded into the main repo under BSL (announced 2025-11-07, mirror of the repo notice) [2]. | 2026-01-23 [G] | 331 [G] | — |
| [FalkorDB](https://github.com/FalkorDB/FalkorDB) | **SSPL-1.0** [R] | Offering it as a service requires releasing the entire service stack under SSPL [R]. | 2026-11-10 [G] | 10,494 [G] | **Reject.** |
| [Kùzu](https://github.com/kuzudb/kuzu) | MIT [G] | **Archived**; last push 2025-10-10 [G]. | — | 4,023 [G] | **Reject.** |
| [Apache AGE](https://github.com/apache/age) | Apache-2.0 [G] | PostgreSQL extension giving openCypher queries. PostgreSQL 16 support and algorithm coverage **not verified [U]**. | 2026-11-04 [G] | 4,936 [G] | **Evaluate in Phase 1.** |

**Recommendation (needs an ADR and your decision, because it changes the baseline):**

1. Define a `GraphStore` port in Phase 1. Nothing above it knows which engine is underneath.
2. **Default implementation: PostgreSQL.** Edges and nodes live in tenant-scoped tables under the same
   row-level security as everything else. Bounded traversals (k-hop, rapid movement, cycles up to a fixed
   length) run as recursive SQL. Whole-graph analytics (components, communities, risk propagation) run as
   batch jobs in NetworkX over a per-tenant extract. Co-operative-bank graphs are small enough for this:
   a bank with 500,000 accounts and 50 million transactions a year yields an account graph that fits in
   memory on one worker. This claim will be measured on synthetic data in phase 6, not assumed.
3. **Optional implementation: Neo4j Community**, reached over Bolt with the Apache-2.0 Python driver,
   deployed per tenant by the bank. This is the choice between the two engines named in the brief:
   GPLv3 puts no limit on use, while Memgraph's grant forbids the distribution model Sentinel has.
   One Neo4j instance per tenant is required, because Community has one database and no RBAC.
4. Apache AGE is the candidate to replace recursive SQL if query ergonomics become a problem. It keeps
   the single-database, single-RLS model.

Console visualisation: [Cytoscape.js](https://github.com/cytoscape/cytoscape.js) (MIT, 11,278 stars,
pushed 2026-11-05) or [Sigma.js](https://github.com/jacomyal/sigma.js) (MIT, 12,229 stars, pushed
2026-11-09) [G]. Cytoscape.js has the richer layout and interaction set for investigator graphs of a few
hundred nodes; Sigma.js is the WebGL option for thousands.

---

## 6. Threat intelligence and OSINT (group 6)

### Platforms

| Candidate | Licence | Last push | Stars | Verdict |
|-----------|---------|-----------|-------|---------|
| [MISP](https://github.com/MISP/MISP) | AGPL-3.0 [G] | 2026-11-09 [G] | 6,657 [G] | **Integrate over its API only.** Never link or modify. |
| [OpenCTI](https://github.com/OpenCTI-Platform/opencti) | Community: Apache-2.0. Files with an EE header: proprietary Filigran Enterprise licence [R] | 2026-11-10 [G] | 10,288 [G] | **Integrate over its API** (STIX 2.1). Community features only. |

Both run as separate services owned by the bank or its SOC. Sentinel reads and writes indicators through
their HTTP APIs. This keeps AGPL obligations with MISP itself.

### Public feeds — terms of use

| Feed | What the terms say | Verdict |
|------|-------------------|---------|
| abuse.ch URLhaus / ThreatFox | Community APIs are free "under fair use"; companies with commercial or for-profit needs "may require a paid subscription" to the commercial API sold through Spamhaus [S]. Bulk downloads now need a free Auth-Key [2]. The data licence (often described as CC0) was **not confirmed [U]**. | Adapter, **off by default**, key supplied by the bank. |
| OpenPhish community feed | "Non-commercial use only" per the official feed repository; premium tiers are paid annual subscriptions [S]. | Adapter for the **paid** feed only. Community feed not used. |
| PhishTank | New-user registration reported closed since 2020 and still closed in June 2026 [2]. Terms not read [U]. | **Do not depend on it.** Adapter stub only. |
| CERT-In advisories | Reuse terms not checked [U]. | Link to advisories; do not republish text. |

**Decision:** build the indicator model, enrichment pipeline and STIX import/export. Ship a synthetic
feed for tests and demos. Every real feed is a configured adapter with an explicit licence-acknowledgement
field per tenant. Dark-web intelligence is an interface for a licensed vendor feed only; no crawler.

---

## 7. Security monitoring and vulnerability management (group 10)

| Candidate | Licence | Last push | Stars | Verdict |
|-----------|---------|-----------|-------|---------|
| [Wazuh](https://github.com/wazuh/wazuh) | Mixed: GPL-2.0 for most of the tree, AGPL-3.0-or-later for `src/engine/` [R] | 2026-11-10 [G] | 17,648 [G] | **Integrate outputs over its API.** |
| [DefectDojo](https://github.com/DefectDojo/django-DefectDojo) | BSD-3-Clause [G] | 2026-11-10 [G] | 5,214 [G] | **Integrate outputs over its API.** |

Sentinel does not bundle either. It ingests alerts and findings, maps them to incidents and controls,
and drives the reporting workflow. Both are mature and widely deployed.

---

## 8. Identity, workflow, streaming, secrets (platform core)

| Candidate | Licence | Last push | Stars | Verdict |
|-----------|---------|-----------|-------|---------|
| [Keycloak](https://github.com/keycloak/keycloak) | Apache-2.0 [G] | 2026-11-10 [G] | 37,785 [G] | **ADOPT.** |
| [Temporal server](https://github.com/temporalio/temporal) | MIT [G] | 2026-11-10 [G] | 24,299 [G] | **ADOPT.** |
| [Temporal Python SDK](https://github.com/temporalio/sdk-python) | MIT [G] | 2026-11-10 [G] | 1,247 [G] | **ADOPT.** |
| [Redpanda](https://github.com/redpanda-data/redpanda) | **BSL 1.1** core; enterprise features under the Redpanda Community License [R] | 2026-10-08 [G] | 12,676 [G] | **Dev only.** See below. |
| [Apache Kafka](https://github.com/apache/kafka) | Apache-2.0 [G] | 2026-11-10 [G] | 34,198 [G] | **Production default.** |
| [HashiCorp Vault](https://github.com/hashicorp/vault) | **BUSL 1.1**, licensor IBM [R] | 2026-11-10 [G] | 36,495 [G] | **Replace.** |
| [OpenBao](https://github.com/openbao/openbao) | MPL-2.0 [G] | 2026-11-09 [G] | 8,958 [G] | **ADOPT** (Vault-API-compatible fork). |
| [SOPS](https://github.com/getsops/sops) | MPL-2.0 [G] | 2026-11-09 [G] | 23,671 [G] | **ADOPT** (config secrets in git). |
| [Valkey](https://github.com/valkey-io/valkey) | BSD-3-Clause [G] | 2026-11-10 [G] | 27,672 [G] | **ADOPT** if a cache or counter store is needed. |

Notes:

- Redpanda's grant forbids a "Streaming or Queueing Service" where third parties cause topics to be
  created [R]. Sentinel does not do that, so use is permitted. It stays the dev-compose broker because it
  is one container with no ZooKeeper or KRaft set-up. Sentinel codes only against the Kafka protocol, and
  the Helm chart defaults to Apache Kafka so that nothing source-available is shipped to a bank.
- Vault's BUSL allows internal production use but not hosted or embedded competitive offerings [R].
  OpenBao removes the question. The secrets interface is the Vault HTTP API either way.

---

## 9. KYC, OCR, liveness, deepfake (group 1)

| Candidate | Licence | Last push | Stars | Verdict |
|-----------|---------|-----------|-------|---------|
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | Code Apache-2.0; **no licence statement found for pretrained models** [R] | 2026-09-16 [G] | 92,513 [G] | **ADOPT with a flag** (confirm model terms). |
| [Tesseract](https://github.com/tesseract-ocr/tesseract) | Apache-2.0 [G] | 2026-11-09 [G] | 77,436 [G] | **ADOPT** as the conservative default. |
| [docTR](https://github.com/mindee/doctr) | Apache-2.0 [G] | 2026-11-09 [G] | 6,428 [G] | Alternative. |
| [Silent-Face-Anti-Spoofing](https://github.com/minivision-ai/Silent-Face-Anti-Spoofing) | Apache-2.0 (repo sidebar) [R] | **2023-10-03** [G] | 1,844 [G] | **Demo reference only.** |
| [InsightFace](https://github.com/deepinsight/insightface) | Code MIT; **models non-commercial research only**, commercial use needs a paid licence [R] | 2026-10-19 [G] | 30,180 [G] | **Do not use the models.** |
| [DeepFace](https://github.com/serengil/deepface) | MIT wrapper [G]; wraps third-party models whose weights carry their own terms [2] | 2026-11-09 [G] | 23,666 [G] | **Avoid** until each weight's licence is checked. |
| [DeepfakeBench](https://github.com/SCLBD/DeepfakeBench) | **CC BY-NC 4.0** [R] | 2025-08-20 [G] | 1,138 [G] | **Do not use.** |
| [Presidio](https://github.com/data-privacy-stack/presidio) | MIT [G] | 2026-11-08 [G] | 11,811 [G] | **ADOPT** (PII detection in free text and logs). |

Notes:

- Silent-Face's open model is the small one; the README states the high-precision model is not
  open-sourced [R]. It is a single-frame passive detector with no updates since 2023. No evidence was
  found of ISO/IEC 30107-3 testing for any open-source liveness model [2].
- RBI's KYC Directions for UCBs require the V-CIP application to have "face liveness / spoof detection as
  well as face matching technology with high degree of accuracy" [S]. An unmaintained research model does
  not meet that bar.
- The GitHub API returned Presidio under `data-privacy-stack`, not `microsoft`. Recorded as observed.

**Decision:** OCR is ADOPT-OSS. Face match, liveness, deepfake and injection-attack detection are
**INTEGRATE-VENDOR** behind `LivenessProvider` and `FaceMatchProvider` interfaces. A reference
implementation on Silent-Face exists for the demo and is labelled not for production. CKYC, Aadhaar
e-KYC, DigiLocker and PAN verification are adapters with recorded synthetic responses.

---

## 10. Device and behaviour (group 2)

| Candidate | Licence | Last push | Stars | Verdict |
|-----------|---------|-----------|-------|---------|
| [FingerprintJS](https://github.com/fingerprintjs/fingerprintjs) | MIT per GitHub API [G]. Earlier major versions were under other terms; **confirm the licence of the exact version pinned** [U]. | 2026-11-05 [G] | 28,722 [G] | **Reference only.** |

**Decision: BUILD** a small demo SDK (web first) that collects a documented, consented signal set from
inside the bank's own app and posts it to Sentinel. Signals carry a DPDP purpose tag. No third-party
fingerprinting library is bundled. Commercial behavioural biometrics are INTEGRATE-VENDOR.

---

## 11. Data quality, features, synthetic data

| Candidate | Licence | Last push | Stars | Verdict |
|-----------|---------|-----------|-------|---------|
| [Pandera](https://github.com/unionai-oss/pandera) | MIT [G] | 2026-11-10 [G] | 4,522 [G] | **ADOPT** (batch validation and quarantine). |
| [Great Expectations](https://github.com/fivetran/great_expectations) | Apache-2.0 [G] | 2026-11-10 [G] | 11,923 [G] | Heavier alternative; not chosen. |
| [Feast](https://github.com/feast-dev/feast) | Apache-2.0 [G] | 2026-11-10 [G] | 7,357 [G] | Not chosen; the brief asks for a lightweight SQL pattern. |
| [IBM AMLSim](https://github.com/IBM/AMLSim) | Apache-2.0 [G] | 2025-09-17 [G] | 412 [G] | **Ideas only** (typology shapes). |
| [PaySim](https://github.com/EdgarLopezPhD/PaySim) | **GPL-3.0** [G] | 2021-05-30 [G] | 134 [G] | **Do not use.** |

**Decision: BUILD** the synthetic generator. Neither simulator models Indian rails (UPI, IMPS, NEFT,
RTGS, AePS, cash), co-operative-bank customer mixes or NBFC lending. AMLSim's published typology shapes
(fan-in, fan-out, cycle, scatter-gather) are reused as concepts, not code.

---

## 12. Case management, policy, console (groups 8, 11, 12)

No open-source case-management system was found that is permissively licensed, multi-tenant and
workflow-driven. Tazama's is new (12 stars) and tied to its own pipeline [G]. **BUILD** on Temporal.

| Candidate | Licence | Last push | Stars | Verdict |
|-----------|---------|-----------|-------|---------|
| [Open Policy Agent](https://github.com/open-policy-agent/opa) | Apache-2.0 [G] | 2026-11-10 [G] | 12,453 [G] | Candidate for fine-grained authorisation. |
| [Cerbos](https://github.com/cerbos/cerbos) | Apache-2.0 [G] | 2026-11-10 [G] | 4,654 [G] | Alternative. |

Keycloak roles plus PostgreSQL row-level security cover Phase 2. A policy engine is added only if
attribute-based rules (for example, branch-scoped investigators) cannot be expressed that way.

---

## 13. Indian regulatory context

Official sources only where possible. **Every item below must be re-read from the primary PDF before
Sentinel cites a paragraph number or encodes a deadline.** Until then, clause references in code and
documents are `TODO(verify)`.

### 13.1 RBI — fraud risk management

| Instrument | Status | Source |
|-----------|--------|--------|
| Master Directions on Fraud Risk Management in UCBs / StCBs / CCBs, RBI/DOS/2024-25/119, 15 July 2024 | Issued; replaced 36 earlier circulars. Board-approved policy reviewed at least every three years; Special Committee of the Board; early-warning framework for Tier 3 and 4 UCBs and StCBs/CCBs with deposits above ₹1,000 crore; show-cause notice with at least 21 days to respond before classifying fraud; report to RBI within 14 days of classification [S]. **Appears superseded on 31 July 2026.** | [RBI notification 12703](https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=12703), [press release](https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=58294), [FAQ, 22 Apr 2025](https://www.rbi.org.in/commonman/english/scripts/FAQs.aspx?Id=3763) |
| Reserve Bank of India (Urban Co-operative Banks – Fraud Risk Management) Directions, 2026, 31 July 2026 | Listed among the DoS directions issued that day [2]. Reference number, text and whether the 2024 provisions carried over: **[U]**. | [CAalley list](https://caalley.com/corporate-laws-updates/64-rbi-directions-released-on-july-31-2026) (secondary) |
| Sister directions of the same date | Commercial Banks RBI/DoS/2026-27/412; Small Finance Banks /421; Local Area Banks /446; Regional Rural Banks /454; AIFIs /457. Rural Co-operative Banks and NBFC fraud directions listed, reference numbers not found [2]. | [Commercial banks PDF on rbidocs](https://rbidocs.rbi.org.in/rdocs/notification/PDFs/412MD5B6CA712E156413BACDEA9DD5CF5D1EF.PDF) (official, not opened) |
| RBI (UCBs – Miscellaneous) Supervisory Directions, 2026, DoS.CO.PPG.37/11.01.005/2026-27, 31 July 2026 | Has a "Fraud Prevention Measures" chapter. Repeal of earlier instructions is by circular DoS.CO.PPG.66/11.01.005/2026-27 of the same date [S]. | [RBI PDF 443MD](https://rbidocs.rbi.org.in/rdocs/notification/PDFs/443MD40CA627030B44C78A2B73D3CE3BE4E98.PDF) |
| Department of Regulation consolidation, 28 November 2025 | 244 Master Directions issued; 9,445 circulars repealed or withdrawn [S]. | [RBI press release 61705](https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=61705) |
| Unauthorised electronic banking transactions — amendment directions, 24 June 2026 | Final amendments to the Commercial Banks and Payments Banks Responsible Business Conduct directions: wider coverage of fraudulent electronic transactions and a small-value compensation mechanism [S]. Applicability to co-operative banks **[U]**. | [RBI press release 63011](https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=63011), [PIB 2244478](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2244478) |

### 13.2 RBI — cyber security for UCBs

| Instrument | Status | Source |
|-----------|--------|--------|
| Comprehensive Cyber Security Framework for UCBs – A Graded Approach, RBI/2019-20/129, 31 December 2019 | Four levels by digital depth. Level IV adds a Cyber Security Operations Centre. The RBI PDF now carries a "Withdrawn" watermark [S]. | [RBI notification 11772](https://www.rbi.org.in/scripts/NotificationUser.aspx?Mode=0&Id=11772) |
| RBI (Urban Co-operative Banks – Cybersecurity, Technology: Risk, Resilience and Assurance Framework) Directions, 2026, RBI/DoS/2026-27/437, 31 July 2026 | Reported as the replacement, in force from issue, retaining a four-level graded approach with cumulative chapters; CISO from Level II, C-SOC at Level IV; cyber incidents reported to RBI within six hours via DAKSH [2]. **Primary text not read [U].** | [TaxGuru summary](https://taxguru.in/rbi/rbi-issues-cybersecurity-technology-risk-framework-directions-2026-urban-op-banks.html) (secondary) |

Group 10's control library must be built from RBI/DoS/2026-27/437, not the 2019 circular.

### 13.3 RBI — KYC

Reserve Bank of India (Urban Co-operative Banks – Know Your Customer) Directions, 2025,
RBI/DOR/2025-26/291, 28 November 2025, amended 29 December 2025 and 18 September 2026 [S].
V-CIP requires liveness and spoof detection, geo-tagged and time-stamped recording, and blocking of
non-Indian or spoofed IP addresses. The entity that last uploaded a CKYCR record is responsible for
verifying identity or address [S]. Risk-categorisation and periodic-updation paragraphs: **[U]**.
Source: [RBI PDF](https://rbidocs.rbi.org.in/rdocs/notification/PDFs/291MDE108E59AC43D4C13B2D06598B296C877.PDF).

### 13.4 CERT-In

Directions under section 70B(6) of the IT Act, No. 20(3)/2022-CERT-In, 28 April 2022 [S]:

- Report the incident types in Annexure I within **6 hours** of noticing them or being told of them.
- Channels: email incident@cert-in.org.in, phone 1800-11-4949, fax 1800-11-6969.
- Partial information may be filed in the window and completed later (FAQ).
- Keep logs of all ICT systems for a rolling **180 days** within Indian jurisdiction.
- Synchronise clocks to NIC or NPL NTP servers.

Sources: [Directions PDF](https://www.cert-in.org.in/PDF/CERT-In_Directions_70B_28.04.2022.pdf),
[FAQ](https://www.cert-in.org.in/PDF/FAQs_on_CyberSecurityDirections_May2022.pdf),
[PIB 1820904](https://www.pib.gov.in/PressReleasePage.aspx?PRID=1820904).
The Annexure I incident list and the reporting form fields were not read **[U]**.

A UCB therefore has two six-hour clocks: CERT-In, and RBI via DAKSH (13.2). The incident workflow
models them as separate obligations.

### 13.5 DPDP Act 2023 and DPDP Rules 2025

- Rules notified as G.S.R. 846(E); official pages give 13 and 14 November 2025 [S].
  Sources: [PIB 2190014](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2190014),
  [PIB explainer PDF](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf).
- 18-month phased compliance timeline [S]. Commentary reads Rule 1 as: rule 4 from 13 November 2026;
  rules 3, 5 to 16, 22 and 23 from 13 May 2027 [2].
- Breach: tell affected individuals promptly in plain language [S]. Commentary reads Rule 7 as: notify
  the Board without delay, then detailed information within 72 hours [2].
- Security safeguards (Rule 6) and retention (Rule 8) reported to include retaining logs for at least one
  year [2].
- Penalty for failing to notify a breach: up to ₹200 crore [S].
- Exemptions relevant to banks (processing under law, prevention and detection of offences): **[U]**.

Design consequences that do not depend on the unverified parts: purpose tag on every personal-data
field, per-purpose retention schedule, erasure and legal-hold workflow, access logging with at least one
year retention, breach register with separate clocks per regulator.

### 13.6 FIU-IND reporting

- FINGate 2.0 is the filing portal. Report types: CTR, STR, NTR, CCR, CBWTR, PTR, with sub-formats per
  reporting-entity category [S].
- The FINGate 2.0 "Reporting Format" introduction (version 1.14 in the copy found) maps report types to
  format codes; codes seen include TC2 (CTR, branch cash transactions), CB1 (CBWTR) and GT1 (STR, general
  transactions) [S]. The **full code table for banks and NBFCs, field layouts, file format and schema
  were not read [U]**.
- A batch holds CTRs or STRs, not both; each record is a report [S].
- CTR: cash transactions above ₹10 lakh, and integrally connected cash transactions exceeding ₹10 lakh in
  a month; due by the 15th of the following month [S, older RBI circulars].
- STR: within seven working days of forming suspicion; attempted transactions included; no tipping off [S].
- CBWTR: cross-border wire transfers above ₹5 lakh, monthly by the 15th [S, FIU-IND annual report 2019-20].

Sources: [Reporting_Format.pdf](https://fiuindia.gov.in/pdfs/quicklinks/Reporting_Format.pdf),
[FINGate reports user manual](https://fiuindia.gov.in/pdfs/quicklinks/User_Manual_FINGate_Reports.pdf),
[FINGate 2.0 user manual](https://fiuindia.gov.in/pdfs/quicklinks/User_Manual_FINGate2.0.pdf).

Phase 7 builds the STR/CTR **preparation workflow and an internal canonical report model**. The
FINGate export serialiser stays a `TODO(verify)` stub until the current format specification is read.
No field names or codes will be invented.

### 13.7 CKYC (CERSAI)

CERSAI operates the Central KYC Records Registry. Search and Download API access requires the
institution to register, upload the public key of its digital signature and whitelist its IP address;
test and live environments register separately [S]. API request and response formats: **[U]**.
Source: [CKYCRR user manual v1.12, 28 March 2023](https://www.ckycindia.in/ckyc/assets/doc/User_Manual_1.12.pdf).
Sentinel ships an adapter interface and a synthetic stub only.

### 13.8 RBIH MuleHunter.AI and DPIP

- MuleHunter.AI is RBIH's AI/ML mule-account detection solution, live in **26 banks** and being scaled
  [S, [PIB 2244478](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2244478)].
- I4C and RBIH signed an MoU (May 2026) to share Suspect Registry identifiers with systems such as
  MuleHunter.ai [S, [PIB 2260277](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2260277)].
- DPIP (Digital Payments Intelligence Platform) is described in RBI speeches as a proposed near-real-time
  fraud-intelligence sharing platform still being developed [S]. No public API specification was found.
- No public MuleHunter specification or code exists; GitHub repositories with that name are unrelated
  student projects [G].

Sentinel's mule scoring is independent. `MuleHunterAdapter` and `DpipAdapter` are interfaces with
synthetic stubs.

### 13.9 I4C and DoT

- I4C Suspect Registry launched 10 September 2024 with banks. To 30 June 2026: 30.48 lakh suspect
  identifiers received from banks, 32.08 lakh Layer 1 mule accounts shared, ₹25,698 crore of transactions
  declined [S].
- CFCFRMS (helpline 1930) launched 2021 for immediate reporting of financial cyber fraud [S].
- DoT Financial Fraud Risk Indicator: a Medium / High / Very High risk tag on mobile numbers, delivered
  through the Digital Intelligence Platform. RBI advised banks, including co-operative banks, on
  30 June 2025 to integrate it [S, [PIB 2141616](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2141616)].

FRI is a direct input to mule and beneficiary risk scoring. All three are adapters with stubs.

### 13.10 NPCI / UPI and NABARD

- NPCI: the circular index was found ([UPI circulars](https://www.npci.org.in/circulars/upi)) but no
  circular text on fraud-monitoring obligations or message specifications could be read **[U]**. UPI
  technical specifications are available to members only. Sentinel models UPI transactions from publicly
  described fields in synthetic data and takes real data through the CBS/switch adapter.
- NABARD: reporting requirements for StCBs and CCBs were not researched in this pass **[U]**.

---

## 14. Open verification items before dependent phases start

| # | Item | Blocks |
|---|------|--------|
| 1 | Receive `capabilities.txt`; produce the 185-row matrix | Phase 1 |
| 2 | Read RBI (UCB – Fraud Risk Management) Directions, 2026 in full; record reference number and paragraph map | Phases 4, 5, 7 |
| 3 | Read RBI/DoS/2026-27/437 in full; extract the control list per level | Phase 11 |
| 4 | Read the equivalent 2026 fraud and cyber directions for NBFCs, SFBs and rural co-operative banks | Phases 7, 11 |
| 5 | Read DPDP Rules G.S.R. 846(E) from the Gazette: Rule 1 commencement, Rules 6, 7, 8, schedules | Phase 2 |
| 6 | Read current FIU-IND FINGate 2.0 format specification and any published schema | Phase 7 |
| 7 | Read CERT-In Annexure I and the incident reporting form | Phase 11 |
| 8 | Read abuse.ch, OpenPhish and PhishTank terms pages | Phase 9 |
| 9 | Confirm Apache AGE support for PostgreSQL 16; confirm Neo4j GDS Community limits from Neo4j docs | Phase 1 ADR |
| 10 | Confirm PaddleOCR model licence; confirm licence of the pinned FingerprintJS version | Phase 10 |
| 11 | Identify official sources for India-specific sanctions lists | Phase 7 |
| 12 | Legal review of `LICENCES.md` by counsel | Before any release |

Items 2 to 8 need either network access to the listed government and vendor sites from this
environment, or the PDFs added to the repository under `research/sources/`.
