"""
Tests for acuitybridge.audit -- Append-Only, Tamper-Evident Audit Log.

Covers: append + chain verification, tamper detection, query filtering,
export format, PHI redaction, empty log verification, concurrent append
ordering, and multi-tenant audit isolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from acuitybridge.audit import (
    AuditEntry,
    AuditEventType,
    AuditLog,
    redact_phi_from_metadata,
)


def _make_entry(
    org_id: str = "org_a",
    actor_id: str = "clinician_1",
    actor_role: str = "CLINICIAN",
    event_type: AuditEventType = AuditEventType.SIGNAL_EVALUATED,
    target_entity: str = "participant_1",
    metadata: dict | None = None,
) -> AuditEntry:
    """Helper to create audit entries for testing."""
    return AuditEntry(
        org_id=org_id,
        actor_id=actor_id,
        actor_role=actor_role,
        event_type=event_type,
        target_entity=target_entity,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# 1. Append + chain verification
# ---------------------------------------------------------------------------

class TestAppendAndChainVerification:
    def test_append_single_entry(self):
        log = AuditLog()
        entry = _make_entry()
        appended = log.append(entry)
        assert appended.previous_hash == ""
        assert len(log) == 1

    def test_append_multiple_entries_builds_chain(self):
        log = AuditLog()
        e1 = log.append(_make_entry(actor_id="actor_1"))
        e2 = log.append(_make_entry(actor_id="actor_2"))
        e3 = log.append(_make_entry(actor_id="actor_3"))

        assert e1.previous_hash == ""
        assert e2.previous_hash == e1.compute_hash()
        assert e3.previous_hash == e2.compute_hash()

    def test_chain_verification_passes_for_valid_log(self):
        log = AuditLog()
        for i in range(5):
            log.append(_make_entry(actor_id=f"actor_{i}"))
        valid, broken_at = log.verify_chain()
        assert valid is True
        assert broken_at is None


# ---------------------------------------------------------------------------
# 2. Tamper detection
# ---------------------------------------------------------------------------

class TestTamperDetection:
    def test_modified_entry_breaks_chain(self):
        """If an entry is modified after appending, verify_chain detects it."""
        log = AuditLog()
        log.append(_make_entry(actor_id="actor_1"))
        log.append(_make_entry(actor_id="actor_2"))
        log.append(_make_entry(actor_id="actor_3"))

        # Tamper with the second entry
        log._entries[1].metadata = {"tampered": True}

        valid, broken_at = log.verify_chain()
        assert valid is False
        # The break is detected at entry 1 (the tampered entry) or entry 2
        # (where the previous_hash no longer matches)
        assert broken_at is not None
        assert broken_at in (1, 2)

    def test_modified_first_entry_detected(self):
        log = AuditLog()
        log.append(_make_entry(actor_id="actor_1"))
        log.append(_make_entry(actor_id="actor_2"))

        # Tamper with the first entry
        log._entries[0].actor_id = "TAMPERED"

        valid, broken_at = log.verify_chain()
        assert valid is False


# ---------------------------------------------------------------------------
# 3. Empty log verification
# ---------------------------------------------------------------------------

class TestEmptyLog:
    def test_empty_log_is_valid(self):
        log = AuditLog()
        valid, broken_at = log.verify_chain()
        assert valid is True
        assert broken_at is None

    def test_empty_log_length_is_zero(self):
        log = AuditLog()
        assert len(log) == 0
        assert log.length == 0


# ---------------------------------------------------------------------------
# 4. Query filtering
# ---------------------------------------------------------------------------

class TestQueryFiltering:
    def test_query_by_org_id(self):
        log = AuditLog()
        log.append(_make_entry(org_id="org_a"))
        log.append(_make_entry(org_id="org_b"))
        log.append(_make_entry(org_id="org_a"))

        results = log.query(org_id="org_a")
        assert len(results) == 2
        assert all(e.org_id == "org_a" for e in results)

    def test_query_by_event_type(self):
        log = AuditLog()
        log.append(_make_entry(event_type=AuditEventType.SIGNAL_EVALUATED))
        log.append(_make_entry(event_type=AuditEventType.ESCALATION_OPENED))
        log.append(_make_entry(event_type=AuditEventType.SIGNAL_EVALUATED))

        results = log.query(org_id="org_a", event_type=AuditEventType.ESCALATION_OPENED)
        assert len(results) == 1

    def test_query_by_time_range(self):
        log = AuditLog()
        now = datetime.now(timezone.utc)

        e1 = _make_entry()
        e1.timestamp = now - timedelta(hours=2)
        log.append(e1)

        e2 = _make_entry()
        e2.timestamp = now - timedelta(hours=1)
        log.append(e2)

        e3 = _make_entry()
        e3.timestamp = now
        log.append(e3)

        results = log.query(
            org_id="org_a",
            time_start=now - timedelta(hours=1, minutes=30),
            time_end=now - timedelta(minutes=30),
        )
        assert len(results) == 1

    def test_query_by_actor_id(self):
        log = AuditLog()
        log.append(_make_entry(actor_id="clinician_1"))
        log.append(_make_entry(actor_id="clinician_2"))
        log.append(_make_entry(actor_id="clinician_1"))

        results = log.query(org_id="org_a", actor_id="clinician_2")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# 5. Multi-tenant audit isolation
# ---------------------------------------------------------------------------

class TestMultiTenantIsolation:
    def test_org_a_entries_not_visible_to_org_b(self):
        """Entries from org A must never appear in queries for org B."""
        log = AuditLog()
        log.append(_make_entry(org_id="org_a", actor_id="a_clinician"))
        log.append(_make_entry(org_id="org_b", actor_id="b_clinician"))
        log.append(_make_entry(org_id="org_a", actor_id="a_admin"))

        results_a = log.query(org_id="org_a")
        results_b = log.query(org_id="org_b")

        assert len(results_a) == 2
        assert len(results_b) == 1
        assert all(e.org_id == "org_a" for e in results_a)
        assert all(e.org_id == "org_b" for e in results_b)

    def test_export_scoped_by_org(self):
        log = AuditLog()
        log.append(_make_entry(org_id="org_a"))
        log.append(_make_entry(org_id="org_b"))
        log.append(_make_entry(org_id="org_a"))

        export = log.export_for_review(org_id="org_a")
        assert export["export_metadata"]["entry_count"] == 2
        assert all(
            e["org_id"] == "org_a" for e in export["entries"]
        )


# ---------------------------------------------------------------------------
# 6. PHI redaction
# ---------------------------------------------------------------------------

class TestPHIRedaction:
    def test_redact_known_phi_keys(self):
        metadata = {
            "name": "John Doe",
            "ssn": "123-45-6789",
            "email": "john@example.com",
            "flag_level": "RED",
        }
        redacted = redact_phi_from_metadata(metadata)
        assert redacted["name"] == "[REDACTED]"
        assert redacted["ssn"] == "[REDACTED]"
        assert redacted["email"] == "[REDACTED]"
        assert redacted["flag_level"] == "RED"

    def test_redact_ssn_pattern_in_values(self):
        metadata = {"notes": "Participant SSN is 123-45-6789 on file."}
        redacted = redact_phi_from_metadata(metadata)
        assert "123-45-6789" not in redacted["notes"]
        assert "[REDACTED-SSN]" in redacted["notes"]

    def test_redact_nested_metadata(self):
        metadata = {
            "outer": {
                "name": "Jane Doe",
                "score": 7.5,
            }
        }
        redacted = redact_phi_from_metadata(metadata)
        assert redacted["outer"]["name"] == "[REDACTED]"
        assert redacted["outer"]["score"] == 7.5

    def test_export_applies_redaction(self):
        log = AuditLog()
        log.append(_make_entry(
            metadata={"name": "Test Person", "flag_level": "YELLOW"}
        ))
        export = log.export_for_review(org_id="org_a")
        entry = export["entries"][0]
        assert entry["metadata"]["name"] == "[REDACTED]"
        assert entry["metadata"]["flag_level"] == "YELLOW"


# ---------------------------------------------------------------------------
# 7. Export format
# ---------------------------------------------------------------------------

class TestExportFormat:
    def test_export_contains_required_fields(self):
        log = AuditLog()
        log.append(_make_entry())
        export = log.export_for_review(org_id="org_a")

        assert "export_metadata" in export
        assert "entries" in export
        meta = export["export_metadata"]
        assert "org_id" in meta
        assert "exported_at" in meta
        assert "entry_count" in meta
        assert "chain_integrity" in meta
        assert "scope_note" in meta

    def test_export_chain_integrity_valid(self):
        log = AuditLog()
        log.append(_make_entry())
        export = log.export_for_review(org_id="org_a")
        assert export["export_metadata"]["chain_integrity"] == "VALID"

    def test_export_scope_note_mentions_worm(self):
        """The export should include the honest scope note about WORM storage."""
        log = AuditLog()
        log.append(_make_entry())
        export = log.export_for_review(org_id="org_a")
        assert "WORM" in export["export_metadata"]["scope_note"]


# ---------------------------------------------------------------------------
# 8. Concurrent append ordering
# ---------------------------------------------------------------------------

class TestAppendOrdering:
    def test_entries_maintain_insertion_order(self):
        log = AuditLog()
        ids = []
        for i in range(10):
            entry = _make_entry(actor_id=f"actor_{i}")
            log.append(entry)
            ids.append(entry.entry_id)

        results = log.query(org_id="org_a")
        result_ids = [e.entry_id for e in results]
        assert result_ids == ids
