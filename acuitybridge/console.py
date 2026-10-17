"""
Local pilot console for the AcuityBridge sandbox.

Serves a small HTTP API and a browser page on top of the existing
orchestrator:

* partner policies and the hash-chained audit log persist in SQLite
* check-ins are evaluated with the current signal evaluator
* elevated flags open an escalation case and assign the sandbox clinician
* acknowledgment and resolution stay behind the existing human gates
* notification channels are recorded as stubs and are not delivered
* wearable payloads from Apple HealthKit and Fitbit shapes are normalized
  locally; no vendor API is called

Run it from the repository root::

    python -m acuitybridge.console --host 127.0.0.1 --port 8741

DISCLAIMER: This console is a synthetic sandbox. It is not a medical
device, does not contact crisis services, and does not replace clinician
review.
"""

from __future__ import annotations

import argparse
import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field, ValidationError, field_validator

from acuitybridge import __version__
from acuitybridge.audit import AuditEntry, AuditEventType
from acuitybridge.config import PartnerPolicy, load_policies_from_yaml
from acuitybridge.escalation import (
    EscalationOrchestrator,
    InvalidTransitionError,
    UnauthorizedAcknowledgmentError,
)
from acuitybridge.models import BiomarkerReading, CheckIn, Participant
from acuitybridge.notifications import queue_clinician_notifications
from acuitybridge.rbac import get_permissions_for_role, require_permission
from acuitybridge.sessions import (
    SANDBOX_CLINICIAN_ID,
    AuthenticationError,
    SessionActor,
    authenticate,
)
from acuitybridge.signal_evaluator import SignalEvaluationResult, evaluate_check_in
from acuitybridge.store import (
    SqliteAuditLog,
    SqliteBackend,
    SqliteCaseStore,
    SqliteCheckInStore,
    SqlitePolicyRegistry,
    SqliteWearableStore,
)
from acuitybridge.wearables import WearableIngest, normalize_samples
from acuitybridge.transparency_report import generate_transparency_report


MAX_BODY_BYTES = 64 * 1024
STATIC_INDEX = Path(__file__).with_name("static") / "console.html"

_DISCLAIMER = (
    "This software is not a medical device. Outputs are workflow flags for "
    "human review, not diagnoses. Sandbox data is synthetic."
)


