"""
Tests for acuitybridge.crisis_router -- Crisis Resource Interfaces.
"""

from __future__ import annotations

from acuitybridge.audit import AuditEventType, AuditLog
from acuitybridge.config import PartnerPolicy
from acuitybridge.crisis_router import route_to_crisis_resources
from acuitybridge.escalation import EscalationCase
from acuitybridge.models import CrisisResourceTarget, EscalationState, RiskFlag


def _make_case(org_id: str = "org_a") -> EscalationCase:
    return EscalationCase(
        participant_id="p1",
        org_id=org_id,
        flag_level=RiskFlag.RED,
        state=EscalationState.CRISIS_INTERFACE_TRIGGERED,
    )


def _make_policy(org_id: str = "org_a", targets: list | None = None) -> PartnerPolicy:
    return PartnerPolicy(
        org_id=org_id,
        org_name="Test Org",
        crisis_resource_targets=targets or [],
    )


class TestCrisisRouter:
    def test_route_with_configured_targets(self):
        audit_log = AuditLog()
        target = CrisisResourceTarget(
            name="Test Hotline",
            target_type="phone",
            endpoint="+1-555-0000",
        )
        policy = _make_policy(targets=[target])
        case = _make_case()

        results = route_to_crisis_resources(case, policy, audit_log)
        assert len(results) == 1
        assert results[0].routed is True
        assert "STUB" in results[0].message

    def test_route_with_no_targets_logs_event(self):
        audit_log = AuditLog()
        policy = _make_policy(targets=[])
        case = _make_case()

        results = route_to_crisis_resources(case, policy, audit_log)
        assert len(results) == 0

        events = audit_log.query(
            org_id="org_a",
            event_type=AuditEventType.CRISIS_INTERFACE_TRIGGERED,
        )
        assert len(events) == 1
        assert "No crisis resource targets" in events[0].metadata["note"]

    def test_audit_events_emitted_per_target(self):
        audit_log = AuditLog()
        targets = [
            CrisisResourceTarget(name="Target A", target_type="phone", endpoint="111"),
            CrisisResourceTarget(name="Target B", target_type="webhook", endpoint="http://example.com"),
        ]
        policy = _make_policy(targets=targets)
        case = _make_case()

        route_to_crisis_resources(case, policy, audit_log)

        events = audit_log.query(
            org_id="org_a",
            event_type=AuditEventType.CRISIS_INTERFACE_TRIGGERED,
        )
        assert len(events) == 2

    def test_partner_responsibility_noted_in_audit(self):
        audit_log = AuditLog()
        target = CrisisResourceTarget(
            name="Test", target_type="phone", endpoint="555"
        )
        policy = _make_policy(targets=[target])
        case = _make_case()

        route_to_crisis_resources(case, policy, audit_log)
        events = audit_log.query(org_id="org_a")
        assert any("partner" in str(e.metadata).lower() for e in events)
