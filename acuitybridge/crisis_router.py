"""
Crisis Resource Interfaces -- Partner-Configured Crisis Workflow Stubs.

This module provides interfaces for routing escalation cases to
partner-configured crisis resources.  These are **integration stubs** --
they define the interface contract and emit audit events, but do not
establish operational connections to emergency services.

**Partners define operational crisis protocols.**  This project provides
interface hooks and audit logging, not emergency service operations.
Operational readiness, staffing, and response times are the sole
responsibility of the deploying partner organization.

**This module does not guarantee connection to emergency services.**

DISCLAIMER: Crisis resource interfaces support routing to crisis resources
where partner integration permits.  They do not provide emergency services,
guarantee response times, or replace institutional crisis protocols.
"""

from __future__ import annotations

from acuitybridge.audit import AuditEntry, AuditEventType, AuditLog
from acuitybridge.config import PartnerPolicy
from acuitybridge.escalation import EscalationCase
from acuitybridge.models import CrisisResourceTarget


class CrisisRouteResult:
    """Result of a crisis resource routing attempt."""

    def __init__(
        self,
        target: CrisisResourceTarget,
        routed: bool,
        message: str,
    ) -> None:
        self.target = target
        self.routed = routed
        self.message = message

    def __repr__(self) -> str:
        return (
            f"CrisisRouteResult(target='{self.target.name}', "
            f"routed={self.routed}, message='{self.message}')"
        )


def route_to_crisis_resources(
    case: EscalationCase,
    policy: PartnerPolicy,
    audit_log: AuditLog,
) -> list[CrisisRouteResult]:
    """Attempt to route an escalation case to configured crisis resources.

    Iterates through the partner's configured crisis resource targets and
    invokes the appropriate stub for each.  Every attempt is recorded in
    the audit log.

    Args:
        case: The escalation case being routed.
        policy: The partner policy (defines crisis resource targets).
        audit_log: The audit log for recording routing events.

    Returns:
        List of ``CrisisRouteResult`` objects describing each attempt.
    """
    results: list[CrisisRouteResult] = []

    if not policy.crisis_resource_targets:
        audit_log.append(AuditEntry(
            org_id=case.org_id,
            actor_id="SYSTEM",
            actor_role="SYSTEM",
            event_type=AuditEventType.CRISIS_INTERFACE_TRIGGERED,
            target_entity=case.case_id,
            metadata={
                "note": "No crisis resource targets configured for this partner.",
                "action": "No routing attempted. Partner must configure targets.",
            },
        ))
        return results

    for target in policy.crisis_resource_targets:
        result = _route_single_target(target)
        results.append(result)

        audit_log.append(AuditEntry(
            org_id=case.org_id,
            actor_id="SYSTEM",
            actor_role="SYSTEM",
            event_type=AuditEventType.CRISIS_INTERFACE_TRIGGERED,
            target_entity=case.case_id,
            metadata={
                "target_name": target.name,
                "target_type": target.target_type,
                "routed": result.routed,
                "message": result.message,
                "note": (
                    "Interface stub invoked. Operational crisis protocols "
                    "are the partner's responsibility."
                ),
            },
        ))

    return results


def _route_single_target(target: CrisisResourceTarget) -> CrisisRouteResult:
    """Stub: route to a single crisis resource target.

    In production, this would integrate with telephony APIs, webhook
    endpoints, or internal queuing systems as configured by the partner.
    This stub logs the attempt and returns a simulated success.

    Partners are responsible for ensuring operational readiness of all
    configured crisis resource targets.
    """
    # Stub implementation -- no actual external calls
    return CrisisRouteResult(
        target=target,
        routed=True,
        message=(
            f"[STUB] Crisis interface invoked for target '{target.name}' "
            f"({target.target_type}: {target.endpoint}). "
            "Production implementation requires partner integration."
        ),
    )
