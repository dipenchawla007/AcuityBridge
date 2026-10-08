"""
Tests for acuitybridge.escalation -- Human-in-the-Loop Escalation Orchestrator.

Covers: full lifecycle happy path, SLA timeout escalation, unauthorized ack
rejection, duplicate ack idempotency, state skip rejection, concurrent case
isolation, resolution without ack rejection, crisis interface trigger on
timeout, multi-tenant isolation, GREEN flag rejection, empty resolution notes
rejection, and automated interaction suspension.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from acuitybridge.audit import AuditEventType, AuditLog
from acuitybridge.config import PartnerPolicy
from acuitybridge.escalation import (
    EscalationCase,
    EscalationOrchestrator,
    InvalidTransitionError,
    OrgMismatchError,
    UnauthorizedAcknowledgmentError,
)
from acuitybridge.models import EscalationState, Participant, RiskFlag


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_policy(org_id: str = "org_a", sla: int = 300) -> PartnerPolicy:
    return PartnerPolicy(
        org_id=org_id,
        org_name=f"Test Org {org_id}",
        clinician_ack_sla_seconds=sla,
    )


def _make_participant(org_id: str = "org_a") -> Participant:
    return Participant(org_id=org_id)


def _open_and_advance_to_notified(
    orch: EscalationOrchestrator,
    policy: PartnerPolicy | None = None,
    participant: Participant | None = None,
    clinician_id: str = "dr_smith",
) -> EscalationCase:
    """Helper: open case and advance to CLINICIAN_NOTIFIED."""
    policy = policy or _make_policy()
    participant = participant or _make_participant(org_id=policy.org_id)
    case = orch.open_case(participant, RiskFlag.RED, ["keyword_detected"], policy)
    case = orch.send_alert(case)
    case = orch.notify_clinician(case, clinician_id)
    return case


# ---------------------------------------------------------------------------
# 1. Full lifecycle happy path
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    def test_complete_lifecycle(self):
        """DETECTED -> ALERT_SENT -> CLINICIAN_NOTIFIED -> ACKNOWLEDGED -> RESOLVED"""
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)
        policy = _make_policy()
        participant = _make_participant()

        case = orch.open_case(participant, RiskFlag.RED, ["crisis_keyword"], policy)
        assert case.state == EscalationState.DETECTED
        assert case.automated_interaction_suspended is True

        case = orch.send_alert(case)
        assert case.state == EscalationState.ALERT_SENT

        case = orch.notify_clinician(case, "dr_smith")
        assert case.state == EscalationState.CLINICIAN_NOTIFIED
        assert case.assigned_clinician_id == "dr_smith"

        case = orch.acknowledge(case, "dr_smith")
        assert case.state == EscalationState.ACKNOWLEDGED

        case = orch.resolve(case, "dr_smith", "Participant stabilized, follow-up scheduled.")
        assert case.state == EscalationState.RESOLVED
        assert case.resolution_notes != ""
        assert case.automated_interaction_suspended is False

        # Verify audit trail
        events = audit_log.query(org_id="org_a")
        event_types = [e.event_type for e in events]
        assert AuditEventType.ESCALATION_OPENED in event_types
        assert AuditEventType.CLINICIAN_NOTIFIED in event_types
        assert AuditEventType.ESCALATION_ACKNOWLEDGED in event_types
        assert AuditEventType.ESCALATION_RESOLVED in event_types


# ---------------------------------------------------------------------------
# 2. SLA timeout escalation
# ---------------------------------------------------------------------------

class TestSLATimeout:
    def test_sla_timeout_triggers_crisis_interface(self):
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)
        policy = _make_policy(sla=1)  # 1-second SLA for testing

        case = _open_and_advance_to_notified(orch, policy)

        # Simulate SLA expiry by backdating notification
        case.clinician_notified_at = datetime.now(timezone.utc) - timedelta(seconds=10)

        case = orch.check_sla_timeout(case, policy)
        assert case.state == EscalationState.CRISIS_INTERFACE_TRIGGERED
        assert case.timed_out_at is not None
        assert case.crisis_triggered_at is not None

        # Verify audit events
        events = audit_log.query(
            org_id="org_a",
            event_type=AuditEventType.CRISIS_INTERFACE_TRIGGERED,
        )
        assert len(events) >= 1
        assert "partner policy" in events[-1].metadata.get("note", "").lower()

    def test_sla_not_exceeded_no_transition(self):
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)
        policy = _make_policy(sla=9999)

        case = _open_and_advance_to_notified(orch, policy)
        original_state = case.state

        case = orch.check_sla_timeout(case, policy)
        assert case.state == original_state  # unchanged


# ---------------------------------------------------------------------------
# 3. Unauthorized acknowledgment rejection
# ---------------------------------------------------------------------------

class TestUnauthorizedAck:
    def test_wrong_clinician_cannot_acknowledge(self):
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)
        case = _open_and_advance_to_notified(orch, clinician_id="dr_smith")

        with pytest.raises(UnauthorizedAcknowledgmentError):
            orch.acknowledge(case, "dr_jones")  # wrong clinician

    def test_wrong_clinician_cannot_resolve(self):
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)
        case = _open_and_advance_to_notified(orch, clinician_id="dr_smith")
        case = orch.acknowledge(case, "dr_smith")

        with pytest.raises(UnauthorizedAcknowledgmentError):
            orch.resolve(case, "dr_jones", "Some notes")


# ---------------------------------------------------------------------------
# 4. Duplicate ack idempotency
# ---------------------------------------------------------------------------

class TestDuplicateAck:
    def test_second_ack_raises_invalid_transition(self):
        """Once acknowledged, re-acknowledging is an invalid transition."""
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)
        case = _open_and_advance_to_notified(orch, clinician_id="dr_smith")
        case = orch.acknowledge(case, "dr_smith")

        with pytest.raises(InvalidTransitionError):
            orch.acknowledge(case, "dr_smith")


# ---------------------------------------------------------------------------
# 5. State skip rejection
# ---------------------------------------------------------------------------

class TestStateSkipRejection:
    def test_cannot_skip_from_detected_to_acknowledged(self):
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)
        policy = _make_policy()
        participant = _make_participant()
        case = orch.open_case(participant, RiskFlag.ORANGE, ["elevated_distress"], policy)

        with pytest.raises(InvalidTransitionError):
            orch.acknowledge(case, "dr_smith")

    def test_cannot_skip_from_detected_to_resolved(self):
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)
        policy = _make_policy()
        participant = _make_participant()
        case = orch.open_case(participant, RiskFlag.RED, ["keyword"], policy)

        with pytest.raises(InvalidTransitionError):
            orch.resolve(case, "dr_smith", "notes")


# ---------------------------------------------------------------------------
# 6. Concurrent case isolation
# ---------------------------------------------------------------------------

class TestConcurrentCaseIsolation:
    def test_two_cases_independent_lifecycle(self):
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)
        policy = _make_policy()
        p1 = _make_participant()
        p2 = _make_participant()

        case1 = orch.open_case(p1, RiskFlag.RED, ["kw1"], policy)
        case2 = orch.open_case(p2, RiskFlag.ORANGE, ["kw2"], policy)

        case1 = orch.send_alert(case1)
        case1 = orch.notify_clinician(case1, "dr_a")
        case1 = orch.acknowledge(case1, "dr_a")

        # case2 should still be in DETECTED
        assert case2.state == EscalationState.DETECTED
        assert case1.state == EscalationState.ACKNOWLEDGED


# ---------------------------------------------------------------------------
# 7. Resolution without acknowledgment rejection
# ---------------------------------------------------------------------------

class TestResolutionWithoutAck:
    def test_cannot_resolve_without_acknowledgment(self):
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)
        case = _open_and_advance_to_notified(orch, clinician_id="dr_smith")

        with pytest.raises(InvalidTransitionError):
            orch.resolve(case, "dr_smith", "Some notes")


# ---------------------------------------------------------------------------
# 8. Empty resolution notes rejection
# ---------------------------------------------------------------------------

class TestEmptyResolutionNotes:
    def test_empty_notes_rejected(self):
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)
        case = _open_and_advance_to_notified(orch, clinician_id="dr_smith")
        case = orch.acknowledge(case, "dr_smith")

        with pytest.raises(ValueError, match="[Rr]esolution notes"):
            orch.resolve(case, "dr_smith", "")

    def test_whitespace_only_notes_rejected(self):
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)
        case = _open_and_advance_to_notified(orch, clinician_id="dr_smith")
        case = orch.acknowledge(case, "dr_smith")

        with pytest.raises(ValueError):
            orch.resolve(case, "dr_smith", "   ")


# ---------------------------------------------------------------------------
# 9. GREEN flag rejection
# ---------------------------------------------------------------------------

class TestGreenFlagRejection:
    def test_green_flag_does_not_trigger_escalation(self):
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)
        policy = _make_policy()
        participant = _make_participant()

        with pytest.raises(ValueError, match="GREEN"):
            orch.open_case(participant, RiskFlag.GREEN, [], policy)


# ---------------------------------------------------------------------------
# 10. Multi-tenant isolation
# ---------------------------------------------------------------------------

class TestMultiTenantIsolation:
    def test_org_a_policy_cannot_manage_org_b_case(self):
        """A policy from org A must not be usable for an org B participant."""
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)

        policy_a = _make_policy(org_id="org_a")
        participant_b = _make_participant(org_id="org_b")

        with pytest.raises(OrgMismatchError):
            orch.open_case(participant_b, RiskFlag.RED, ["kw"], policy_a)

    def test_sla_check_rejects_mismatched_policy(self):
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)

        policy_a = _make_policy(org_id="org_a")
        case = _open_and_advance_to_notified(orch, policy=policy_a)

        policy_b = _make_policy(org_id="org_b")
        with pytest.raises(OrgMismatchError):
            orch.check_sla_timeout(case, policy_b)


# ---------------------------------------------------------------------------
# 11. Automated interaction suspension
# ---------------------------------------------------------------------------

class TestAutomatedInteractionSuspension:
    def test_suspension_on_case_open(self):
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)
        policy = _make_policy()
        participant = _make_participant()

        case = orch.open_case(participant, RiskFlag.RED, ["kw"], policy)
        assert orch.is_interaction_suspended(participant.participant_id) is True

    def test_resumption_on_resolve(self):
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)
        case = _open_and_advance_to_notified(orch, clinician_id="dr_smith")

        participant_id = case.participant_id
        assert orch.is_interaction_suspended(participant_id) is True

        case = orch.acknowledge(case, "dr_smith")
        case = orch.resolve(case, "dr_smith", "Resolved safely.")
        assert orch.is_interaction_suspended(participant_id) is False


# ---------------------------------------------------------------------------
# 12. YELLOW flag triggers escalation
# ---------------------------------------------------------------------------

class TestYellowFlagEscalation:
    def test_yellow_flag_opens_case(self):
        """YELLOW flags require human review and should open a case."""
        audit_log = AuditLog()
        orch = EscalationOrchestrator(audit_log)
        policy = _make_policy()
        participant = _make_participant()

        case = orch.open_case(participant, RiskFlag.YELLOW, ["low_mood"], policy)
        assert case.state == EscalationState.DETECTED
        assert case.flag_level == RiskFlag.YELLOW
