"""
Synthetic Scenario: Post-Discharge Workflow Support Walkthrough
===============================================================

This script demonstrates the full AcuityBridge Safety & Escalation
Orchestrator workflow using entirely synthetic data.  No real participant
data, PHI, or PII is used.

The scenario simulates a community health center that has enrolled a
recently-discharged participant in a decision-support monitoring workflow.

Steps demonstrated:
  1. Load partner policy from YAML
  2. Enroll synthetic participant
  3. Process daily check-ins through the signal evaluator
  4. Trigger escalation on elevated flag
  5. Walk through the full escalation lifecycle
  6. Generate a Decision Transparency Report
  7. Export audit log for compliance review

DISCLAIMER: This is a synthetic demonstration.  This software is not a
medical device, does not diagnose or treat any condition, and all outputs
require human review by licensed professionals.

Usage:
    python -m examples.synthetic_scenario
    # or: python examples/synthetic_scenario.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure the project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from acuitybridge.audit import AuditEntry, AuditEventType, AuditLog
from acuitybridge.config import PartnerPolicy, PolicyRegistry, load_policies_from_yaml
from acuitybridge.crisis_router import route_to_crisis_resources
from acuitybridge.escalation import EscalationOrchestrator
from acuitybridge.models import CheckIn, CrisisResourceTarget, Participant, RiskFlag
from acuitybridge.signal_evaluator import evaluate_check_in
from acuitybridge.transparency_report import generate_transparency_report


def _banner(text: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}\n")


def main() -> None:
    _banner("AcuityBridge Synthetic Scenario: Post-Discharge Workflow")
    print("DISCLAIMER: All data in this demo is entirely synthetic.")
    print("This software is not a medical device.\n")

    # ------------------------------------------------------------------
    # Step 1: Load partner policy
    # ------------------------------------------------------------------
    _banner("Step 1: Load Partner Policy")

    sample_yaml = Path(__file__).parent / "partner_policies.yaml"
    if sample_yaml.exists():
        policies = load_policies_from_yaml(sample_yaml)
        policy = policies[0]  # Use Alpha Community Health Center
        print(f"Loaded policy: {policy.org_name} (org_id: {policy.org_id})")
    else:
        # Fallback: create inline policy
        policy = PartnerPolicy(
            org_id="demo_clinic",
            org_name="Demo Community Clinic",
            clinician_ack_sla_seconds=300,
            crisis_resource_targets=[
                CrisisResourceTarget(
                    name="County Crisis Team (synthetic)",
                    target_type="phone",
                    endpoint="+1-555-0000",
                )
            ],
            escalation_keyword_overrides=["overdose", "relapse"],
        )
        print(f"Created inline policy: {policy.org_name}")

    registry = PolicyRegistry()
    registry.register(policy)
    print(f"Policy registered. Org IDs in registry: {registry.list_orgs()}")

    # ------------------------------------------------------------------
    # Step 2: Enroll synthetic participant
    # ------------------------------------------------------------------
    _banner("Step 2: Enroll Synthetic Participant")

    participant = Participant(
        org_id=policy.org_id,
        display_name="Synthetic Participant A (not a real person)",
    )
    print(f"Enrolled: {participant.display_name}")
    print(f"  participant_id: {participant.participant_id}")
    print(f"  org_id: {participant.org_id}")

    # ------------------------------------------------------------------
    # Step 3: Initialize audit log and orchestrator
    # ------------------------------------------------------------------
    audit_log = AuditLog()
    orchestrator = EscalationOrchestrator(audit_log)

    # ------------------------------------------------------------------
    # Step 4: Day 1 -- GREEN check-in (normal)
    # ------------------------------------------------------------------
    _banner("Step 3: Day 1 Check-In (Normal)")

    checkin_day1 = CheckIn(
        participant_id=participant.participant_id,
        org_id=policy.org_id,
        mood_score=7.0,
        sleep_quality=8.0,
        energy_level=6.5,
        distress_level=1.5,
    )
    result_day1 = evaluate_check_in(checkin_day1, policy)
    print(f"Check-in result: {result_day1}")
    print(f"  Flag: {result_day1.flag.value}")
    print(f"  Requires human review: {result_day1.requires_human_review()}")
    print(f"  Reasons: {result_day1.reasons}")

    # Log the evaluation
    audit_log.append(AuditEntry(
        org_id=policy.org_id,
        actor_id="SYSTEM",
        actor_role="SYSTEM",
        event_type=AuditEventType.SIGNAL_EVALUATED,
        target_entity=participant.participant_id,
        metadata={"flag": result_day1.flag.value, "reasons": result_day1.reasons},
    ))

    # ------------------------------------------------------------------
    # Step 5: Day 5 -- RED check-in (elevated distress + keyword)
    # ------------------------------------------------------------------
    _banner("Step 4: Day 5 Check-In (Elevated -- Triggers Escalation)")

    checkin_day5 = CheckIn(
        participant_id=participant.participant_id,
        org_id=policy.org_id,
        mood_score=2.0,
        sleep_quality=1.5,
        energy_level=1.0,
        distress_level=9.0,
        keyword_flags=["relapse"],
        notes="(Synthetic) Participant reported difficulty coping.",
    )
    result_day5 = evaluate_check_in(checkin_day5, policy)
    print(f"Check-in result: {result_day5}")
    print(f"  Flag: {result_day5.flag.value}")
    print(f"  Requires human review: {result_day5.requires_human_review()}")
    for reason in result_day5.reasons:
        print(f"  - {reason}")

    audit_log.append(AuditEntry(
        org_id=policy.org_id,
        actor_id="SYSTEM",
        actor_role="SYSTEM",
        event_type=AuditEventType.SIGNAL_EVALUATED,
        target_entity=participant.participant_id,
        metadata={"flag": result_day5.flag.value, "reasons": result_day5.reasons},
    ))

    # ------------------------------------------------------------------
    # Step 6: Escalation lifecycle
    # ------------------------------------------------------------------
    _banner("Step 5: Full Escalation Lifecycle")

    # Open case
    case = orchestrator.open_case(
        participant=participant,
        flag_level=result_day5.flag,
        indicators=result_day5.reasons,
        policy=policy,
    )
    print(f"Case opened: {case.case_id}")
    print(f"  State: {case.state.value}")
    print(f"  Automated interaction suspended: {case.automated_interaction_suspended}")
    print(f"  is_interaction_suspended: {orchestrator.is_interaction_suspended(participant.participant_id)}")

    # Send alert
    case = orchestrator.send_alert(case)
    print(f"\nAlert sent. State: {case.state.value}")

    # Notify clinician
    case = orchestrator.notify_clinician(case, "dr_synthetic_001")
    print(f"Clinician notified: dr_synthetic_001. State: {case.state.value}")

    # Clinician acknowledges (human gate)
    case = orchestrator.acknowledge(case, "dr_synthetic_001")
    print(f"Clinician acknowledged. State: {case.state.value}")

    # Clinician resolves with notes
    case = orchestrator.resolve(
        case,
        "dr_synthetic_001",
        "Synthetic resolution: Follow-up appointment scheduled. "
        "Safety plan reviewed. No immediate safety concern identified.",
    )
    print(f"Case resolved. State: {case.state.value}")
    print(f"  Resolution notes: {case.resolution_notes}")
    print(f"  Automated interaction resumed: {not case.automated_interaction_suspended}")

    # ------------------------------------------------------------------
    # Step 7: Generate Transparency Report
    # ------------------------------------------------------------------
    _banner("Step 6: Decision Transparency Report")

    report = generate_transparency_report(case, evaluation_reasons=result_day5.reasons)
    report_dict = report.to_dict()
    print(json.dumps(report_dict, indent=2, default=str))

    # ------------------------------------------------------------------
    # Step 8: Export audit log
    # ------------------------------------------------------------------
    _banner("Step 7: Audit Log Export (Compliance Review)")

    export = audit_log.export_for_review(org_id=policy.org_id)
    print(f"Export metadata:")
    print(json.dumps(export["export_metadata"], indent=2))
    print(f"\nTotal audit entries for {policy.org_id}: {export['export_metadata']['entry_count']}")
    print(f"Chain integrity: {export['export_metadata']['chain_integrity']}")

    # Verify chain
    valid, broken_at = audit_log.verify_chain()
    print(f"\nFull chain verification: valid={valid}, broken_at={broken_at}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    _banner("Scenario Complete")
    print("This demo exercised:")
    print("  - Partner policy loading and registration")
    print("  - Participant enrollment (synthetic data only)")
    print("  - Signal evaluation with workflow risk flagging")
    print("  - Full escalation lifecycle with human-in-the-loop gates")
    print("  - Decision Transparency Report generation")
    print("  - Append-only, tamper-evident audit log with export")
    print()
    print("All data was synthetic. No real participants, PHI, or PII.")
    print("This software is not a medical device.")


if __name__ == "__main__":
    main()
