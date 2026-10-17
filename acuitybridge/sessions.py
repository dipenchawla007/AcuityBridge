"""
Sandbox session hooks for the local pilot console.

Maps a bearer token to an actor id and an existing RBAC role. These
tokens are fixed sandbox credentials for local synthetic workflows.
They are not an identity provider, and they must not be reused as
production authentication.

DISCLAIMER: This module gates decision-support workflow access only.
It does not establish clinical privileges or emergency authority.
"""

from __future__ import annotations

import hmac

from pydantic import BaseModel, Field

from acuitybridge.models import Role


SANDBOX_CLINICIAN_ID = "clinician.sandbox"
"""Clinician id assigned to cases opened from the local pilot console."""


class SessionActor(BaseModel):
    """Authenticated sandbox actor."""

    actor_id: str = Field(..., description="Stable actor identifier used in the audit log.")
    role: Role = Field(..., description="RBAC role enforced for this session.")
    label: str = Field(..., description="Human-readable sandbox role label.")


class AuthenticationError(Exception):
    """Raised when a request has no recognized sandbox session."""


# Fixed local tokens. They are intentionally visible in the console UI.
_SANDBOX_SESSIONS: dict[str, SessionActor] = {
    "sandbox-admin": SessionActor(
        actor_id="admin.sandbox",
        role=Role.ADMIN,
        label="Sandbox Admin",
    ),
    "sandbox-clinician": SessionActor(
        actor_id=SANDBOX_CLINICIAN_ID,
        role=Role.CLINICIAN,
        label="Sandbox Clinician",
    ),
    "sandbox-auditor": SessionActor(
        actor_id="auditor.sandbox",
        role=Role.AUDITOR,
        label="Sandbox Auditor",
    ),
    "sandbox-participant": SessionActor(
        actor_id="participant.sandbox",
        role=Role.PARTICIPANT,
        label="Sandbox Participant",
    ),
}


def sandbox_tokens() -> dict[str, str]:
    """Return ``{label: token}`` for local console display.

    These values are sandbox role selectors, not secrets.
    """
    return {actor.label: token for token, actor in _SANDBOX_SESSIONS.items()}


def authenticate(authorization_header: str | None) -> SessionActor:
    """Resolve a sandbox bearer token to an actor.

    Args:
        authorization_header: The raw ``Authorization`` header value.

    Returns:
        A copy of the matching ``SessionActor``.

    Raises:
        AuthenticationError: If the header is missing or the token is unknown.
    """
    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise AuthenticationError(
            "A sandbox bearer token is required. Choose a role in the pilot console."
        )

    token = authorization_header.removeprefix("Bearer ").strip()
    for known, actor in _SANDBOX_SESSIONS.items():
        if len(token) == len(known) and hmac.compare_digest(token, known):
            return actor.model_copy()

    raise AuthenticationError("Unrecognized sandbox token.")
