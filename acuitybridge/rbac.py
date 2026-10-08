"""
Role-Based Access Control (RBAC) for AcuityBridge.

Defines roles and permission checks used by the escalation and audit
modules to gate operations.  This is a lightweight, in-process RBAC
model suitable for demonstration and pilot deployments.

**Roles:**

* PARTICIPANT -- end-user of the workflow.
* CLINICIAN   -- licensed professional with escalation authority.
* ADMIN       -- system administrator with policy management rights.
* AUDITOR     -- read-only access to audit logs and exports.

DISCLAIMER: This RBAC model is a decision-support workflow access control
layer.  Production deployments should integrate with enterprise identity
providers (e.g., OAuth2/OIDC, SAML) and enforce additional security controls.
"""

from __future__ import annotations

from acuitybridge.models import Role


# ---------------------------------------------------------------------------
# Permission definitions
# ---------------------------------------------------------------------------

# Maps (role, action) -> allowed
_PERMISSIONS: dict[tuple[Role, str], bool] = {
    # Participant permissions
    (Role.PARTICIPANT, "submit_check_in"): True,
    (Role.PARTICIPANT, "view_own_data"): True,
    (Role.PARTICIPANT, "view_escalation"): False,
    (Role.PARTICIPANT, "acknowledge_escalation"): False,
    (Role.PARTICIPANT, "resolve_escalation"): False,
    (Role.PARTICIPANT, "manage_policy"): False,
    (Role.PARTICIPANT, "export_audit"): False,
    (Role.PARTICIPANT, "query_audit"): False,
    # Clinician permissions
    (Role.CLINICIAN, "submit_check_in"): False,
    (Role.CLINICIAN, "view_own_data"): False,
    (Role.CLINICIAN, "view_escalation"): True,
    (Role.CLINICIAN, "acknowledge_escalation"): True,
    (Role.CLINICIAN, "resolve_escalation"): True,
    (Role.CLINICIAN, "manage_policy"): False,
    (Role.CLINICIAN, "export_audit"): False,
    (Role.CLINICIAN, "query_audit"): True,
    # Admin permissions
    (Role.ADMIN, "submit_check_in"): False,
    (Role.ADMIN, "view_own_data"): False,
    (Role.ADMIN, "view_escalation"): True,
    (Role.ADMIN, "acknowledge_escalation"): False,
    (Role.ADMIN, "resolve_escalation"): False,
    (Role.ADMIN, "manage_policy"): True,
    (Role.ADMIN, "export_audit"): True,
    (Role.ADMIN, "query_audit"): True,
    # Auditor permissions
    (Role.AUDITOR, "submit_check_in"): False,
    (Role.AUDITOR, "view_own_data"): False,
    (Role.AUDITOR, "view_escalation"): True,
    (Role.AUDITOR, "acknowledge_escalation"): False,
    (Role.AUDITOR, "resolve_escalation"): False,
    (Role.AUDITOR, "manage_policy"): False,
    (Role.AUDITOR, "export_audit"): True,
    (Role.AUDITOR, "query_audit"): True,
}


def check_permission(role: Role, action: str) -> bool:
    """Check whether a role has permission to perform an action.

    Args:
        role: The actor's role.
        action: The action to check (e.g., 'acknowledge_escalation').

    Returns:
        True if the role is permitted to perform the action, False otherwise.
    """
    return _PERMISSIONS.get((role, action), False)


def require_permission(role: Role, action: str) -> None:
    """Enforce a permission check; raise if denied.

    Args:
        role: The actor's role.
        action: The action to check.

    Raises:
        PermissionError: If the role is not permitted.
    """
    if not check_permission(role, action):
        raise PermissionError(
            f"Role '{role.value}' is not permitted to perform action '{action}'."
        )


def get_permissions_for_role(role: Role) -> dict[str, bool]:
    """Return all permissions for a given role.

    Args:
        role: The role to query.

    Returns:
        Dictionary mapping action names to permission booleans.
    """
    return {
        action: allowed
        for (r, action), allowed in _PERMISSIONS.items()
        if r == role
    }
