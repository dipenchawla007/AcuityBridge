"""
Partner Policy Engine -- Multi-Tenant Configuration for AcuityBridge.

This module implements the partner-configurable workflow policy system that
governs how the Safety & Escalation Orchestrator behaves for each deploying
organization.  Every partner (clinic, employer, community organization) defines
its own escalation thresholds, crisis resource routing, consent model, and
clinician acknowledgment SLA.

**Why this is partner-configurable:**

Each deployment is different.  A VA medical center routes crisis situations to
the Veterans Crisis Line, not 988.  A rural community health center may have
longer clinician SLAs due to staffing constraints.  An employer-based EAP
program may use opt-out consent while a clinical research pilot requires
explicit opt-in.  This module encodes those differences as structured,
validated policy objects -- ensuring that partner-specific requirements are
enforced consistently across the orchestrator.

This design reflects the multi-partner, multi-state deployment model that is
fundamental to the project.  There is no single "default" configuration that
works for every care setting.

DISCLAIMER: This module configures decision-support workflow policies only.
It does not define clinical protocols, treatment guidelines, or diagnostic
criteria.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from acuitybridge.models import CrisisResourceTarget, RiskFlag


# ---------------------------------------------------------------------------
# Escalation threshold model
# ---------------------------------------------------------------------------

class EscalationThresholds(BaseModel):
    """Policy-defined thresholds that determine when check-in signals are
    mapped to workflow risk flags.

    These thresholds are **not** clinical cut-offs.  They are configurable
    policy parameters that control workflow routing.  Different partners
    may set different thresholds based on their operational context and
    the populations they serve.

    Each threshold represents the *minimum distress_level score* (0-10)
    at which the corresponding flag activates.  Lower values are more
    conservative (trigger sooner).
    """

    yellow_min_distress: float = Field(
        default=4.0,
        ge=0,
        le=10,
        description=(
            "Distress level at or above which YELLOW flag is raised. "
            "YELLOW requires human review before any action.  "
            "Partners in high-acuity settings may lower this threshold."
        ),
    )
    orange_min_distress: float = Field(
        default=6.0,
        ge=0,
        le=10,
        description=(
            "Distress level at or above which ORANGE flag is raised. "
            "ORANGE requires human review and restricts automated interaction. "
            "Partners with limited clinician availability may adjust this."
        ),
    )
    red_min_distress: float = Field(
        default=8.0,
        ge=0,
        le=10,
        description=(
            "Distress level at or above which RED flag is raised. "
            "RED requires immediate human review and activates crisis "
            "interface protocols per partner policy."
        ),
    )
    low_mood_threshold: float = Field(
        default=3.0,
        ge=0,
        le=10,
        description=(
            "Mood score at or below which the signal evaluator raises "
            "the flag by one level.  Different populations may warrant "
            "different sensitivity."
        ),
    )
    low_sleep_threshold: float = Field(
        default=3.0,
        ge=0,
        le=10,
        description="Sleep quality at or below which it contributes to flag elevation.",
    )

    @field_validator("orange_min_distress")
    @classmethod
    def orange_above_yellow(cls, v: float, info) -> float:
        yellow = info.data.get("yellow_min_distress")
        if yellow is not None and v < yellow:
            raise ValueError(
                f"orange_min_distress ({v}) must be >= yellow_min_distress ({yellow})"
            )
        return v

    @field_validator("red_min_distress")
    @classmethod
    def red_above_orange(cls, v: float, info) -> float:
        orange = info.data.get("orange_min_distress")
        if orange is not None and v < orange:
            raise ValueError(
                f"red_min_distress ({v}) must be >= orange_min_distress ({orange})"
            )
        return v


# ---------------------------------------------------------------------------
# Partner policy model
# ---------------------------------------------------------------------------

class PartnerPolicy(BaseModel):
    """Complete workflow policy for a single partner organization.

    Each field is partner-configurable because deployment contexts vary
    significantly across clinics, employers, community organizations, and
    geographic regions.  Docstrings explain *why* each field must be
    customizable.
    """

    org_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Unique identifier for the partner organization.  Used as the "
            "isolation key for multi-tenant operations -- audit logs, "
            "escalation cases, and policy lookups are scoped by org_id."
        ),
    )
    org_name: str = Field(
        ...,
        min_length=1,
        description="Human-readable name of the organization.",
    )
    escalation_thresholds: EscalationThresholds = Field(
        default_factory=EscalationThresholds,
        description=(
            "Thresholds for mapping signal evaluator outputs to workflow risk "
            "flags.  Partners serving higher-acuity populations may use lower "
            "(more conservative) thresholds."
        ),
    )
    crisis_resource_targets: list[CrisisResourceTarget] = Field(
        default_factory=list,
        description=(
            "Partner-defined crisis resource interfaces.  Different partners "
            "route to different resources: VA facilities use the Veterans "
            "Crisis Line; community centers may use a local mobile crisis "
            "team; employer EAPs may route to an internal on-call clinician.  "
            "These are interface stubs -- operational crisis protocols are the "
            "partner's responsibility."
        ),
    )
    consent_model: str = Field(
        default="opt_in",
        description=(
            "Consent approach: 'opt_in' (explicit consent required before "
            "enrollment) or 'opt_out' (enrolled by default, can withdraw).  "
            "Clinical research pilots typically require opt_in; employer EAPs "
            "may use opt_out with clear disclosure.  Must comply with partner "
            "IRB/ethics requirements."
        ),
    )
    data_retention_days: int = Field(
        default=90,
        ge=30,
        description=(
            "Number of days to retain participant workflow data before "
            "scheduled deletion.  Minimum 30 days (audit integrity).  "
            "Partners in states with longer record-retention requirements "
            "may need higher values."
        ),
    )
    notification_channels: list[str] = Field(
        default_factory=lambda: ["dashboard"],
        description=(
            "Channels for clinician escalation alerts: 'dashboard', 'sms', "
            "'webhook', 'email'.  Rural partners with limited connectivity "
            "may prefer SMS; large hospital systems may use webhook "
            "integration with their existing alerting infrastructure."
        ),
    )
    clinician_ack_sla_seconds: int = Field(
        default=300,
        gt=0,
        description=(
            "Maximum time (in seconds) a clinician has to acknowledge an "
            "escalation before the system triggers the crisis resource "
            "interface per policy.  Partners with 24/7 on-call coverage "
            "may use shorter SLAs; those with limited staffing may need "
            "longer windows.  Must be > 0."
        ),
    )
    escalation_keyword_overrides: list[str] = Field(
        default_factory=list,
        description=(
            "Additional keywords that immediately trigger RED-level "
            "escalation when detected in participant input.  Partners may "
            "add population-specific terms (e.g., substance names for "
            "addiction recovery programs, specific self-harm terminology).  "
            "These supplement -- not replace -- the default keyword list."
        ),
    )
    human_review_required_flags: list[RiskFlag] = Field(
        default_factory=lambda: [RiskFlag.YELLOW, RiskFlag.ORANGE, RiskFlag.RED],
        description=(
            "Risk flag levels that require mandatory human review before "
            "any action.  GREEN is excluded by default (human review "
            "optional for GREEN).  Partners may add GREEN to require "
            "human review for all flags."
        ),
    )

    @field_validator("consent_model")
    @classmethod
    def validate_consent_model(cls, v: str) -> str:
        allowed = {"opt_in", "opt_out"}
        if v not in allowed:
            raise ValueError(f"consent_model must be one of {allowed}, got '{v}'")
        return v


# ---------------------------------------------------------------------------
# Default policy
# ---------------------------------------------------------------------------

DEFAULT_POLICY = PartnerPolicy(
    org_id="default",
    org_name="Default Policy (Conservative Defaults)",
    escalation_thresholds=EscalationThresholds(),
    crisis_resource_targets=[],
    consent_model="opt_in",
    data_retention_days=90,
    notification_channels=["dashboard"],
    clinician_ack_sla_seconds=300,
    escalation_keyword_overrides=[],
)
"""Built-in default policy with conservative, safe defaults.

