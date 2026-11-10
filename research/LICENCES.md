# Sentinel — licence compatibility check

Checked 2026-11-10. Verification tags as in `RESEARCH.md`: **[R]** licence file read, **[G]** GitHub API
SPDX id, **[S]** vendor page via search, **[U]** unverified.

This is an engineering assessment, not legal advice. Counsel must review it before any release.

## 1. Assumptions about Sentinel

These drive every verdict. If one is wrong, the verdicts change.

1. Sentinel is **proprietary commercial software**. Its source is not published.
2. It is delivered two ways: **installed in a bank's own environment** (we distribute container images and
   Helm charts), and possibly **hosted by us for several banks** (multi-tenant service).
3. Bank staff use it over a network.

## 2. Rules

1. **Depend, do not vendor.** Third-party code enters only as a pinned package or a container image.
   No source is copied into the repo.
2. **Permissive licences** (MIT, BSD, Apache-2.0, ISC) are allowed as linked dependencies.
3. **Weak copyleft** (MPL-2.0, LGPL) is allowed unmodified. Changes go upstream, not into a fork.
4. **Strong copyleft** (GPL, AGPL) code is **never imported, linked or modified**. A GPL or AGPL
   *service* may run as a separate process that Sentinel reaches over a network API, and only when the
   bank deploys it. Sentinel images do not contain it.
5. **Source-available** licences (BSL/BUSL, Elastic 2.0, SSPL, vendor community licences) are **not
   shipped to customers and not used in a hosted offering** without written sign-off. Use in local
   development is allowed where the licence permits it.
6. **Non-commercial** licences (CC BY-NC, "research only" model weights, non-commercial feeds) are
   **not used at all**, including in demos.
7. **Model weights and data are licensed separately from code.** Each is checked on its own.
8. CI runs a licence scan (`pip-licenses`, `license-checker`) against an allow-list and fails on
   anything else. Added in Phase 2.

## 3. Flagged licences — explicit decisions

### AGPL-3.0

| Component | Verdict | Reason |
|-----------|---------|--------|
| Jube [R] | **Rejected** | Would have to be embedded or modified to be useful. |
| Zingg [G] | **Rejected** | Linked library. Splink (MIT) does the job. |
| MISP [G] | **API integration only** | Runs as the bank's own service. Sentinel calls its REST API and ships no MISP code. `PyMISP` client licence to be checked before use [U]. |
| Wazuh `src/engine/` [R] | **API integration only** | Same pattern. |

### GPL

| Component | Licence | Verdict | Reason |
|-----------|---------|---------|--------|
| Neo4j Community | GPL-3.0 [G] | **Optional, bank-deployed, over Bolt** | Separate server process. The Python driver is Apache-2.0 [U — confirm at pin time]. Sentinel images do not include Neo4j. We do not write server-side plugins or procedures, which would be derivative works. |
| Neo4j GDS | GPL-3.0 [R] | Same | Same. |
| Wazuh (rest of tree) | GPL-2.0 [R] | **API integration only** | Separate service. |
| python-igraph | GPL-2.0 [G] | **Rejected** | In-process import. |
| leidenalg | GPL-3.0 [G] | **Rejected** | In-process import. |
| PaySim | GPL-3.0 [G] | **Rejected** | Not needed. |

### SSPL-1.0

| Component | Verdict | Reason |
|-----------|---------|--------|
| FalkorDB [R] | **Rejected** | Offering it as part of a service triggers release of the whole service stack under SSPL. |

### Business Source Licence (BSL / BUSL 1.1)

| Component | What the grant forbids | Verdict |
|-----------|-----------------------|---------|
| Memgraph Community [R] | Embedding or distributing to third parties; giving third parties access as a service; DBaaS. | **Rejected.** Both of Sentinel's delivery models are forbidden uses. |
| Redpanda core [R] | Offering a streaming or queueing service where third parties create topics. | **Local development only.** Sentinel's use is permitted, but production charts default to Apache Kafka so no BSL software is shipped. |
| HashiCorp Vault ≥ 1.15 [R] | Hosted or embedded offerings that compete with the licensor's paid product. | **Replaced by OpenBao (MPL-2.0).** A bank may point Sentinel at its own Vault. |

### Elastic License 2.0

