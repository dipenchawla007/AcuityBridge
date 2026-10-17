"""Tests for SQLite policy, audit, and case persistence."""

import json
import sqlite3
from pathlib import Path

import pytest

from acuitybridge.audit import AuditEntry, AuditEventType
from acuitybridge.console import PilotApp
from acuitybridge.sessions import authenticate
from acuitybridge.store import AuditChainError


def test_policies_and_audit_survive_restart(tmp_path):
    db = tmp_path / "pilot.sqlite"
    admin = authenticate("Bearer sandbox-admin")
    app = PilotApp(db)
    app.create_policy(admin, {"org_id": "clinic_a", "org_name": "Clinic A"})
    app.create_policy(
        admin,
        {
            "org_id": "clinic_b",
            "org_name": "Clinic B",
            "notification_channels": ["email"],
        },
    )
    app.audit.append(
        AuditEntry(
            org_id="clinic_a",
            actor_id="system",
            actor_role="SYSTEM",
            event_type=AuditEventType.DATA_ACCESSED,
            target_entity="clinic_a",
            metadata={"note": "synthetic"},
        )
    )
    first_length = len(app.audit)
    app.close()

    reopened = PilotApp(db)
    assert reopened.policies.list_orgs() == ["clinic_a", "clinic_b"]
    assert reopened.policies.get("clinic_b").notification_channels == ["email"]
    valid, broken_at = reopened.audit.verify_chain()
    assert valid is True
    assert broken_at is None
    assert len(reopened.audit) == first_length
    org_a = reopened.audit.query("clinic_a")
    assert all(entry.org_id == "clinic_a" for entry in org_a)
    assert "clinic_b" not in {entry.org_id for entry in org_a}
    reopened.close()


def test_duplicate_policy_is_rejected_and_not_written(tmp_path):
    from acuitybridge.console import ApiError

    db = tmp_path / "pilot.sqlite"
    admin = authenticate("Bearer sandbox-admin")
    app = PilotApp(db)
    payload = {"org_id": "clinic_a", "org_name": "Clinic A"}
    app.create_policy(admin, payload)
    with pytest.raises(ApiError) as caught:
        app.create_policy(admin, payload)
    assert caught.value.status == 409
    app.close()

    reopened = PilotApp(db)
    assert reopened.policies.list_orgs() == ["clinic_a"]
    reopened.close()


def test_tampered_audit_row_refuses_startup(tmp_path):
    db = tmp_path / "pilot.sqlite"
    app = PilotApp(db)
    app.audit.append(
        AuditEntry(
            org_id="clinic_a",
            actor_id="auditor.sandbox",
            actor_role="AUDITOR",
            event_type=AuditEventType.DATA_ACCESSED,
            target_entity="clinic_a",
        )
    )
    app.close()

    conn = sqlite3.connect(db)
    payload = json.loads(conn.execute("SELECT payload FROM audit_entries").fetchone()[0])
    payload["actor_id"] = "tampered"
    conn.execute(
        "UPDATE audit_entries SET payload = ? WHERE seq = 1",
        (json.dumps(payload),),
    )
    conn.commit()
    conn.close()

    with pytest.raises(AuditChainError):
        PilotApp(db)


def test_sample_policies_seed_once(tmp_path):
    db = tmp_path / "pilot.sqlite"
    seed = Path(__file__).resolve().parents[1] / "examples" / "partner_policies.yaml"
    app = PilotApp(db, seed)
    assert app.policies.list_orgs() == ["clinic_alpha", "corp_charlie", "va_bravo"]
    seeded_entries = len(app.audit)
    app.close()

    again = PilotApp(db, seed)
    assert len(again.policies) == 3
    assert len(again.audit) == seeded_entries
    assert again.audit.verify_chain()[0] is True
    again.close()