Uses the lowest reasonable thresholds and shortest SLAs to maximize
safety when no partner-specific policy is configured.  Partners should
always define their own policy for production use.
"""


# ---------------------------------------------------------------------------
# Policy registry (multi-tenant)
# ---------------------------------------------------------------------------

class PolicyRegistry:
    """In-memory multi-tenant policy registry.

    Policies are keyed by ``org_id``.  The registry enforces isolation:
    a policy registered for organization A cannot be retrieved using
    organization B's identifier.

    This class is the core of the multi-tenant pattern -- it ensures that
    each partner's workflow configuration is self-contained and cannot
    leak across organizational boundaries.
    """

    def __init__(self) -> None:
        self._policies: dict[str, PartnerPolicy] = {}

    def register(self, policy: PartnerPolicy) -> None:
        """Register a new partner policy.

        Validates the policy on registration and stores it keyed by
        ``org_id``.  Raises ``ValueError`` if a policy for the same
        ``org_id`` already exists (use ``update()`` to modify).

        Args:
            policy: A validated ``PartnerPolicy`` instance.

        Raises:
            ValueError: If ``org_id`` is already registered.
        """
        if policy.org_id in self._policies:
            raise ValueError(
                f"Policy for org_id '{policy.org_id}' already registered. "
                "Use update() to modify an existing policy."
            )
        self._policies[policy.org_id] = copy.deepcopy(policy)

    def get(self, org_id: str) -> PartnerPolicy:
        """Retrieve the policy for a specific organization.

        Args:
            org_id: The organization identifier.

        Returns:
            A deep copy of the registered ``PartnerPolicy``.

        Raises:
            KeyError: If no policy is registered for ``org_id``.
        """
        if org_id not in self._policies:
            raise KeyError(f"No policy registered for org_id '{org_id}'")
        return copy.deepcopy(self._policies[org_id])

    def update(self, policy: PartnerPolicy) -> None:
        """Update an existing partner policy.

        The policy must already be registered.  Emits a policy-update
        event suitable for audit logging by callers.

        Args:
            policy: The updated ``PartnerPolicy`` instance.

        Raises:
            KeyError: If no policy is registered for the given ``org_id``.
        """
        if policy.org_id not in self._policies:
            raise KeyError(
                f"Cannot update: no policy registered for org_id '{policy.org_id}'"
            )
        self._policies[policy.org_id] = copy.deepcopy(policy)

    def list_orgs(self) -> list[str]:
        """Return a list of all registered organization IDs.

        Returns:
            Sorted list of org_id strings.
        """
        return sorted(self._policies.keys())

    def __len__(self) -> int:
        return len(self._policies)

    def __contains__(self, org_id: str) -> bool:
        return org_id in self._policies


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

def load_policies_from_yaml(path: str | Path) -> list[PartnerPolicy]:
    """Load partner policies from a YAML file.

    The YAML file should contain a top-level ``policies`` key with a list
    of policy objects.  Each object is validated through the
    ``PartnerPolicy`` model.

    Example YAML structure::

        policies:
          - org_id: "clinic_alpha"
            org_name: "Alpha Community Health Center"
            clinician_ack_sla_seconds: 600
            ...

    Args:
        path: Path to the YAML file.

    Returns:
        List of validated ``PartnerPolicy`` instances.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the YAML structure is invalid.
        pydantic.ValidationError: If any policy fails validation.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Policy file not found: {path}")

    with open(path, "r") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict) or "policies" not in raw:
        raise ValueError(
            "YAML file must contain a top-level 'policies' key with a list of policy objects."
        )

    policies_data = raw["policies"]
    if not isinstance(policies_data, list):
        raise ValueError("'policies' must be a list of policy objects.")

    policies: list[PartnerPolicy] = []
    for idx, entry in enumerate(policies_data):
        if not isinstance(entry, dict):
            raise ValueError(f"Policy entry at index {idx} must be a mapping.")

        # Convert nested structures
        if "escalation_thresholds" in entry and isinstance(
            entry["escalation_thresholds"], dict
        ):
            entry["escalation_thresholds"] = EscalationThresholds(
                **entry["escalation_thresholds"]
            )
        if "crisis_resource_targets" in entry and isinstance(
            entry["crisis_resource_targets"], list
        ):
            entry["crisis_resource_targets"] = [
                CrisisResourceTarget(**t) if isinstance(t, dict) else t
                for t in entry["crisis_resource_targets"]
            ]

        policies.append(PartnerPolicy(**entry))

    return policies