class ApiError(Exception):
    """HTTP error with a status code and a client-facing message."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class CheckInRequest(BaseModel):
    """Synthetic check-in submitted from the pilot console."""

    org_id: str = Field(..., min_length=1, max_length=80)
    display_name: str = Field(default="Synthetic participant", max_length=120)
    participant_id: str | None = Field(default=None, max_length=80)
    mood_score: float | None = Field(default=None, ge=0, le=10)
    sleep_quality: float | None = Field(default=None, ge=0, le=10)
    energy_level: float | None = Field(default=None, ge=0, le=10)
    distress_level: float | None = Field(default=None, ge=0, le=10)
    keyword_flags: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=2000)
    heart_rate_variability: float | None = Field(default=None, ge=0, le=500)
    sleep_hours: float | None = Field(default=None, ge=0, le=24)

    def keywords(self) -> list[str]:
        cleaned = []
        for keyword in self.keyword_flags:
            text = keyword.strip()
            if not text:
                continue
            if len(text) > 80:
                raise ValueError("Each keyword must be 80 characters or fewer.")
            cleaned.append(text)
        return cleaned


class ResolveRequest(BaseModel):
    """Clinician resolution notes."""

    resolution_notes: str = Field(..., min_length=1, max_length=4000)

    @field_validator("resolution_notes")
    @classmethod
    def notes_required(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Resolution notes are required.")
        return text


class PilotApp:
    """Workflow operations used by the HTTP handlers."""

    def __init__(self, db_path: str | Path, seed_path: str | Path | None = None) -> None:
        self.backend = SqliteBackend(db_path)
        self.policies = SqlitePolicyRegistry(self.backend)
        self.audit = SqliteAuditLog(self.backend)
        self.cases = SqliteCaseStore(self.backend)
        self.check_ins = SqliteCheckInStore(self.backend)
        self.wearables = SqliteWearableStore(self.backend)
        self.orchestrator = EscalationOrchestrator(self.audit)
        self.orchestrator.restore_suspension(
            [
                case.participant_id
                for case in self.cases.list()
                if case.automated_interaction_suspended
            ]
        )
        self.seed_if_empty(seed_path)

    def close(self) -> None:
        self.backend.close()

    def seed_if_empty(self, seed_path: str | Path | None) -> int:
        """Load YAML policies when the registry has none.

        Returns the number of policies inserted.
        """
        if seed_path is None or len(self.policies) > 0:
            return 0
        path = Path(seed_path)
        if not path.exists():
            return 0
        inserted = 0
        for policy in load_policies_from_yaml(path):
            self.policies.register(policy)
            self.audit.append(
                AuditEntry(
                    org_id=policy.org_id,
                    actor_id="SYSTEM",
                    actor_role="SYSTEM",
                    event_type=AuditEventType.POLICY_REGISTERED,
                    target_entity=policy.org_id,
                    metadata={"org_name": policy.org_name, "source": str(path.name)},
                )
            )
            inserted += 1
        return inserted

    def actor_view(self, actor: SessionActor) -> dict[str, Any]:
        return {
            "actor_id": actor.actor_id,
            "role": actor.role.value,
            "label": actor.label,
            "permissions": get_permissions_for_role(actor.role),
            "disclaimer": _DISCLAIMER,
        }

    def list_policies(self, actor: SessionActor) -> dict[str, Any]:
        full = actor.role.value in {"ADMIN", "CLINICIAN", "AUDITOR"}
        policies = []
        for org_id in self.policies.list_orgs():
            policy = self.policies.get(org_id)
            if full:
                policies.append(policy.model_dump(mode="json"))
            else:
                policies.append(_policy_summary(policy))
        return {"policies": policies, "detail": "full" if full else "summary"}

    def create_policy(self, actor: SessionActor, payload: dict[str, Any]) -> dict[str, Any]:
        require_permission(actor.role, "manage_policy")
        policy = PartnerPolicy.model_validate(payload)
        try:
            self.policies.register(policy)
        except ValueError as exc:
            raise ApiError(409, str(exc)) from exc
        self._audit_policy(actor, AuditEventType.POLICY_REGISTERED, policy)
        return policy.model_dump(mode="json")

    def update_policy(
        self, actor: SessionActor, org_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        require_permission(actor.role, "manage_policy")
        policy = PartnerPolicy.model_validate(payload)
        if policy.org_id != org_id:
            raise ApiError(400, "Policy org_id does not match the URL.")
        try:
            self.policies.update(policy)
        except KeyError as exc:
            raise ApiError(404, str(exc)) from exc
        self._audit_policy(actor, AuditEventType.POLICY_UPDATED, policy)
        return policy.model_dump(mode="json")

    def submit_check_in(self, actor: SessionActor, payload: dict[str, Any]) -> dict[str, Any]:
        require_permission(actor.role, "submit_check_in")
        request = CheckInRequest.model_validate(payload)
        try:
            keywords = request.keywords()
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc
        try:
            policy = self.policies.get(request.org_id)
        except KeyError as exc:
            raise ApiError(404, str(exc)) from exc

        supplied_id = (request.participant_id or "").strip()
        participant = Participant(
            participant_id=supplied_id or str(uuid.uuid4()),
            org_id=policy.org_id,
            display_name=request.display_name.strip() or "Synthetic participant",
        )
        check_in = CheckIn(
            participant_id=participant.participant_id,
            org_id=policy.org_id,
            mood_score=request.mood_score,
            sleep_quality=request.sleep_quality,
            energy_level=request.energy_level,
            distress_level=request.distress_level,
            keyword_flags=keywords,
            notes=request.notes,
        )
        readings = _biomarkers(participant, request)
        result = evaluate_check_in(check_in, policy, readings or None)

        self.audit.append(
            AuditEntry(
                org_id=policy.org_id,
                actor_id=actor.actor_id,
                actor_role=actor.role.value,
                event_type=AuditEventType.SIGNAL_EVALUATED,
                target_entity=participant.participant_id,
                metadata={
                    "check_in_id": check_in.check_in_id,
                    "flag": result.flag.value,
                    "reasons": result.reasons,
                    "display_name": participant.display_name,
                },
            )
        )

        case_payload, report_payload, notifications = self._open_review(
            participant, policy, result
        )

        record = {
            "check_in_id": check_in.check_in_id,
            "org_id": policy.org_id,
            "participant_id": participant.participant_id,
            "display_name": participant.display_name,
            "flag": result.flag.value,
            "reasons": result.reasons,
            "requires_human_review": result.requires_human_review(),
            "case_id": None if case_payload is None else case_payload["case_id"],
            "created_at": check_in.timestamp.isoformat(),
            "notifications": notifications,
        }
        self.check_ins.insert(record)

        return {
            "check_in": check_in.model_dump(mode="json"),
            "participant": participant.model_dump(mode="json"),
            "evaluation": {
                "flag": result.flag.value,
                "reasons": result.reasons,
                "requires_human_review": result.requires_human_review(),
            },
            "case": case_payload,
            "report": report_payload,
            "notifications": notifications,
            "disclaimer": _DISCLAIMER,
        }

    def ingest_wearable(self, actor: SessionActor, payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize a synthetic HealthKit or Fitbit payload and evaluate it.

        Nothing is fetched from a device vendor. Elevated flags follow the
        same clinician gate as a self-report check-in.
        """
        require_permission(actor.role, "submit_check_in")
        request = WearableIngest.model_validate(payload)
        try:
            metrics = normalize_samples(request.source, request.samples)
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc
        try:
            policy = self.policies.get(request.org_id)
        except KeyError as exc:
            raise ApiError(404, str(exc)) from exc

        supplied_id = (request.participant_id or "").strip()
        participant = Participant(
            participant_id=supplied_id or str(uuid.uuid4()),
            org_id=policy.org_id,
            display_name=request.display_name.strip() or "Synthetic participant",
        )
        readings: list[BiomarkerReading] = []
        stored: list[dict[str, Any]] = []
        for metric in metrics:
            reading = BiomarkerReading(
                participant_id=participant.participant_id,
                org_id=policy.org_id,
                metric_name=metric.metric_name,
                value=metric.value,
                unit=metric.unit,
            )
            readings.append(reading)
            record = {
                "reading_id": reading.reading_id,
                "org_id": policy.org_id,
                "participant_id": participant.participant_id,
                "display_name": participant.display_name,
                "source": request.source,
                "vendor_type": metric.vendor_type,
                "metric_name": metric.metric_name,
                "value": metric.value,
                "unit": metric.unit,
                "created_at": reading.timestamp.isoformat(),
            }
            self.wearables.insert(record)
            stored.append(record)

        self.audit.append(
            AuditEntry(
                org_id=policy.org_id,
                actor_id=actor.actor_id,
                actor_role=actor.role.value,
                event_type=AuditEventType.WEARABLE_INGESTED,
                target_entity=participant.participant_id,
                metadata={
                    "source": request.source,
                    "metrics": [item.metric_name for item in metrics],
                    "reading_ids": [item.reading_id for item in readings],
                    "note": "Local stub. No vendor API was called.",
                },
            )
        )

        check_in = CheckIn(
            participant_id=participant.participant_id,
            org_id=policy.org_id,
            notes=f"Wearable stub ingest from {request.source}. Not a clinical measurement.",
        )
        result = evaluate_check_in(check_in, policy, readings)
        self.audit.append(
            AuditEntry(
                org_id=policy.org_id,
                actor_id=actor.actor_id,
                actor_role=actor.role.value,
                event_type=AuditEventType.SIGNAL_EVALUATED,
                target_entity=participant.participant_id,
                metadata={
                    "source": request.source,
                    "flag": result.flag.value,
                    "reasons": result.reasons,
                },
            )
        )
        case_payload, report_payload, notifications = self._open_review(
            participant, policy, result
        )
        return {
            "source": request.source,
            "participant": participant.model_dump(mode="json"),
            "readings": stored,
            "evaluation": {
                "flag": result.flag.value,
                "reasons": result.reasons,
                "requires_human_review": result.requires_human_review(),
            },
            "case": case_payload,
            "report": report_payload,
            "notifications": notifications,
            "disclaimer": (
                "Wearable values are synthetic workflow inputs from a local stub. "
                "No Apple or Fitbit API was called. " + _DISCLAIMER
            ),
        }

    def list_wearables(self, actor: SessionActor, org_id: str) -> dict[str, Any]:
        _require_any(
            actor,
            ("view_own_data", "view_escalation", "query_audit"),
        )
        self._require_org(org_id)
        return {"org_id": org_id, "readings": self.wearables.list(org_id)}

    def _open_review(
        self,
        participant: Participant,
        policy: PartnerPolicy,
        result: SignalEvaluationResult,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
        if not result.requires_human_review():
            return None, None, []
        case = self.orchestrator.open_case(
            participant,
            result.flag,
            result.reasons,
            policy,
        )
        case = self.orchestrator.send_alert(case)
        case = self.orchestrator.notify_clinician(case, SANDBOX_CLINICIAN_ID)
        self.cases.upsert(case)
        receipts = queue_clinician_notifications(policy, case, SANDBOX_CLINICIAN_ID)
        return (
            case.model_dump(mode="json"),
            generate_transparency_report(case, result.reasons).to_dict(),
            [item.model_dump(mode="json") for item in receipts],
        )

    def list_check_ins(self, actor: SessionActor, org_id: str) -> dict[str, Any]:
        _require_any(
            actor,
            ("view_own_data", "view_escalation", "query_audit"),
        )
        self._require_org(org_id)
        return {"org_id": org_id, "check_ins": self.check_ins.list(org_id)}

    def list_cases(self, actor: SessionActor, org_id: str) -> dict[str, Any]:
        require_permission(actor.role, "view_escalation")
        self._require_org(org_id)
        cases = [case.model_dump(mode="json") for case in self.cases.list(org_id)]
        return {"org_id": org_id, "cases": cases}

    def get_report(self, actor: SessionActor, case_id: str) -> dict[str, Any]:
        require_permission(actor.role, "view_escalation")
        case = self._case_for_actor(case_id)
        return generate_transparency_report(case).to_dict()

    def acknowledge_case(self, actor: SessionActor, case_id: str) -> dict[str, Any]:
        require_permission(actor.role, "acknowledge_escalation")
        case = self._case_for_actor(case_id)
        try:
            updated = self.orchestrator.acknowledge(case, actor.actor_id)
        except InvalidTransitionError as exc:
            raise ApiError(409, str(exc)) from exc
        except UnauthorizedAcknowledgmentError as exc:
            raise ApiError(403, str(exc)) from exc
        self.cases.upsert(updated)
        return updated.model_dump(mode="json")

    def resolve_case(
        self, actor: SessionActor, case_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        require_permission(actor.role, "resolve_escalation")
        body = ResolveRequest.model_validate(payload)
        case = self._case_for_actor(case_id)
        try:
            updated = self.orchestrator.resolve(
                case, actor.actor_id, body.resolution_notes.strip()
            )
        except InvalidTransitionError as exc:
            raise ApiError(409, str(exc)) from exc
        except UnauthorizedAcknowledgmentError as exc:
            raise ApiError(403, str(exc)) from exc
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc
        self.cases.upsert(updated)
        return updated.model_dump(mode="json")

    def query_audit(self, actor: SessionActor, org_id: str) -> dict[str, Any]:
        require_permission(actor.role, "query_audit")
        self._require_org(org_id)
        entries = self.audit.query(org_id)
        valid, broken_at = self.audit.verify_chain()
        visible = entries[-100:]
        return {
            "org_id": org_id,
            "chain_valid": valid,
            "broken_at": broken_at,
            "total_entries": len(entries),
            "entries": [_audit_dict(entry) for entry in visible],
        }

    def export_audit(self, actor: SessionActor, org_id: str) -> dict[str, Any]:
        require_permission(actor.role, "export_audit")
        self._require_org(org_id)
        self.audit.append(
            AuditEntry(
                org_id=org_id,
                actor_id=actor.actor_id,
                actor_role=actor.role.value,
                event_type=AuditEventType.AUDIT_EXPORTED,
                target_entity=org_id,
                metadata={"source": "pilot_console"},
            )
        )
        return self.audit.export_for_review(org_id)

    def _require_org(self, org_id: str) -> PartnerPolicy:
        if not org_id:
            raise ApiError(400, "org_id is required.")
        try:
            return self.policies.get(org_id)
        except KeyError as exc:
            raise ApiError(404, str(exc)) from exc

    def _case_for_actor(self, case_id: str):
        try:
            return self.cases.get(case_id)
        except KeyError as exc:
            raise ApiError(404, str(exc)) from exc

    def _audit_policy(
        self, actor: SessionActor, event_type: AuditEventType, policy: PartnerPolicy
    ) -> None:
        self.audit.append(
            AuditEntry(
                org_id=policy.org_id,
                actor_id=actor.actor_id,
                actor_role=actor.role.value,
                event_type=event_type,
                target_entity=policy.org_id,
                metadata={"org_name": policy.org_name},
            )
        )


def _policy_summary(policy: PartnerPolicy) -> dict[str, Any]:
    return {
        "org_id": policy.org_id,
        "org_name": policy.org_name,
        "consent_model": policy.consent_model,
        "clinician_ack_sla_seconds": policy.clinician_ack_sla_seconds,
        "notification_channels": list(policy.notification_channels),
        "human_review_required_flags": [
            flag.value for flag in policy.human_review_required_flags
        ],
    }


def _biomarkers(participant: Participant, request: CheckInRequest) -> list[BiomarkerReading]:
    readings: list[BiomarkerReading] = []
    if request.heart_rate_variability is not None:
        readings.append(
            BiomarkerReading(
                participant_id=participant.participant_id,
                org_id=participant.org_id,
                metric_name="heart_rate_variability",
                value=request.heart_rate_variability,
                unit="ms",
            )
        )
    if request.sleep_hours is not None:
        readings.append(
            BiomarkerReading(
                participant_id=participant.participant_id,
                org_id=participant.org_id,
                metric_name="sleep_hours",
                value=request.sleep_hours,
                unit="hours",
            )
        )
    return readings


def _audit_dict(entry: AuditEntry) -> dict[str, Any]:
    payload = entry.model_dump(mode="json")
    payload["entry_hash"] = entry.compute_hash()
    return payload


def _require_any(actor: SessionActor, actions: tuple[str, ...]) -> None:
    permissions = get_permissions_for_role(actor.role)
    if not any(permissions.get(action) for action in actions):
        raise PermissionError(
            f"Role '{actor.role.value}' is not permitted to perform this action."
        )


def _format_validation(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(item) for item in err["loc"]) or "body"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def build_handler(app: PilotApp) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to one ``PilotApp``."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            self._route("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._route("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._route("PUT")

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[pilot] {self.address_string()} {fmt % args}")

        def _route(self, method: str) -> None:
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            query = parse_qs(parsed.query)
            try:
                if method == "GET" and parts == []:
                    self._send_html()
                    return
                if method == "GET" and parts == ["favicon.ico"]:
                    self._send(204, b"")
                    return
                if method == "GET" and parts == ["api", "health"]:
                    self._send_json(
                        200,
                        {
                            "status": "ok",
                            "service": "acuitybridge-pilot-console",
                            "version": __version__,
                            "persistence": "sqlite",
                            "disclaimer": _DISCLAIMER,
                        },
                    )
                    return

                actor = authenticate(self.headers.get("Authorization"))
                self._dispatch(method, parts, query, actor)
            except AuthenticationError as exc:
                self._send_json(401, {"error": str(exc)})
            except PermissionError as exc:
                self._send_json(403, {"error": str(exc)})
            except ApiError as exc:
                self._send_json(exc.status, {"error": exc.message})
            except ValidationError as exc:
                self._send_json(422, {"error": _format_validation(exc)})
            except Exception as exc:  # pragma: no cover - defensive
                self._send_json(500, {"error": f"Sandbox error: {exc}"})

        def _dispatch(
            self,
            method: str,
            parts: list[str],
            query: dict[str, list[str]],
            actor: SessionActor,
        ) -> None:
            org_id = _query_one(query, "org_id")
            if method == "GET" and parts == ["api", "me"]:
                self._send_json(200, app.actor_view(actor))
                return
            if method == "GET" and parts == ["api", "policies"]:
                self._send_json(200, app.list_policies(actor))
                return
            if method == "POST" and parts == ["api", "policies"]:
                self._send_json(201, app.create_policy(actor, self._read_json()))
                return
            if method == "PUT" and len(parts) == 3 and parts[:2] == ["api", "policies"]:
                self._send_json(200, app.update_policy(actor, parts[2], self._read_json()))
                return
            if method == "POST" and parts == ["api", "check-ins"]:
                self._send_json(201, app.submit_check_in(actor, self._read_json()))
                return
            if method == "GET" and parts == ["api", "check-ins"]:
                self._send_json(200, app.list_check_ins(actor, org_id))
                return
            if method == "POST" and parts == ["api", "wearables"]:
                self._send_json(201, app.ingest_wearable(actor, self._read_json()))
                return
            if method == "GET" and parts == ["api", "wearables"]:
                self._send_json(200, app.list_wearables(actor, org_id))
                return
            if method == "GET" and parts == ["api", "cases"]:
                self._send_json(200, app.list_cases(actor, org_id))
                return
            if method == "GET" and len(parts) == 4 and parts[:2] == ["api", "cases"] and parts[3] == "report":
                self._send_json(200, app.get_report(actor, parts[2]))
                return
            if method == "POST" and len(parts) == 4 and parts[:2] == ["api", "cases"] and parts[3] == "acknowledge":
                self._read_optional_json()
                self._send_json(200, app.acknowledge_case(actor, parts[2]))
                return
            if method == "POST" and len(parts) == 4 and parts[:2] == ["api", "cases"] and parts[3] == "resolve":
                self._send_json(200, app.resolve_case(actor, parts[2], self._read_json()))
                return
            if method == "GET" and parts == ["api", "audit"]:
                self._send_json(200, app.query_audit(actor, org_id))
                return
            if method == "GET" and parts == ["api", "audit", "export"]:
                self._send_json(200, app.export_audit(actor, org_id))
                return
            raise ApiError(404, "No route matches that request.")

        def _read_json(self) -> dict[str, Any]:
            length_header = self.headers.get("Content-Length")
            if length_header is None:
                raise ApiError(411, "Content-Length is required.")
            try:
                length = int(length_header)
            except ValueError as exc:
                raise ApiError(400, "Content-Length must be an integer.") from exc
            if length < 0 or length > MAX_BODY_BYTES:
                self.close_connection = True
                raise ApiError(413, "Request body exceeds the 64KB sandbox limit.")
            content_type = self.headers.get("Content-Type", "")
            if "application/json" not in content_type:
                raise ApiError(415, "Content-Type must be application/json.")
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ApiError(400, "Request body must be JSON.") from exc
            if not isinstance(payload, dict):
                raise ApiError(400, "JSON body must be an object.")
            return payload

        def _read_optional_json(self) -> dict[str, Any]:
            length_header = self.headers.get("Content-Length")
            if length_header is None or int(length_header) == 0:
                return {}
            return self._read_json()

        def _send_html(self) -> None:
            if not STATIC_INDEX.exists():
                self._send_json(500, {"error": "Pilot console page is missing."})
                return
            body = STATIC_INDEX.read_bytes()
            self._send(200, body, "text/html; charset=utf-8")

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _send(self, status: int, body: bytes, content_type: str | None = None) -> None:
            self.send_response(status)
            if content_type:
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _query_one(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return values[0] if values else ""


def serve(app: PilotApp, host: str, port: int) -> ThreadingHTTPServer:
    """Bind the pilot console without starting the request loop."""
    server = ThreadingHTTPServer((host, port), build_handler(app))
    return server


def default_seed_path() -> Path | None:
    candidate = Path(__file__).resolve().parents[1] / "examples" / "partner_policies.yaml"
    if candidate.exists():
        return candidate
    return None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="AcuityBridge local pilot console")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8741)
    parser.add_argument("--db", default="data/acuitybridge.sqlite")
    parser.add_argument(
        "--seed",
        default=None,
        help="YAML policy file loaded when the database has no policies.",
    )
    args = parser.parse_args(argv)
    seed = Path(args.seed) if args.seed else default_seed_path()
    app = PilotApp(args.db, seed)
    server = serve(app, args.host, args.port)
    host, port = server.server_address[:2]
    print("AcuityBridge pilot console")
    print(_DISCLAIMER)
    print(f"Listening on http://{host}:{port}")
    print(f"Open http://127.0.0.1:{port}")
    print(f"Database: {Path(args.db).resolve()}")
    print("Sandbox roles: Sandbox Participant, Clinician, Admin, Auditor")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping pilot console.")
    finally:
        server.server_close()
        app.close()


if __name__ == "__main__":
    main()
