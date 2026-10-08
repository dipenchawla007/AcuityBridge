# Roadmap & Milestones

## Overview

This document defines time-bound development milestones for the AcuityBridge Safety & Escalation Orchestrator. Each milestone has specific deliverables and unlocks the next phase of partner deployment capability.

---

## Days 1-30: v0.1 -- Core Framework

**Focus:** Establish the foundational safety and governance infrastructure.

### Deliverables

- [x] Signal evaluator with policy-driven workflow risk flagging (GREEN/YELLOW/ORANGE/RED)
- [x] Escalation orchestrator with full state machine lifecycle and human-in-the-loop gates
- [x] Append-only, tamper-evident audit log with SHA-256 hash chain and PHI redaction
- [x] Partner policy engine with multi-tenant registry and YAML configuration
- [x] Role-based access control (RBAC) with four defined roles
- [x] Crisis resource interface stubs with audit logging
- [x] Decision Transparency Report generator
- [x] Synthetic scenario demo exercising all modules end-to-end
- [x] Test suite (84 tests) with CI pipeline (GitHub Actions)
- [x] Apache 2.0 open-source release with documentation suite

### Unlocks

- Internal architecture review and security assessment
- Partner conversations with demonstrable technical artifacts
- Initial clinical advisor feedback on workflow design

---

## Days 31-60: v0.2 -- Pilot Readiness

**Focus:** Harden the system for sandbox pilot deployment with a first partner.

### Planned Deliverables

- [ ] Persistent policy storage (database-backed registry replacing in-memory)
- [ ] Persistent audit log storage (database or object storage backend)
- [ ] Wearable data ingestion pipeline (Apple HealthKit, Fitbit API integration stubs)
- [ ] Enhanced RBAC with session management and authentication hooks
- [ ] Configurable notification delivery (SMS via Twilio stub, webhook, email)
- [ ] Admin API for policy management (REST endpoints)
- [ ] Expanded test coverage (target: >90% line coverage)
- [ ] Security hardening review (input validation, rate limiting, error handling)

### Unlocks

- Sandbox pilot deployment with first partner (synthetic data)
- Clinician training on Provider Dashboard prototype
- BAA negotiation with validated technical architecture

---

## Days 61-90: v1.0 -- First Partner Pilot

**Focus:** Support a live limited pilot with 10-20 participants under clinician oversight.

### Planned Deliverables

- [ ] Clinician dashboard API (read-only case view, acknowledgment, resolution endpoints)
- [ ] Crisis resource integration interfaces (telephony API, webhook delivery)
- [ ] FHIR API integration stubs for EHR interoperability (Epic, Cerner)
- [ ] Pilot playbook finalized with partner-specific customization
- [ ] Observability and monitoring (health checks, alerting, SLA tracking dashboards)
- [ ] Data retention enforcement (automated expiration per partner policy)
- [ ] Compliance documentation package (security controls assessment, regulatory controls review)

### Unlocks

- First partner limited pilot (10-20 participants, full clinician oversight)
- Outcome data collection for clinical validation
- Second and third partner conversations with pilot evidence

---

## Beyond 90 Days

**Focus:** Scale validated workflows; pursue regulatory pathway.

### Planned Activities

- Regulatory pathway research (understanding applicable frameworks for decision-support software)
- Multi-site pilot expansion based on initial partner outcomes
- Outcome measurement and evidence generation from pilot deployments
- Reimbursement and sustainability model research
- Relevant standards review (quality management, safety, interoperability)
- Community contributions and peer review through open-source engagement

---

## Version Summary

| Version | Timeline | Key Capability | Pilot Readiness |
|---------|----------|---------------|-----------------|
| v0.1 | Days 1-30 | Core safety framework + governance | Internal review only |
| v0.2 | Days 31-60 | Persistent storage + notifications | Sandbox pilot (synthetic data) |
| v1.0 | Days 61-90 | Dashboard + integrations + monitoring | Limited pilot (10-20 participants) |
| v1.x+ | 90+ days | Regulatory readiness + multi-site scale | Production deployment |

---

*This document is part of the AcuityBridge technical documentation suite. For the pilot deployment process, see `docs/05_pilot_playbook.md`. For current architecture, see `docs/01_architecture.md`.*
