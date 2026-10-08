"""
Human-in-the-Loop Escalation Orchestrator.

This module implements the full escalation lifecycle as an explicit state
machine.  Every elevated workflow flag (YELLOW, ORANGE, RED) requires
mandatory human review before any action is taken.  The system **never**
acts autonomously on behalf of participants in escalated scenarios.

**State machine:**

    DETECTED -> ALERT_SENT -> CLINICIAN_NOTIFIED -> ACKNOWLEDGED -> RESOLVED

With timeout path:

    TIMED_OUT -> CRISIS_INTERFACE_TRIGGERED

``CRISIS_INTERFACE_TRIGGERED`` indicates the crisis resource interface was
triggered per partner policy.  It requires that partner integration permits
the routing, and human oversight is expected at the receiving end.

**Human gates enforced in code:**

* ``acknowledge()`` requires the assigned clinician's ID -- no anonymous
  acknowledgments.
* ``resolve()`` requires mandatory resolution notes.
* States cannot be skipped -- the machine enforces sequential transitions.
* ``suspend_automated_interaction()`` hard-blocks any automated content
  for the participant while a case is open.

**Multi-tenant isolation:**  Escalation cases are scoped by ``org_id``.
A policy from organization A cannot be used to manage a case belonging
to organization B.

DISCLAIMER: This module orchestrates decision-support workflows under
clinician oversight.  It does not diagnose, treat, or make clinical
decisions.  All escalation actions require human review and approval.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from acuitybridge.audit import AuditEntry, AuditEventType, AuditLog
from acuitybridge.config import PartnerPolicy
from acuitybridge.models import EscalationState, Participant, RiskFlag


# ---------------------------------------------------------------------------
# Valid state transitions
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS: dict[EscalationState, set[EscalationState]] = {
    EscalationState.DETECTED: {EscalationState.ALERT_SENT},
    EscalationState.ALERT_SENT: {EscalationState.CLINICIAN_NOTIFIED},
    EscalationState.CLINICIAN_NOTIFIED: {
        EscalationState.ACKNOWLEDGED,
        EscalationState.TIMED_OUT,
    },
    EscalationState.ACKNOWLEDGED: {EscalationState.RESOLVED},
    EscalationState.RESOLVED: set(),  # terminal state
    EscalationState.TIMED_OUT: {EscalationState.CRISIS_INTERFACE_TRIGGERED},
    EscalationState.CRISIS_INTERFACE_TRIGGERED: set(),  # terminal state
}


# ---------------------------------------------------------------------------
# Escalation case model
# ---------------------------------------------------------------------------

class EscalationCase(BaseModel):
    """Tracks the full lifecycle of a single escalation.

    Every field change is recorded via the audit log.  The case is scoped
    to a single organization (``org_id``) and a single participant.
    """

    case_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique case identifier.",
    )
    participant_id: str = Field(
        ...,
        description="Identifier of the participant this escalation concerns.",
    )
    org_id: str = Field(
        ...,
        description="Organization that owns this case (multi-tenant key).",
    )
    flag_level: RiskFlag = Field(
        ...,
        description="The workflow risk flag that triggered this escalation.",
    )
    triggering_indicators: list[str] = Field(
        default_factory=list,
        description="List of indicators that triggered the escalation (e.g., keyword matches, threshold breaches).",
    )
    state: EscalationState = Field(
        default=EscalationState.DETECTED,
        description="Current lifecycle state.",
    )
    assigned_clinician_id: Optional[str] = Field(
        default=None,
        description="ID of the clinician assigned to review this case.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the case was opened.",
    )
    alert_sent_at: Optional[datetime] = Field(default=None)
    clinician_notified_at: Optional[datetime] = Field(default=None)
    acknowledged_at: Optional[datetime] = Field(default=None)
    resolved_at: Optional[datetime] = Field(default=None)
    timed_out_at: Optional[datetime] = Field(default=None)
    crisis_triggered_at: Optional[datetime] = Field(default=None)
    resolution_notes: str = Field(
        default="",
        description="Mandatory notes provided by the clinician upon resolution.",
    )
    automated_interaction_suspended: bool = Field(
        default=False,
        description="Whether automated interaction is suspended for this participant.",
    )


# ---------------------------------------------------------------------------
# Transition errors
# ---------------------------------------------------------------------------

class InvalidTransitionError(Exception):
    """Raised when a state transition is not permitted."""
    pass


class UnauthorizedAcknowledgmentError(Exception):
    """Raised when an unauthorized actor attempts to acknowledge a case."""
    pass


class OrgMismatchError(Exception):
    """Raised when a policy's org_id does not match the case's org_id."""
    pass


# ---------------------------------------------------------------------------
# Escalation orchestrator
# ---------------------------------------------------------------------------

class EscalationOrchestrator:
    """Orchestrates the human-in-the-loop escalation lifecycle.

    All operations require an ``AuditLog`` instance for event recording.
    Every state transition emits a structured audit event.

    **Key safety properties:**

    * States cannot be skipped.
    * ``acknowledge()`` requires the assigned clinician's ID.
    * ``resolve()`` requires mandatory resolution notes.
    * ``check_sla_timeout()`` triggers crisis interface if SLA exceeded.
    * ``suspend_automated_interaction()`` blocks automated content.
    * Multi-tenant isolation: policy org_id must match case org_id.
    """

    def __init__(self, audit_log: AuditLog) -> None:
        self._audit_log = audit_log
        self._suspended_participants: set[str] = set()

    # -- helpers --

    def _validate_transition(
        self, case: EscalationCase, target: EscalationState
    ) -> None:
        """Raise InvalidTransitionError if the transition is not allowed."""
        allowed = _VALID_TRANSITIONS.get(case.state, set())
        if target not in allowed:
            raise InvalidTransitionError(
                f"Cannot transition from {case.state.value} to {target.value}. "
                f"Allowed transitions: {[s.value for s in allowed]}"
            )

    def _validate_org_match(
        self, case: EscalationCase, policy: PartnerPolicy
    ) -> None:
        """Ensure the policy belongs to the same org as the case."""
        if case.org_id != policy.org_id:
            raise OrgMismatchError(
                f"Policy org_id '{policy.org_id}' does not match case org_id '{case.org_id}'. "
                "Multi-tenant isolation requires matching organizations."
            )

    def _emit_audit(
        self,
        event_type: AuditEventType,
        case: EscalationCase,
        actor_id: str,
        actor_role: str = "SYSTEM",
        metadata: dict | None = None,
    ) -> None:
        """Emit a structured audit event for a case action."""
        self._audit_log.append(AuditEntry(
            org_id=case.org_id,
            actor_id=actor_id,
            actor_role=actor_role,
            event_type=event_type,
            target_entity=case.case_id,
            metadata=metadata or {},
        ))

    # -- lifecycle operations --

    def open_case(
        self,
        participant: Participant,
        flag_level: RiskFlag,
        indicators: list[str],
        policy: PartnerPolicy,
    ) -> EscalationCase:
        """Open a new escalation case.

        Creates the case in DETECTED state, emits an audit event, and
        immediately suspends automated interaction for the participant.

        Args:
            participant: The participant this escalation concerns.
            flag_level: The workflow risk flag level (must be YELLOW, ORANGE, or RED).
            indicators: List of triggering indicators.
            policy: The partner policy governing this case.

        Returns:
            The newly created ``EscalationCase``.

        Raises:
            ValueError: If flag_level is GREEN (GREEN does not require escalation).
            OrgMismatchError: If participant org_id does not match policy org_id.
        """
        if flag_level == RiskFlag.GREEN:
            raise ValueError(
                "GREEN flags do not trigger escalation. "
                "Only YELLOW, ORANGE, and RED flags require human review."
            )
        if participant.org_id != policy.org_id:
            raise OrgMismatchError(
                f"Participant org_id '{participant.org_id}' does not match "
                f"policy org_id '{policy.org_id}'."
            )

        case = EscalationCase(
            participant_id=participant.participant_id,
            org_id=policy.org_id,
            flag_level=flag_level,
            triggering_indicators=indicators,
            state=EscalationState.DETECTED,
        )

        self._emit_audit(
            event_type=AuditEventType.ESCALATION_OPENED,
            case=case,
            actor_id="SYSTEM",
            metadata={
                "flag_level": flag_level.value,
                "indicators": indicators,
                "participant_id": participant.participant_id,
            },
        )

        # Automatically suspend automated interaction
        self.suspend_automated_interaction(case)

        return case

    def send_alert(self, case: EscalationCase) -> EscalationCase:
        """Transition case from DETECTED to ALERT_SENT.

        Represents the system sending an alert through the configured
        notification channels.

        Args:
            case: The escalation case.

        Returns:
            The updated case.
        """
        self._validate_transition(case, EscalationState.ALERT_SENT)
        case.state = EscalationState.ALERT_SENT
        case.alert_sent_at = datetime.now(timezone.utc)

        self._emit_audit(
            event_type=AuditEventType.ESCALATION_OPENED,
            case=case,
            actor_id="SYSTEM",
            metadata={"new_state": EscalationState.ALERT_SENT.value},
        )
        return case

    def notify_clinician(
        self, case: EscalationCase, clinician_id: str
    ) -> EscalationCase:
        """Record clinician notification and assign the clinician.

        Transitions from ALERT_SENT to CLINICIAN_NOTIFIED.

        Args:
            case: The escalation case.
            clinician_id: ID of the clinician being notified.

        Returns:
            The updated case.
        """
        self._validate_transition(case, EscalationState.CLINICIAN_NOTIFIED)
        case.state = EscalationState.CLINICIAN_NOTIFIED
        case.assigned_clinician_id = clinician_id
        case.clinician_notified_at = datetime.now(timezone.utc)

        self._emit_audit(
            event_type=AuditEventType.CLINICIAN_NOTIFIED,
            case=case,
            actor_id="SYSTEM",
            metadata={
                "clinician_id": clinician_id,
                "new_state": EscalationState.CLINICIAN_NOTIFIED.value,
            },
        )
        return case

    def acknowledge(
        self, case: EscalationCase, clinician_id: str
    ) -> EscalationCase:
        """Human gate: clinician acknowledges the escalation.

        Only the assigned clinician can acknowledge.  This is the primary
        human-in-the-loop gate -- no further action proceeds without it.

        Args:
            case: The escalation case.
            clinician_id: ID of the clinician acknowledging.

        Returns:
            The updated case.

        Raises:
            InvalidTransitionError: If case is not in CLINICIAN_NOTIFIED state.
            UnauthorizedAcknowledgmentError: If clinician_id does not match
                the assigned clinician.
        """
        self._validate_transition(case, EscalationState.ACKNOWLEDGED)

        if case.assigned_clinician_id != clinician_id:
            raise UnauthorizedAcknowledgmentError(
                f"Clinician '{clinician_id}' is not authorized to acknowledge "
                f"this case. Assigned clinician: '{case.assigned_clinician_id}'."
            )

        case.state = EscalationState.ACKNOWLEDGED
        case.acknowledged_at = datetime.now(timezone.utc)

        self._emit_audit(
            event_type=AuditEventType.ESCALATION_ACKNOWLEDGED,
            case=case,
            actor_id=clinician_id,
            actor_role="CLINICIAN",
            metadata={"new_state": EscalationState.ACKNOWLEDGED.value},
        )
        return case

    def resolve(
        self,
        case: EscalationCase,
        clinician_id: str,
        resolution_notes: str,
    ) -> EscalationCase:
        """Close the case with mandatory resolution notes.

        Only the assigned clinician can resolve.  Resolution notes are
        required -- empty notes are rejected.

        Args:
            case: The escalation case.
            clinician_id: ID of the resolving clinician.
            resolution_notes: Mandatory notes explaining the resolution.

        Returns:
            The updated case.

        Raises:
            InvalidTransitionError: If case is not in ACKNOWLEDGED state.
            UnauthorizedAcknowledgmentError: If clinician_id doesn't match.
            ValueError: If resolution_notes is empty.
        """
        self._validate_transition(case, EscalationState.RESOLVED)

        if case.assigned_clinician_id != clinician_id:
            raise UnauthorizedAcknowledgmentError(
                f"Clinician '{clinician_id}' is not authorized to resolve "
                f"this case. Assigned clinician: '{case.assigned_clinician_id}'."
            )
        if not resolution_notes.strip():
            raise ValueError(
                "Resolution notes are mandatory. Cannot resolve a case "
                "without documenting the resolution."
            )

        case.state = EscalationState.RESOLVED
        case.resolved_at = datetime.now(timezone.utc)
        case.resolution_notes = resolution_notes

        # Resume automated interaction
        self.resume_automated_interaction(case)

        self._emit_audit(
            event_type=AuditEventType.ESCALATION_RESOLVED,
            case=case,
            actor_id=clinician_id,
            actor_role="CLINICIAN",
            metadata={
                "new_state": EscalationState.RESOLVED.value,
                "resolution_notes": resolution_notes,
            },
        )
        return case

    def check_sla_timeout(
        self, case: EscalationCase, policy: PartnerPolicy
    ) -> EscalationCase:
        """Check if the clinician acknowledgment SLA has been exceeded.

        If the case is in CLINICIAN_NOTIFIED state and the SLA window has
        passed without acknowledgment, the case transitions to TIMED_OUT
        and then to CRISIS_INTERFACE_TRIGGERED.

        ``CRISIS_INTERFACE_TRIGGERED`` means: triggered per partner policy;
        requires that partner integration permits; human oversight expected
        at the receiving end.

        Args:
            case: The escalation case.
            policy: The partner policy (must match case org_id).

        Returns:
            The updated case (may be unchanged if SLA not exceeded).

        Raises:
            OrgMismatchError: If policy org doesn't match case org.
        """
        self._validate_org_match(case, policy)

        if case.state != EscalationState.CLINICIAN_NOTIFIED:
            return case  # SLA check only applies in CLINICIAN_NOTIFIED state

        if case.clinician_notified_at is None:
            return case

        now = datetime.now(timezone.utc)
        elapsed = (now - case.clinician_notified_at).total_seconds()

        if elapsed > policy.clinician_ack_sla_seconds:
            # Transition to TIMED_OUT
            case.state = EscalationState.TIMED_OUT
            case.timed_out_at = now

            self._emit_audit(
                event_type=AuditEventType.ESCALATION_TIMED_OUT,
                case=case,
                actor_id="SYSTEM",
                metadata={
                    "sla_seconds": policy.clinician_ack_sla_seconds,
                    "elapsed_seconds": elapsed,
                    "new_state": EscalationState.TIMED_OUT.value,
                },
            )

            # Immediately transition to CRISIS_INTERFACE_TRIGGERED
            case.state = EscalationState.CRISIS_INTERFACE_TRIGGERED
            case.crisis_triggered_at = datetime.now(timezone.utc)

            self._emit_audit(
                event_type=AuditEventType.CRISIS_INTERFACE_TRIGGERED,
                case=case,
                actor_id="SYSTEM",
                metadata={
                    "new_state": EscalationState.CRISIS_INTERFACE_TRIGGERED.value,
                    "note": (
                        "Triggered per partner policy. Requires that partner "
                        "integration permits. Human oversight expected at "
                        "receiving end."
                    ),
                    "crisis_targets": [
                        t.name for t in policy.crisis_resource_targets
                    ],
                },
            )

        return case

    def suspend_automated_interaction(self, case: EscalationCase) -> None:
        """Hard gate: block automated content for this participant.

        While any escalation case is open for a participant, no automated
        content should be delivered.  This prevents the system from
        acting autonomously during an active escalation.
        """
        case.automated_interaction_suspended = True
        self._suspended_participants.add(case.participant_id)

        self._emit_audit(
            event_type=AuditEventType.AUTOMATED_INTERACTION_SUSPENDED,
            case=case,
            actor_id="SYSTEM",
            metadata={"participant_id": case.participant_id},
        )

    def resume_automated_interaction(self, case: EscalationCase) -> None:
        """Resume automated interaction after case resolution."""
        case.automated_interaction_suspended = False
        self._suspended_participants.discard(case.participant_id)

        self._emit_audit(
            event_type=AuditEventType.AUTOMATED_INTERACTION_RESUMED,
            case=case,
            actor_id="SYSTEM",
            metadata={"participant_id": case.participant_id},
        )

    def is_interaction_suspended(self, participant_id: str) -> bool:
        """Check whether automated interaction is suspended for a participant.

        Args:
            participant_id: The participant to check.

        Returns:
            True if automated interaction is currently suspended.
        """
        return participant_id in self._suspended_participants
