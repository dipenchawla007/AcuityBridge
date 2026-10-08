"""
Append-Only, Tamper-Evident Audit Log (Hash-Chained).

Every action in the AcuityBridge orchestrator -- signal evaluations,
escalation state transitions, clinician acknowledgments, policy changes,
consent events, and crisis interface triggers -- is recorded as a structured,
append-only audit entry.  Entries are linked via a SHA-256 hash chain that
provides structural tamper evidence: if any entry is modified after the fact,
the chain verification will detect the inconsistency.

**Honest scope note:**  This hash-chain mechanism is a *demonstration* of
tamper-evident design.  It provides structural tamper evidence suitable for
audit review and compliance demonstration.  A production deployment would
use WORM (Write Once Read Many) storage, object-lock, or a cryptographic
commitment scheme (e.g., Merkle trees with an external trust anchor) to
provide stronger guarantees.

**Multi-tenant isolation:**  All queries and exports are scoped by
``org_id``.  Entries belonging to organization A are never visible in
queries or exports for organization B.

DISCLAIMER: This module supports governance and audit workflows only.
It does not perform clinical assessment or store diagnostic information.
"""

from __future__ import annotations

import enum
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Audit event types
# ---------------------------------------------------------------------------

class AuditEventType(str, enum.Enum):
    """Enumeration of all auditable event types in the orchestrator.

    Each event type maps to a specific system action that must be recorded
    for governance, compliance, and operational review.
    """

    # Signal evaluation
    SIGNAL_EVALUATED = "SIGNAL_EVALUATED"

    # Escalation lifecycle
    ESCALATION_OPENED = "ESCALATION_OPENED"
    CLINICIAN_NOTIFIED = "CLINICIAN_NOTIFIED"
    ESCALATION_ACKNOWLEDGED = "ESCALATION_ACKNOWLEDGED"
    ESCALATION_RESOLVED = "ESCALATION_RESOLVED"
    ESCALATION_TIMED_OUT = "ESCALATION_TIMED_OUT"

    # Crisis interface
    CRISIS_INTERFACE_TRIGGERED = "CRISIS_INTERFACE_TRIGGERED"

    # Automated interaction control
    AUTOMATED_INTERACTION_SUSPENDED = "AUTOMATED_INTERACTION_SUSPENDED"
    AUTOMATED_INTERACTION_RESUMED = "AUTOMATED_INTERACTION_RESUMED"

    # Policy management
    POLICY_REGISTERED = "POLICY_REGISTERED"
    POLICY_UPDATED = "POLICY_UPDATED"

    # Consent management
    CONSENT_GRANTED = "CONSENT_GRANTED"
    CONSENT_REVOKED = "CONSENT_REVOKED"

    # Data access
    DATA_ACCESSED = "DATA_ACCESSED"

    # Audit operations
    AUDIT_EXPORTED = "AUDIT_EXPORTED"


# ---------------------------------------------------------------------------
# Audit entry model
# ---------------------------------------------------------------------------

