# Security Posture

Plain-language overview of the Document-Driver's security architecture—what's in place today vs. what's on the roadmap.

---

## Authentication & Access Control

### Today

- **JWT Bearer tokens** issued on login; expires in 12 hours (configurable).
- **Three RBAC roles:** 
  - **Admin:** full control (upload, process, review, approve, reject, manage users/webhooks/auto-approve).
  - **Reviewer:** can upload, process, and approve/reject documents.
  - **Viewer:** read-only (list documents and schemas).
- Passwords hashed with **Argon2** (salted, modern best practice).
- Protected endpoints validate JWT and enforce role at the route level; unauthenticated requests receive `401`.

### Roadmap

- **SSO (Single Sign-On):** OAuth2 / SAML integration for enterprise identity providers—required for healthcare/regulated verticals.

---

## Multi-Tenant Isolation

### Today

- **Strict org scoping, fail-closed:** Every data row (documents, fields, configs, audit logs, users) belongs to an `Organization`. 
- **Database-layer enforcement:** A SQLAlchemy listener (`with_loader_criteria`) auto-filters every `SELECT` to the authenticated user's org. A query with no org context raises `RuntimeError` immediately—data cannot leak silently.
- **Org ID in JWT:** The access token carries the user's org, set at login. All requests are scoped to that org in middleware *before* any endpoint code runs.
- **Cross-org lookups return 404:** A user who tries to access another org's document gets a 404 (existence is not disclosed); it is structurally impossible to read, edit, or delete data outside the user's org.
- **Verified by test suite:** Two-org isolation matrix (`test_org_isolation.py`) confirms org 1 and org 2 cannot see each other's data across all REST endpoints (documents, schemas, webhooks, auto-approve, users, webhooks, etc.).

### Roadmap

- **Multi-org superadmin:** Today, roles (admin/reviewer/viewer) are org-scoped; there is no cross-org superadmin role. Org creation is manual (database seed). A true superadmin and self-service org creation will follow.

---

## Transport Security

### Today

- **HTTPS everywhere:** 
  - Frontend (Vercel) serves over TLS.
  - Backend (Render) serves over TLS.
  - All browser-to-API traffic is encrypted in transit.
- **CORS:** Pinned to the deployed frontend origin(s)—requests from other domains are rejected.
- **Rate limiting:** Requests are throttled (default 20 per minute) to mitigate brute-force attacks.

---

## Storage & Data at Rest

### Today

- **Database:** Postgres (production) or SQLite (local dev). Both configured by `DATABASE_URL`. Databases are stored at rest unencrypted (plaintext on disk). Network access is controlled by the hosting provider (Render/Neon firewall rules).
- **Object storage:** Document files (PDFs, images) are stored in R2 (Cloudflare S3-compatible storage) or local disk. Files are stored unencrypted at rest.
- **Backups:** Handled by the hosting provider (Render automatic backups, Neon automatic snapshots)—encrypted in transit to backup storage, unencrypted at rest in backup files.

### Roadmap

- **Encryption-at-rest:** Encrypted database columns (Postgres `pgcrypto` or application-level AES) for sensitive fields (PII, extracted values). Requires key rotation and key management infrastructure.

---

## PII Handling

### Today

- **Display-only redaction toggle:** Schema fields can be marked `"pii": true`. In the reviewer UI, PII fields are masked with `••••` during review—**for human readability only**.
- **Critical limitation:** Values are **NOT encrypted at rest**—they are stored in clear text in Postgres and exported in clear text (JSON / CSV). The redaction toggle only hides them from the UI; anyone with database access reads the real values.
- **Audit trail:** All edits, approvals, and exports are logged with user email, timestamp, and action—but logs themselves contain no sensitive data (only field names and action types, not values).

### Roadmap

- **True PII-redaction pipeline (B13):** Encrypt PII values on ingestion, decrypt only for human review (with access logs), and support permanent deletion. Requires application-level encryption with user-scoped or org-scoped keys.
- **Data retention & deletion:** Define retention windows per data class (e.g., approved documents 7 years, logs 1 year); automatic purge on retention expiry.

---

## Extraction & External APIs

### Today

