"""
Tests for acuitybridge.rbac -- Role-Based Access Control.
"""

import pytest

from acuitybridge.models import Role
from acuitybridge.rbac import check_permission, get_permissions_for_role, require_permission


class TestRBAC:
    def test_clinician_can_acknowledge_escalation(self):
        assert check_permission(Role.CLINICIAN, "acknowledge_escalation") is True

    def test_participant_cannot_acknowledge_escalation(self):
        assert check_permission(Role.PARTICIPANT, "acknowledge_escalation") is False

    def test_auditor_can_export_audit(self):
        assert check_permission(Role.AUDITOR, "export_audit") is True

    def test_clinician_cannot_manage_policy(self):
        assert check_permission(Role.CLINICIAN, "manage_policy") is False

    def test_admin_can_manage_policy(self):
        assert check_permission(Role.ADMIN, "manage_policy") is True

    def test_require_permission_raises_on_denied(self):
        with pytest.raises(PermissionError):
            require_permission(Role.PARTICIPANT, "resolve_escalation")

    def test_require_permission_passes_on_allowed(self):
        require_permission(Role.CLINICIAN, "resolve_escalation")  # should not raise

    def test_get_permissions_returns_all_actions(self):
        perms = get_permissions_for_role(Role.AUDITOR)
        assert "export_audit" in perms
        assert "query_audit" in perms
        assert perms["export_audit"] is True
        assert perms.get("manage_policy") is False
