# Security & Privacy

## Overview

This document describes the security architecture, access control model, audit event catalog, PHI handling rules, and data retention design for the AcuityBridge Safety & Escalation Orchestrator.

**DISCLAIMER:** This software is not a medical device. The security controls described here support decision-support workflow governance. Production deployments require additional security review, penetration testing, and compliance validation specific to each partner's requirements.

---

## 1. Threat Model (High-Level)

| Threat Actor | Attack Surface | Potential Impact | Mitigations |
|-------------|---------------|-----------------|-------------|
| **External attacker** | API endpoints, network perimeter | Unauthorized data access; service disruption | TLS termination at load balancer; VCN network segmentation; input validation; rate limiting |
| **Insider (malicious)** | Direct database access; API with valid credentials | Data exfiltration; audit log tampering; policy manipulation | RBAC enforcement; append-only audit log with hash chain; least-privilege IAM; separation of duties (CLINICIAN cannot manage policies; ADMIN cannot acknowledge escalations) |
| **Insider (accidental)** | Misconfigured policy; incorrect threshold | Missed escalation; false positive alerts | Policy validation on registration (Pydantic models enforce constraints); threshold ordering enforcement; multi-tenant isolation prevents cross-org contamination |
| **Compromised device** | Participant mobile device; wearable integration | Spoofed check-in data; false biomarker readings | Data treated as supplementary signals, not clinical measurements; all elevated flags require human clinician review regardless of data source |
| **Supply chain** | Third-party dependencies (Pydantic, PyYAML) | Code injection; data leakage | Minimal dependency footprint (2 runtime dependencies); pinned versions; CI pipeline validates on every commit |

---

## 2. Access Control Model (RBAC)

Four roles with enforced permission boundaries:

| Action | PARTICIPANT | CLINICIAN | ADMIN | AUDITOR |
|--------|:-----------:|:---------:|:-----:|:-------:|
| Submit check-in | Yes | -- | -- | -- |
| View own data | Yes | -- | -- | -- |
| View escalation cases | -- | Yes | Yes | Yes |
| Acknowledge escalation | -- | Yes | -- | -- |
| Resolve escalation | -- | Yes | -- | -- |
| Manage partner policies | -- | -- | Yes | -- |
| Query audit log | -- | Yes | Yes | Yes |
| Export audit log | -- | -- | Yes | Yes |

**Separation of duties:**
- Clinicians who acknowledge escalations cannot modify policies
- Administrators who manage policies cannot acknowledge or resolve escalations
- Auditors have read-only access to audit data; they cannot modify any operational state

See `acuitybridge/rbac.py` for the implementation.

---

## 3. Audit Log Events

The following events are recorded in the append-only, tamper-evident audit log:

| Event Type | Trigger | Recorded Data |
|-----------|---------|---------------|
| `SIGNAL_EVALUATED` | Check-in processed by signal evaluator | Participant ID, flag level, evaluation reasons |
| `ESCALATION_OPENED` | Elevated flag triggers escalation | Case ID, participant ID, flag level, indicators |
| `CLINICIAN_NOTIFIED` | Clinician alert sent | Case ID, clinician ID, notification channel |
| `ESCALATION_ACKNOWLEDGED` | Clinician acknowledges case | Case ID, clinician ID, timestamp |
| `ESCALATION_RESOLVED` | Clinician resolves case with notes | Case ID, clinician ID, resolution notes |
| `ESCALATION_TIMED_OUT` | SLA exceeded without acknowledgment | Case ID, SLA seconds, elapsed seconds |
| `CRISIS_INTERFACE_TRIGGERED` | Crisis resource interface invoked per policy | Case ID, target name, route result |
| `AUTOMATED_INTERACTION_SUSPENDED` | Automated content blocked for participant | Participant ID |
| `AUTOMATED_INTERACTION_RESUMED` | Automated content resumed after resolution | Participant ID |
| `POLICY_REGISTERED` | New partner policy added | Org ID, policy summary |
| `POLICY_UPDATED` | Existing policy modified | Org ID, changed fields |
| `CONSENT_GRANTED` | Participant provides consent | Participant ID, consent type |
| `CONSENT_REVOKED` | Participant revokes consent | Participant ID, revocation scope |
| `DATA_ACCESSED` | Participant data accessed by authorized role | Accessor ID, accessor role, data scope |
| `AUDIT_EXPORTED` | Audit log exported for review | Org ID, export time range, entry count |

---

## 4. PHI Handling Rules

### No PHI in this repository

All examples, test data, and demo scenarios use entirely synthetic data. No real participant names, SSNs, dates of birth, phone numbers, email addresses, or other PHI/PII are present in this repository.

### Redaction patterns

Before any audit export, the `redact_phi_from_metadata()` utility automatically strips:

| Pattern | Detection Method | Replacement |
|---------|-----------------|-------------|
| SSN (XXX-XX-XXXX) | Regex | `[REDACTED-SSN]` |
| Date of birth (YYYY-MM-DD) | Regex | `[REDACTED-DOB]` |
| Phone number | Regex | `[REDACTED-PHONE]` |
| Email address | Regex | `[REDACTED-EMAIL]` |
| Known PHI keys (name, address, etc.) | Key name matching | `[REDACTED]` |

### Synthetic data approach

Test and demo data is generated using:
- UUID-based participant IDs (no real identifiers)
- Numeric scores on 0-10 scales (no clinical instruments)
- Generic display names (e.g., "Synthetic Participant A")
- Fictional organization names and phone numbers

---

## 5. Data Retention & Deletion

### Configurable retention windows

Each partner policy includes a `data_retention_days` field (minimum: 30 days) that defines how long participant workflow data is retained. This value is enforced per organization.

### Right-to-delete workflow (conceptual)

1. Participant submits deletion request through authorized channel
2. System verifies participant identity and consent status
3. Participant's check-in data and biomarker readings are marked for deletion
4. After retention window expiration, data is permanently removed
5. Audit log entries are retained (they contain event metadata, not raw data) to maintain chain integrity
6. A `CONSENT_REVOKED` event is logged with the deletion scope

### Cryptographic erasure (production concept)

In production deployments, participant data can be encrypted with per-participant keys stored in OCI Vault. Deletion is achieved by destroying the encryption key, rendering the encrypted data permanently unrecoverable without modifying the audit chain.

---

*This document is part of the AcuityBridge technical documentation suite. For architecture details, see `docs/01_architecture.md`. For deployment architecture, see `docs/02_deployment.md`.*
