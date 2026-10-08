"""
Decision Transparency Report Generator.

Generates structured reports from escalation cases for clinician review.
Each report summarizes the workflow flag, triggering indicators, a timeline
of state transitions, and the reasoning chain -- enabling clinicians to
make informed decisions with full context.

DISCLAIMER: Transparency reports are decision-support summaries for
clinician review.  They do not constitute clinical assessments, diagnoses,
or treatment recommendations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from acuitybridge.escalation import EscalationCase
from acuitybridge.models import EscalationState


class TransparencyReport:
    """A structured Decision Transparency Report for clinician review."""

    def __init__(
        self,
        case_id: str,
        participant_id: str,
        org_id: str,
        flag_level: str,
        current_state: str,
        triggering_indicators: list[str],
        timeline: list[dict[str, str]],
        reasoning_chain: list[str],
        generated_at: str,
    ) -> None:
        self.case_id = case_id
        self.participant_id = participant_id
        self.org_id = org_id
        self.flag_level = flag_level
        self.current_state = current_state
        self.triggering_indicators = triggering_indicators
        self.timeline = timeline
        self.reasoning_chain = reasoning_chain
        self.generated_at = generated_at

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report to a dictionary."""
        return {
            "report_type": "Decision Transparency Report",
            "disclaimer": (
                "This report is a decision-support summary for clinician review. "
                "It does not constitute a clinical assessment or diagnosis."
            ),
            "case_id": self.case_id,
            "participant_id": self.participant_id,
            "org_id": self.org_id,
            "flag_level": self.flag_level,
            "current_state": self.current_state,
            "triggering_indicators": self.triggering_indicators,
            "timeline": self.timeline,
            "reasoning_chain": self.reasoning_chain,
            "generated_at": self.generated_at,
        }

    def __repr__(self) -> str:
        return (
            f"TransparencyReport(case_id={self.case_id}, "
            f"flag={self.flag_level}, state={self.current_state})"
        )


def generate_transparency_report(
    case: EscalationCase,
    evaluation_reasons: list[str] | None = None,
) -> TransparencyReport:
    """Generate a Decision Transparency Report from an escalation case.

    Args:
        case: The escalation case.
        evaluation_reasons: Optional list of reasons from the signal evaluator.

    Returns:
        A ``TransparencyReport`` instance ready for clinician review.
    """
    timeline = _build_timeline(case)
    reasoning = evaluation_reasons or [
        f"Flag level {case.flag_level.value} triggered by indicators: "
        + ", ".join(case.triggering_indicators)
    ]

    return TransparencyReport(
        case_id=case.case_id,
        participant_id=case.participant_id,
        org_id=case.org_id,
        flag_level=case.flag_level.value,
        current_state=case.state.value,
        triggering_indicators=case.triggering_indicators,
        timeline=timeline,
        reasoning_chain=reasoning,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def _build_timeline(case: EscalationCase) -> list[dict[str, str]]:
    """Build a chronological timeline of case state transitions."""
    events: list[dict[str, str]] = []

    if case.created_at:
        events.append({
            "state": EscalationState.DETECTED.value,
            "timestamp": case.created_at.isoformat(),
            "description": "Escalation case opened.",
        })
    if case.alert_sent_at:
        events.append({
            "state": EscalationState.ALERT_SENT.value,
            "timestamp": case.alert_sent_at.isoformat(),
            "description": "Alert sent through notification channels.",
        })
    if case.clinician_notified_at:
        events.append({
            "state": EscalationState.CLINICIAN_NOTIFIED.value,
            "timestamp": case.clinician_notified_at.isoformat(),
            "description": f"Clinician {case.assigned_clinician_id or 'unknown'} notified.",
        })
    if case.acknowledged_at:
        events.append({
            "state": EscalationState.ACKNOWLEDGED.value,
            "timestamp": case.acknowledged_at.isoformat(),
            "description": f"Acknowledged by clinician {case.assigned_clinician_id or 'unknown'}.",
        })
    if case.resolved_at:
        events.append({
            "state": EscalationState.RESOLVED.value,
            "timestamp": case.resolved_at.isoformat(),
            "description": f"Resolved. Notes: {case.resolution_notes}",
        })
    if case.timed_out_at:
        events.append({
            "state": EscalationState.TIMED_OUT.value,
            "timestamp": case.timed_out_at.isoformat(),
            "description": "Clinician acknowledgment SLA exceeded.",
        })
    if case.crisis_triggered_at:
        events.append({
            "state": EscalationState.CRISIS_INTERFACE_TRIGGERED.value,
            "timestamp": case.crisis_triggered_at.isoformat(),
            "description": (
                "Crisis resource interface triggered per partner policy. "
                "Human oversight expected at receiving end."
            ),
        })

    return events