- **OCR via AWS Textract (optional):** When `OCR_BACKEND=textract`, page images are sent to AWS Textract for text extraction. Images are transmitted over TLS and deleted by AWS after processing (per AWS policy). Enable only if OCR accuracy is critical; the default fallback uses PyMuPDF (local, no external calls).
- **LLM calls (OpenAI/Gemini):** Text extracts are sent to OpenAI (`gpt-4o` by default) or Google Gemini for entity extraction. Transmitted over TLS. Respect your LLM provider's data retention policy (OpenAI retains for 30 days by default, deletable on request).

### Roadmap

- **On-prem or airgapped extraction:** Option to run all extraction models (classifier, extractor, validator) locally without external API calls—for healthcare or air-gapped deployments.

---

## Access & Audit

### Today

- **Structured audit log:** Every document action (upload, process, approve, reject, export, reopen, etc.) is logged with timestamp, actor (user email), action, and details.
- **Queryable audit trail:** `GET /admin/metrics` surfaces error rates, stuck documents, and stage latency. Logs are emitted as JSON to stdout (searchable in Render logs).
- **User management:** Admins can create, deactivate, and change roles for users in their org; no cross-org user access.

### Roadmap

- **Audit log export:** Bulk download of audit trail for compliance reporting (HIPAA, SOX, etc.).
- **Alerting:** Email/Slack alerts on high error rates, failed webhooks, or suspicious access patterns.

---

## Compliance Gaps (Healthcare)

Document-Driver is **not ready for HIPAA/HITECH/BAA today** without the roadmap items below. For regulated verticals:

1. ✅ Audit logs present, but no log retention policy.
2. ❌ **Encryption-at-rest** (required for HIPAA).
3. ❌ **SSO / SAML** (required for enterprise compliance).
4. ❌ **Data retention & deletion** (required for HIPAA 60-day purge, GDPR right-to-delete).
5. ❌ **True PII redaction** (application-level encryption, not display-only masking).
6. ❌ **Business Associate Agreement (BAA)** (contractual).

---

## Summary: In Place Today vs. Roadmap

| Feature | Status | Notes |
|---------|--------|-------|
| JWT Authentication | ✅ In place | 12h expiry, Argon2 hashing |
| RBAC (admin/reviewer/viewer) | ✅ In place | Org-scoped roles |
| Multi-tenant isolation | ✅ In place | Fail-closed DB scoping, tested 2-org matrix |
| HTTPS transport | ✅ In place | Vercel + Render (TLS) |
| Database (Postgres/SQLite) | ✅ In place | Unencrypted at rest |
| Object storage (R2/local) | ✅ In place | Unencrypted at rest |
| Rate limiting | ✅ In place | 20 req/min default |
| PII display-only masking | ✅ In place | **Not encrypted—stored in clear** |
| Audit logging | ✅ In place | JSON-structured, searchable |
| OCR (AWS Textract, optional) | ✅ In place | Data sent to AWS; default local fallback |
| LLM extraction (OpenAI/Gemini) | ✅ In place | Data sent to LLM provider; configurable |
| **Encryption-at-rest** | 🛣️ Roadmap | Application-level AES for sensitive fields |
| **SSO / SAML** | 🛣️ Roadmap | For enterprise + healthcare compliance |
| **Data retention/deletion** | 🛣️ Roadmap | Automated purge per policy |
| **True PII redaction (B13)** | 🛣️ Roadmap | Encrypted PII on ingestion, decrypt for review |
| **BAA / HIPAA ready** | 🛣️ Roadmap | After encryption + retention + SSO |

---

## Questions for Prospects

**On authentication:** "Do you use SSO or SAML?" (If yes, that's roadmap.)

**On data residency:** "Do page images and text extracts need to stay on-premises?" (Today, OCR and LLM calls are optional; no external calls required if `OCR_BACKEND=none` and `OPENAI_API_KEY` is unset.)

**On compliance:** "Do you operate in a regulated vertical (healthcare, finance)?" (If yes: encryption-at-rest, SSO, and retention/deletion are prerequisites; BAA will follow.)

**On PII:** "Do you need encrypted PII at rest?" (Today: display-only masking in the UI; values are stored in clear. True encryption is roadmap.)