class AuditEntry(BaseModel):
    """A single audit log entry.

    Each entry records who did what, when, for which organization, and
    includes a hash link to the previous entry for tamper evidence.
    """

    entry_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for this audit entry (UUID).",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the event.",
    )
    org_id: str = Field(
        ...,
        description="Organization identifier -- scopes this entry for multi-tenant isolation.",
    )
    actor_id: str = Field(
        ...,
        description="Identifier of the actor (clinician ID, system ID, participant ID).",
    )
    actor_role: str = Field(
        ...,
        description="Role of the actor (CLINICIAN, SYSTEM, PARTICIPANT, ADMIN, AUDITOR).",
    )
    event_type: AuditEventType = Field(
        ...,
        description="The type of event being recorded.",
    )
    target_entity: str = Field(
        default="",
        description="Identifier of the target (participant ID, case ID, policy ID).",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional event-specific data (must not contain real PHI in non-production use).",
    )
    previous_hash: str = Field(
        default="",
        description=(
            "SHA-256 hash of the previous entry's canonical representation. "
            "Empty string for the first entry in the chain."
        ),
    )

    def canonical_bytes(self) -> bytes:
        """Return a deterministic byte representation for hashing.

        Uses sorted JSON serialization to ensure consistent ordering.
        """
        data = {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp.isoformat(),
            "org_id": self.org_id,
            "actor_id": self.actor_id,
            "actor_role": self.actor_role,
            "event_type": self.event_type.value,
            "target_entity": self.target_entity,
            "metadata": self.metadata,
            "previous_hash": self.previous_hash,
        }
        return json.dumps(data, sort_keys=True, default=str).encode("utf-8")

    def compute_hash(self) -> str:
        """Compute the SHA-256 hash of this entry's canonical representation."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# PHI redaction patterns
# ---------------------------------------------------------------------------

# Patterns that might appear in metadata and should be redacted before export.
_PHI_PATTERNS: dict[str, re.Pattern] = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "dob": re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),  # ISO date as potential DOB
    "phone": re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
}

# Keys that are likely to contain PII/PHI and should be fully redacted.
_PHI_KEYS = {"name", "full_name", "first_name", "last_name", "dob", "date_of_birth",
             "ssn", "social_security", "email", "phone", "address", "zip_code"}


def redact_phi_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Strip fields matching PHI patterns from metadata before export.

    This demonstrates data minimization: sensitive fields are replaced
    with ``[REDACTED]`` markers.  This utility should be applied before
    any audit export that leaves the secure environment.

    Args:
        metadata: The original metadata dictionary.

    Returns:
        A new dictionary with PHI-matching fields redacted.
    """
    redacted = {}
    for key, value in metadata.items():
        if key.lower() in _PHI_KEYS:
            redacted[key] = "[REDACTED]"
        elif isinstance(value, str):
            redacted_value = value
            for pattern_name, pattern in _PHI_PATTERNS.items():
                redacted_value = pattern.sub(f"[REDACTED-{pattern_name.upper()}]", redacted_value)
            redacted[key] = redacted_value
        elif isinstance(value, dict):
            redacted[key] = redact_phi_from_metadata(value)
        else:
            redacted[key] = value
    return redacted


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class AuditLog:
    """Append-only, tamper-evident audit log with SHA-256 hash chaining.

    This class provides:

    * **Append-only writes** -- there are no ``update()`` or ``delete()``
      methods.  Once an entry is appended, it cannot be modified through
      this interface.
    * **Hash chain verification** -- each entry stores the SHA-256 hash of
      the previous entry.  ``verify_chain()`` walks the full log and
      detects any tampering.
    * **Multi-tenant query isolation** -- ``query()`` and ``export_for_review()``
      are always scoped by ``org_id``.
    * **PHI redaction on export** -- ``export_for_review()`` applies
      ``redact_phi_from_metadata()`` to all entries before producing the
      export bundle.

    **Honest scope note:**  This is a demonstration mechanism for structural
    tamper evidence.  Production deployment would use WORM storage,
    object-lock, or a cryptographic commitment scheme for stronger
    guarantees.
    """

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._hashes: list[str] = []  # parallel list of computed hashes

    def append(self, entry: AuditEntry) -> AuditEntry:
        """Append a new entry to the audit log.

        Computes the hash chain link from the previous entry (if any)
        and stores it in the new entry's ``previous_hash`` field.

        Args:
            entry: The audit entry to append.

        Returns:
            The entry with ``previous_hash`` populated.
        """
        if self._hashes:
            entry.previous_hash = self._hashes[-1]
        else:
            entry.previous_hash = ""

        self._entries.append(entry)
        self._hashes.append(entry.compute_hash())
        return entry

    def verify_chain(self) -> tuple[bool, Optional[int]]:
        """Walk the log and validate every hash link.

        Returns:
            A tuple of ``(valid, broken_at)`` where ``valid`` is True if
            the entire chain is intact, and ``broken_at`` is the index of
            the first broken link (or None if valid).
        """
        if not self._entries:
            return (True, None)

        for i, entry in enumerate(self._entries):
            # Verify previous_hash link
            if i == 0:
                if entry.previous_hash != "":
                    return (False, 0)
            else:
                expected_prev_hash = self._entries[i - 1].compute_hash()
                if entry.previous_hash != expected_prev_hash:
                    return (False, i)

            # Verify stored hash matches recomputation
            recomputed = entry.compute_hash()
            if self._hashes[i] != recomputed:
                return (False, i)

        return (True, None)

    def query(
        self,
        org_id: str,
        event_type: Optional[AuditEventType] = None,
        time_start: Optional[datetime] = None,
        time_end: Optional[datetime] = None,
        actor_id: Optional[str] = None,
    ) -> list[AuditEntry]:
        """Query audit entries with multi-tenant isolation.

        All queries are scoped by ``org_id`` -- entries from other
        organizations are never returned.

        Args:
            org_id: Required.  Only entries for this organization are returned.
            event_type: Optional filter by event type.
            time_start: Optional inclusive start time.
            time_end: Optional inclusive end time.
            actor_id: Optional filter by actor.

        Returns:
            List of matching ``AuditEntry`` objects (copies).
        """
        results = []
        for entry in self._entries:
            if entry.org_id != org_id:
                continue
            if event_type is not None and entry.event_type != event_type:
                continue
            if time_start is not None and entry.timestamp < time_start:
                continue
            if time_end is not None and entry.timestamp > time_end:
                continue
            if actor_id is not None and entry.actor_id != actor_id:
                continue
            results.append(entry.model_copy(deep=True))
        return results

    def export_for_review(
        self,
        org_id: str,
        time_start: Optional[datetime] = None,
        time_end: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Produce a JSON-serializable export bundle for compliance review.

        Applies PHI redaction to all metadata before export.  Includes
        the chain verification result in the bundle.

        Args:
            org_id: Organization to export.
            time_start: Optional start of export window.
            time_end: Optional end of export window.

        Returns:
            A dictionary suitable for JSON serialization containing
            the entries, chain verification status, and export metadata.
        """
        entries = self.query(org_id, time_start=time_start, time_end=time_end)

        # Apply PHI redaction
        redacted_entries = []
        for entry in entries:
            entry_dict = entry.model_dump()
            entry_dict["metadata"] = redact_phi_from_metadata(entry.metadata)
            entry_dict["timestamp"] = entry.timestamp.isoformat()
            redacted_entries.append(entry_dict)

        chain_valid, broken_at = self.verify_chain()

        return {
            "export_metadata": {
                "org_id": org_id,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "entry_count": len(redacted_entries),
                "chain_integrity": "VALID" if chain_valid else f"BROKEN_AT_INDEX_{broken_at}",
                "scope_note": (
                    "This export uses SHA-256 hash chaining for structural tamper "
                    "evidence. Production deployment would use WORM storage, "
                    "object-lock, or a cryptographic commitment scheme."
                ),
            },
            "entries": redacted_entries,
        }

    @property
    def length(self) -> int:
        """Return the number of entries in the log."""
        return len(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
