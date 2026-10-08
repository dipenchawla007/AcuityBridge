# Limitations and Disclaimers

## Overview

This document defines the explicit scope boundaries, limitations, and disclaimers for the AcuityBridge Safety & Escalation Orchestrator. It is intended to prevent overclaims about the system's capabilities and to establish clear expectations for all stakeholders.

---

## Not a Medical Device

This software has not been cleared or approved by the U.S. Food and Drug Administration (FDA) or any other regulatory body. It is not intended for use as a medical device, and it has not undergone the clinical validation or regulatory review required for medical device classification.

---

## Not Diagnostic

The system does not diagnose, assess, or classify any medical or mental health condition. Workflow flags (GREEN, YELLOW, ORANGE, RED) are **policy-driven routing signals** for workflow management -- they are not clinical assessments, risk stratification scores, or diagnostic indicators.

The signal evaluator compares self-reported check-in values and optional supplementary data against partner-defined thresholds. These thresholds are configured by each partner organization based on their operational context. They do not represent clinical cut-offs or validated screening instruments.

---

## No Emergency Guarantees

Crisis resource interfaces defined in this system are **integration stubs**. They provide interface contracts and audit logging for partner-configured crisis resource routing. They do **not**:

- Guarantee connection to emergency services (911, 988, or any other resource)
- Guarantee response times
- Provide emergency medical services
- Replace institutional emergency protocols

---

## Partner Responsibility for Crisis Protocols

**Partners define operational crisis protocols.** This project provides interface hooks and audit logging, not emergency service operations. Operational readiness, staffing, response times, and clinical decision-making at the receiving end of any crisis interface are the sole responsibility of the deploying partner organization.

Each partner must:

- Configure their own crisis resource targets
- Ensure operational readiness of all configured targets
- Maintain staffing for clinician response within their defined SLA
- Establish and test backup protocols for SLA timeout scenarios

---

## Requires Partner Policies, BAAs, and Approvals

Deployment of this system in any care setting requires:

- **Executed Business Associate Agreement (BAA)** between AcuityBridge and the partner organization, where applicable under HIPAA
- **Partner-specific policy configuration** reviewed and approved by the partner's clinical leadership
- **IRB/Ethics review** where the deployment involves research activities or vulnerable populations
- **Clinician oversight agreements** designating licensed professionals for escalation review
- **Data processing agreements** defining data handling, retention, and deletion responsibilities

The system should not be deployed without these prerequisites.

---

## Synthetic Data Only

All examples, test data, and demonstration scenarios in this repository use **entirely synthetic data**. No real participant data, Protected Health Information (PHI), or Personally Identifiable Information (PII) is present in any file in this repository.

Synthetic data includes:

- UUID-based identifiers (not real names or IDs)
- Numeric scores on arbitrary scales (not validated clinical instruments)
- Fictional organization names, phone numbers, and endpoints
- Fabricated scenario narratives for demonstration purposes

---

## Human Review Required

All elevated workflow flags require review by a licensed clinician or authorized human reviewer before any action is taken:

| Flag Level | Human Review Requirement |
|-----------|------------------------|
| **GREEN** | Human review optional |
| **YELLOW** | Human review **required** before any action |
| **ORANGE** | Human review **required**; automated interaction suspended |
| **RED** | Human review **required**; escalation and crisis interface protocols activated per partner policy |

The system does not take autonomous action on behalf of participants when any escalation case is open. Automated interaction is suspended upon case creation and resumed only after clinician resolution with documented notes.

---

## Not a Substitute for Professional Care

This software does not replace:

- Licensed mental health professionals
- Therapy or counseling services
- Psychiatric care or medication management
- Emergency services (911, crisis hotlines, emergency departments)
- Clinical judgment by qualified professionals

The system is designed to **support** decision-support workflows under clinician supervision, not to operate independently or to replace any component of professional care.

---

## Tamper-Evident Audit Log Scope

The audit log uses SHA-256 hash chaining to provide **structural tamper evidence**. This is a demonstration mechanism suitable for audit review and compliance demonstration.

This mechanism does **not** provide:

- Cryptographic immutability guarantees
- Protection against an attacker with full system access
- Regulatory-grade tamper-proofing

Production deployments should use WORM (Write Once Read Many) storage, object-lock policies, or cryptographic commitment schemes (e.g., Merkle trees with external trust anchors) for stronger guarantees.

---

## RBAC Scope

The role-based access control model implemented in `rbac.py` is a lightweight, in-process demonstration suitable for pilot deployments. Production deployments should integrate with enterprise identity providers (OAuth2/OIDC, SAML) and enforce additional security controls including:

- Multi-factor authentication
- Session management and timeout
- Centralized identity governance
- Regular access reviews

---

*This document is part of the AcuityBridge technical documentation suite. For security details, see `docs/03_security_privacy.md`. For architecture, see `docs/01_architecture.md`.*
