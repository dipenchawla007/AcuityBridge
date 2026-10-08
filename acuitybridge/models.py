"""
Core data models for AcuityBridge Safety & Escalation Orchestrator.

All models use ``Participant`` (not "Patient") and ``RiskFlag`` (not
"RiskLevel") to emphasize that outputs are policy-driven workflow routing
flags for human review -- not clinical assessments or diagnoses.

DISCLAIMER: This module defines data structures for decision-support
workflows only. It does not perform clinical assessment, diagnosis, or
treatment recommendations.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RiskFlag(str, enum.Enum):
    """Workflow risk flag levels.

    These are policy-driven routing signals for human review -- they are
    **not** clinical assessments, diagnoses, or risk stratification scores.

    * ``GREEN``  -- baseline; human review optional.
    * ``YELLOW`` -- elevated; human review **required** before any action.
    * ``ORANGE`` -- high; human review **required**; automated interaction
      should be restricted.
    * ``RED``    -- critical; human review **required**; escalation and
      crisis interface protocols activated per partner policy.
    """

    GREEN = "GREEN"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    RED = "RED"


class EscalationState(str, enum.Enum):
    """Lifecycle states for an escalation case.

    The state machine enforces sequential transitions -- states cannot be
    skipped.  ``CRISIS_INTERFACE_TRIGGERED`` indicates the crisis resource
    interface was triggered per partner policy (requires that partner
    integration permits; human oversight expected at receiving end).
    """

    DETECTED = "DETECTED"
    ALERT_SENT = "ALERT_SENT"
    CLINICIAN_NOTIFIED = "CLINICIAN_NOTIFIED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    TIMED_OUT = "TIMED_OUT"
    CRISIS_INTERFACE_TRIGGERED = "CRISIS_INTERFACE_TRIGGERED"


class Role(str, enum.Enum):
    """Roles used for role-based access control (RBAC).

    ``PARTICIPANT`` represents the end-user of the workflow.  All other
    roles represent humans with oversight or administrative responsibilities.
    """

    PARTICIPANT = "PARTICIPANT"
    CLINICIAN = "CLINICIAN"
    ADMIN = "ADMIN"
    AUDITOR = "AUDITOR"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class Participant(BaseModel):
    """A participant enrolled in a decision-support workflow.

    Uses ``participant_id`` (not "patient_id") consistent with the
    conservative framing of this project.
    """

    participant_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the participant.",
    )
    org_id: str = Field(
        ...,
        description="Identifier of the partner organization managing this participant.",
    )
    display_name: str = Field(
        default="",
        description=(
            "Optional display label (synthetic only in all examples). "
            "No real PII should be stored in this field outside production "
            "environments with appropriate BAAs and encryption."
        ),
    )
    enrolled_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of enrollment.",
    )
    active: bool = Field(
        default=True,
        description="Whether the participant is currently active in the workflow.",
    )


class CheckIn(BaseModel):
    """A structured self-report check-in from a participant.

    Values are unitless scores (0-10 scale) for signal evaluation.
    These are workflow inputs, not clinical instruments.
    """

    check_in_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for this check-in.",
    )
    participant_id: str = Field(
        ...,
        description="The participant who submitted this check-in.",
    )
    org_id: str = Field(
        ...,
        description="Partner organization ID.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the check-in.",
    )
    mood_score: Optional[float] = Field(
        default=None,
        ge=0,
        le=10,
        description="Self-reported mood (0=lowest, 10=highest). Workflow input only.",
    )
    sleep_quality: Optional[float] = Field(
        default=None,
        ge=0,
        le=10,
        description="Self-reported sleep quality (0=worst, 10=best).",
    )
    energy_level: Optional[float] = Field(
        default=None,
        ge=0,
        le=10,
        description="Self-reported energy level.",
    )
    distress_level: Optional[float] = Field(
        default=None,
        ge=0,
        le=10,
        description="Self-reported distress (0=none, 10=extreme).",
    )
    keyword_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Keywords detected in free-text input that match partner-defined "
            "escalation keyword lists. Used for workflow routing only."
        ),
    )
    notes: str = Field(
        default="",
        description="Free-text notes (synthetic only in examples).",
    )


class BiomarkerReading(BaseModel):
    """A physiological or behavioral data point from a wearable device.

    These readings serve as supplementary workflow signals. They are not
    clinical measurements and do not constitute medical monitoring.
    """

    reading_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for this reading.",
    )
    participant_id: str = Field(
        ...,
        description="Participant who generated this reading.",
    )
    org_id: str = Field(
        ...,
        description="Partner organization ID.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the reading.",
    )
    metric_name: str = Field(
        ...,
        description="Name of the metric (e.g., 'heart_rate_variability', 'sleep_hours', 'step_count').",
    )
    value: float = Field(
        ...,
        description="Numeric value of the metric.",
    )
    unit: str = Field(
        default="",
        description="Unit of measurement (e.g., 'ms', 'hours', 'steps').",
    )


class CrisisResourceTarget(BaseModel):
    """Configuration for a partner-defined crisis resource interface.

    This model defines *where* to route when the crisis interface is
    triggered per partner policy.  It does **not** guarantee connection to
    emergency services.  Partners are responsible for operational crisis
    protocols.
    """

    target_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for this crisis resource target.",
    )
    name: str = Field(
        ...,
        description="Human-readable name (e.g., 'Partner Internal Hotline', '988 Lifeline where available').",
    )
    target_type: str = Field(
        ...,
        description="Type of resource: 'phone', 'webhook', 'internal_queue', or 'external_api'.",
    )
    endpoint: str = Field(
        ...,
        description="Phone number, URL, or queue identifier (stub in development).",
    )
    requires_baa: bool = Field(
        default=True,
        description="Whether this target requires an executed BAA before use.",
    )
