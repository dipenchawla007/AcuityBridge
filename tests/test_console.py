"""HTTP tests for the local pilot console."""

import json
import threading
from urllib import error, request

import pytest

from acuitybridge.config import PartnerPolicy
from acuitybridge.console import PilotApp, serve
from acuitybridge.escalation import EscalationCase
from acuitybridge.models import EscalationState, RiskFlag
from acuitybridge.notifications import queue_clinician_notifications
from acuitybridge.sessions import SANDBOX_CLINICIAN_ID, AuthenticationError, authenticate


ADMIN = "sandbox-admin"
CLINICIAN = "sandbox-clinician"
AUDITOR = "sandbox-auditor"
PARTICIPANT = "sandbox-participant"


@pytest.fixture
def client(tmp_path):
    app = PilotApp(tmp_path / "pilot.sqlite")
    server = serve(app, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    base = f"http://{host}:{port}"

    def call(method, path, token=None, body=None, raw=False):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = request.Request(base + path, data=data, method=method)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with request.urlopen(req) as response:
                payload = response.read()
                if raw:
                    return response.status, payload
                return response.status, json.loads(payload.decode("utf-8"))
        except error.HTTPError as exc:
            payload = exc.read()
            if raw:
                return exc.code, payload
            text = payload.decode("utf-8")
            return exc.code, json.loads(text) if text else {}

    yield call
    server.shutdown()
    server.server_close()
    app.close()


def _policy(org_id="clinic_test"):
    return {
        "org_id": org_id,
        "org_name": "Test Clinic",
        "notification_channels": ["dashboard", "sms", "email", "webhook"],
        "escalation_keyword_overrides": ["overdose"],
    }


def test_health_and_console_page(client):
    status, health = client("GET", "/api/health")
    assert status == 200
    assert health["status"] == "ok"
    assert "not a medical device" in health["disclaimer"]

    page_status, page = client("GET", "/", raw=True)
    text = page.decode("utf-8")
    assert page_status == 200
    assert "not a medical device" in text
    assert "sandbox-clinician" in text

    missing, body = client("GET", "/api/me")
    assert missing == 401
    assert "error" in body


def test_elevated_check_in_requires_clinician_resolution(client):
    created, policy = client("POST", "/api/policies", ADMIN, _policy())
    assert created == 201
    assert policy["org_id"] == "clinic_test"

    denied, _ = client(
        "POST",
        "/api/check-ins",
        CLINICIAN,
        {"org_id": "clinic_test", "distress_level": 9},
    )
    assert denied == 403

    green, quiet = client(
        "POST",
        "/api/check-ins",
        PARTICIPANT,
        {"org_id": "clinic_test", "distress_level": 1, "mood_score": 8, "sleep_quality": 8},
    )
    assert green == 201
    assert quiet["evaluation"]["flag"] == "GREEN"
    assert quiet["case"] is None

    status, elevated = client(
        "POST",
        "/api/check-ins",
        PARTICIPANT,
        {
            "org_id": "clinic_test",
            "distress_level": 9,
            "display_name": "Synthetic participant",
            "keyword_flags": ["overdose"],
        },
    )
    assert status == 201
    assert elevated["evaluation"]["flag"] == "RED"
    assert elevated["evaluation"]["requires_human_review"] is True
    assert elevated["case"]["state"] == "CLINICIAN_NOTIFIED"
    assert elevated["case"]["assigned_clinician_id"] == SANDBOX_CLINICIAN_ID
    channels = {item["channel"]: item for item in elevated["notifications"]}
    assert set(channels) == {"dashboard", "sms", "email", "webhook"}
    assert channels["sms"]["status"] == "recorded_stub"
    assert "No text message was sent" in channels["sms"]["detail"]

    participant_ack, _ = client(
        "POST",
        f"/api/cases/{elevated['case']['case_id']}/acknowledge",
        PARTICIPANT,
    )
    assert participant_ack == 403

    ack, acknowledged = client(
        "POST",
        f"/api/cases/{elevated['case']['case_id']}/acknowledge",
        CLINICIAN,
    )
    assert ack == 200
    assert acknowledged["state"] == "ACKNOWLEDGED"

    empty, _ = client(
        "POST",
        f"/api/cases/{elevated['case']['case_id']}/resolve",
        CLINICIAN,
        {"resolution_notes": "   "},
    )
    assert empty == 422

    resolved_status, resolved = client(
        "POST",
        f"/api/cases/{elevated['case']['case_id']}/resolve",
        CLINICIAN,
        {"resolution_notes": "Clinician reviewed the synthetic flag and closed the case."},
    )
    assert resolved_status == 200
    assert resolved["state"] == "RESOLVED"
    assert resolved["automated_interaction_suspended"] is False

    exported, bundle = client("GET", "/api/audit/export?org_id=clinic_test", AUDITOR)
    assert exported == 200
    assert bundle["export_metadata"]["chain_integrity"] == "VALID"
    assert bundle["export_metadata"]["org_id"] == "clinic_test"

    clinician_export, _ = client("GET", "/api/audit/export?org_id=clinic_test", CLINICIAN)
    assert clinician_export == 403


def test_admin_can_update_thresholds_and_invalid_scores_are_rejected(client):
    client("POST", "/api/policies", ADMIN, _policy())
    listed, policies = client("GET", "/api/policies", ADMIN)
    assert listed == 200
    current = policies["policies"][0]
    current["escalation_thresholds"]["yellow_min_distress"] = 2
    current["clinician_ack_sla_seconds"] = 120
    updated, saved = client("PUT", "/api/policies/clinic_test", ADMIN, current)
    assert updated == 200
    assert saved["clinician_ack_sla_seconds"] == 120
    assert saved["escalation_thresholds"]["yellow_min_distress"] == 2

    participant_view, summary = client("GET", "/api/policies", PARTICIPANT)
    assert participant_view == 200
    assert summary["detail"] == "summary"
    assert "crisis_resource_targets" not in summary["policies"][0]

    bad, error = client(
        "POST",
        "/api/check-ins",
        PARTICIPANT,
        {"org_id": "clinic_test", "distress_level": 11},
    )
    assert bad == 422
    assert "distress_level" in error["error"]


def test_notification_stub_does_not_claim_delivery():
    policy = PartnerPolicy(
        org_id="clinic_test",
        org_name="Test Clinic",
        notification_channels=["sms", "webhook"],
    )
    case = EscalationCase(
        participant_id="participant-1",
        org_id="clinic_test",
        flag_level=RiskFlag.YELLOW,
        state=EscalationState.CLINICIAN_NOTIFIED,
    )
    receipts = queue_clinician_notifications(policy, case, SANDBOX_CLINICIAN_ID)
    assert [item.channel for item in receipts] == ["sms", "webhook"]
    assert all(item.status == "recorded_stub" for item in receipts)
    assert all("No " in item.detail or "No outbound" in item.detail for item in receipts)


def test_wearable_route_rejects_clinicians_and_accepts_participants(client):
    client("POST", "/api/policies", ADMIN, _policy())
    denied, _ = client(
        "POST",
        "/api/wearables",
        CLINICIAN,
        {
            "source": "fitbit",
            "org_id": "clinic_test",
            "samples": [{"type": "hrv", "value": 16}],
        },
    )
    assert denied == 403

    status, body = client(
        "POST",
        "/api/wearables",
        PARTICIPANT,
        {
            "source": "fitbit",
            "org_id": "clinic_test",
            "samples": [{"type": "hrv", "value": 16}],
        },
    )
    assert status == 201
    assert body["evaluation"]["flag"] == "ORANGE"
    assert body["readings"][0]["metric_name"] == "heart_rate_variability"
    assert body["readings"][0]["unit"] == "ms"

    listed, readings = client("GET", "/api/wearables?org_id=clinic_test", CLINICIAN)
    assert listed == 200
    assert readings["readings"][0]["value"] == 16


def test_unknown_token_is_rejected():
    with pytest.raises(AuthenticationError):
        authenticate("Bearer not-a-sandbox-token")