| Component | Verdict | Reason |
|-----------|---------|--------|
| Marble [R] | **Rejected** | No providing to third parties as a hosted or managed service. |
| Ballerine (default) [R] | **Rejected** | Same, and the per-file licence mix is hard to audit. |
| Elasticsearch (needed by yente) | **Use OpenSearch** | yente supports OpenSearch [R]. OpenSearch licence to be confirmed at pin time [U]. |

### Vendor enterprise or community licences

| Component | Verdict |
|-----------|---------|
| OpenCTI files with an EE header [R] | **Not used.** Community (Apache-2.0) API features only. |
| Redpanda Community License features [R] | **Not used.** |

### Non-commercial

| Component | Licence | Verdict |
|-----------|---------|---------|
| OpenSanctions data [S] | CC BY-NC 4.0 | **Not used without a purchased data licence.** No data in the repo. |
| InsightFace models [R] | Non-commercial research | **Rejected.** |
| DeepfakeBench [R] | CC BY-NC 4.0 | **Rejected.** |
| OpenPhish community feed [S] | Non-commercial | **Rejected.** Paid feed via adapter only. |
| abuse.ch community APIs [S] | Fair use; commercial use may need a paid subscription | **Off by default.** Enabled only with the bank's own key and acknowledgement. |

## 4. Approved dependency set

| Component | Role | Licence | Linkage |
|-----------|------|---------|---------|
| FastAPI, Pydantic, SQLAlchemy | Services | MIT [U — confirm at pin time] | Linked |
| PostgreSQL 16 | System of record | PostgreSQL Licence [U] | Service |
| Apache Kafka | Streaming (production) | Apache-2.0 [G] | Service |
| Temporal server and Python SDK | Workflows | MIT [G] | Service / linked |
| Keycloak | Identity | Apache-2.0 [G] | Service |
| OpenBao | Secrets | MPL-2.0 [G] | Service |
| SOPS | Config secrets | MPL-2.0 [G] | Tool |
| scikit-learn | ML | BSD-3-Clause [G] | Linked |
| LightGBM | ML | MIT [G] | Linked |
| PyOD | Anomaly detection | BSD-2-Clause [G] | Linked |
| PyTorch Geometric | Graph ML | MIT [G] | Linked |
| NetworkX | Graph algorithms | BSD-3-Clause [R] | Linked |
| SHAP | Explanations | MIT [G] | Linked |
| MLflow | Model registry | Apache-2.0 [G] | Service / linked |
| Evidently | Drift | Apache-2.0 [G] | Linked |
| Splink | Entity resolution | MIT [R] | Linked |
| Pandera | Validation | MIT [G] | Linked |
| Presidio | PII detection | MIT [G] | Linked |
| Moov Watchman | Sanctions search | Apache-2.0 [R] | Service |
| yente | Screening API | MIT [R] | Service |
| Tesseract | OCR | Apache-2.0 [G] | Tool |
| PaddleOCR | OCR | Apache-2.0 code [R]; models [U] | Linked, pending model check |
| Cytoscape.js | Network visualisation | MIT [G] | Linked |
| Next.js, React | Console | MIT [U] | Linked |
| OpenTelemetry, Prometheus | Observability | Apache-2.0 [U] | Linked / service |
| Grafana | Dashboards | AGPL-3.0 [U] | **Service only, bank-deployed, unmodified.** Confirm before bundling in any chart. |
| DefectDojo | Vulnerability findings source | BSD-3-Clause [G] | External service |
| OpenCTI Community | Threat intel | Apache-2.0 [R] | External service |

Rows tagged [U] are well-known licences that were not re-checked in this pass. They are confirmed
when versions are pinned in Phase 2, and the CI licence scan enforces them from then on.

## 5. Unresolved

1. Memgraph BSL change date is printed as "2030-21-10" [R]. Irrelevant while Memgraph is rejected.
2. Redpanda's licence text gives both "four years" and "fifth anniversary" for conversion [R].
   Irrelevant while Redpanda is development-only.
3. Whether distributing a Helm chart that *references* (but does not contain) a GPL or AGPL image
   creates any obligation. Our position is that it does not; counsel to confirm.
4. Grafana's AGPL status and whether to ship dashboards as JSON only, leaving Grafana to the bank.
