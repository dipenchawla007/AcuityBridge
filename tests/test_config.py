"""
Tests for acuitybridge.config -- Partner Policy Engine.

Covers: default policy validation, YAML round-trip, policy override,
invalid policy rejection, multi-org isolation, SLA boundary checks,
threshold ordering, and consent model validation.
"""

import tempfile
from pathlib import Path

import pytest
import yaml

from acuitybridge.config import (
    DEFAULT_POLICY,
    EscalationThresholds,
    PartnerPolicy,
    PolicyRegistry,
    load_policies_from_yaml,
)
from acuitybridge.models import CrisisResourceTarget, RiskFlag


# ---------------------------------------------------------------------------
# 1. Default policy validation
# ---------------------------------------------------------------------------

class TestDefaultPolicy:
    def test_default_policy_has_conservative_thresholds(self):
        """DEFAULT_POLICY uses conservative values suitable as a safe fallback."""
        assert DEFAULT_POLICY.org_id == "default"
        assert DEFAULT_POLICY.consent_model == "opt_in"
        assert DEFAULT_POLICY.clinician_ack_sla_seconds == 300
        assert DEFAULT_POLICY.data_retention_days == 90

    def test_default_policy_requires_human_review_for_elevated_flags(self):
        """YELLOW, ORANGE, RED require human review; GREEN does not."""
        assert RiskFlag.GREEN not in DEFAULT_POLICY.human_review_required_flags
        assert RiskFlag.YELLOW in DEFAULT_POLICY.human_review_required_flags
        assert RiskFlag.ORANGE in DEFAULT_POLICY.human_review_required_flags
        assert RiskFlag.RED in DEFAULT_POLICY.human_review_required_flags


# ---------------------------------------------------------------------------
# 2. Partner policy validation
# ---------------------------------------------------------------------------

class TestPartnerPolicyValidation:
    def test_valid_policy_creation(self):
        policy = PartnerPolicy(
            org_id="test_org",
            org_name="Test Organization",
            clinician_ack_sla_seconds=600,
        )
        assert policy.org_id == "test_org"
        assert policy.clinician_ack_sla_seconds == 600

    def test_empty_org_id_rejected(self):
        with pytest.raises(Exception):
            PartnerPolicy(org_id="", org_name="Bad Org")

    def test_sla_must_be_positive(self):
        with pytest.raises(Exception):
            PartnerPolicy(
                org_id="bad_sla",
                org_name="Bad SLA Org",
                clinician_ack_sla_seconds=0,
            )

    def test_retention_days_minimum_enforced(self):
        with pytest.raises(Exception):
            PartnerPolicy(
                org_id="short_retention",
                org_name="Short Retention Org",
                data_retention_days=10,
            )

    def test_invalid_consent_model_rejected(self):
        with pytest.raises(Exception):
            PartnerPolicy(
                org_id="bad_consent",
                org_name="Bad Consent Org",
                consent_model="maybe",
            )


# ---------------------------------------------------------------------------
# 3. Escalation threshold ordering
# ---------------------------------------------------------------------------

class TestEscalationThresholds:
    def test_valid_thresholds(self):
        t = EscalationThresholds(
            yellow_min_distress=3.0,
            orange_min_distress=5.0,
            red_min_distress=7.0,
        )
        assert t.yellow_min_distress < t.orange_min_distress < t.red_min_distress

    def test_orange_below_yellow_rejected(self):
        with pytest.raises(Exception):
            EscalationThresholds(
                yellow_min_distress=5.0,
                orange_min_distress=3.0,
                red_min_distress=7.0,
            )

    def test_red_below_orange_rejected(self):
        with pytest.raises(Exception):
            EscalationThresholds(
                yellow_min_distress=3.0,
                orange_min_distress=5.0,
                red_min_distress=4.0,
            )


# ---------------------------------------------------------------------------
# 4. Policy registry -- multi-org isolation
# ---------------------------------------------------------------------------

class TestPolicyRegistry:
    def test_register_and_retrieve(self):
        registry = PolicyRegistry()
        policy = PartnerPolicy(org_id="org_a", org_name="Org A")
        registry.register(policy)
        retrieved = registry.get("org_a")
        assert retrieved.org_id == "org_a"
        assert retrieved.org_name == "Org A"

    def test_duplicate_registration_rejected(self):
        registry = PolicyRegistry()
        policy = PartnerPolicy(org_id="org_a", org_name="Org A")
        registry.register(policy)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(policy)

    def test_get_nonexistent_org_raises_key_error(self):
        registry = PolicyRegistry()
        with pytest.raises(KeyError):
            registry.get("nonexistent")

    def test_multi_org_isolation(self):
        """Org A's policy cannot be retrieved using org B's identifier."""
        registry = PolicyRegistry()
        policy_a = PartnerPolicy(
            org_id="org_a", org_name="Org A", clinician_ack_sla_seconds=300
        )
        policy_b = PartnerPolicy(
            org_id="org_b", org_name="Org B", clinician_ack_sla_seconds=900
        )
        registry.register(policy_a)
        registry.register(policy_b)

        retrieved_a = registry.get("org_a")
        retrieved_b = registry.get("org_b")

        assert retrieved_a.clinician_ack_sla_seconds == 300
        assert retrieved_b.clinician_ack_sla_seconds == 900
        assert retrieved_a.org_id != retrieved_b.org_id

    def test_update_existing_policy(self):
        registry = PolicyRegistry()
        policy = PartnerPolicy(org_id="org_a", org_name="Org A", data_retention_days=90)
        registry.register(policy)

        updated = PartnerPolicy(org_id="org_a", org_name="Org A Updated", data_retention_days=180)
        registry.update(updated)

        retrieved = registry.get("org_a")
        assert retrieved.org_name == "Org A Updated"
        assert retrieved.data_retention_days == 180

    def test_update_nonexistent_raises_key_error(self):
        registry = PolicyRegistry()
        policy = PartnerPolicy(org_id="ghost", org_name="Ghost Org")
        with pytest.raises(KeyError):
            registry.update(policy)

    def test_list_orgs_sorted(self):
        registry = PolicyRegistry()
        for oid in ["charlie", "alpha", "bravo"]:
            registry.register(PartnerPolicy(org_id=oid, org_name=oid.title()))
        assert registry.list_orgs() == ["alpha", "bravo", "charlie"]

    def test_registry_returns_deep_copies(self):
        """Mutations to retrieved policies must not affect the registry."""
        registry = PolicyRegistry()
        policy = PartnerPolicy(org_id="org_a", org_name="Org A")
        registry.register(policy)

        retrieved = registry.get("org_a")
        retrieved.org_name = "MUTATED"

        original = registry.get("org_a")
        assert original.org_name == "Org A"


# ---------------------------------------------------------------------------
# 5. YAML round-trip
# ---------------------------------------------------------------------------

class TestYAMLLoader:
    def _write_yaml(self, policies_data: list[dict], tmp_dir: Path) -> Path:
        path = tmp_dir / "policies.yaml"
        with open(path, "w") as f:
            yaml.dump({"policies": policies_data}, f)
        return path

    def test_load_valid_yaml(self, tmp_path):
        data = [
            {
                "org_id": "yaml_org",
                "org_name": "YAML Test Org",
                "clinician_ack_sla_seconds": 120,
            }
        ]
        path = self._write_yaml(data, tmp_path)
        policies = load_policies_from_yaml(path)
        assert len(policies) == 1
        assert policies[0].org_id == "yaml_org"

    def test_load_multiple_policies(self, tmp_path):
        data = [
            {"org_id": f"org_{i}", "org_name": f"Org {i}"}
            for i in range(3)
        ]
        path = self._write_yaml(data, tmp_path)
        policies = load_policies_from_yaml(path)
        assert len(policies) == 3

    def test_load_nonexistent_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_policies_from_yaml("/nonexistent/path.yaml")

    def test_load_invalid_structure_raises(self, tmp_path):
        path = tmp_path / "bad.yaml"
        with open(path, "w") as f:
            yaml.dump({"not_policies": []}, f)
        with pytest.raises(ValueError, match="top-level 'policies' key"):
            load_policies_from_yaml(path)

    def test_load_sample_partner_policies(self):
        """Validate that the bundled example file loads successfully."""
        sample_path = Path(__file__).parent.parent / "examples" / "partner_policies.yaml"
        if sample_path.exists():
            policies = load_policies_from_yaml(sample_path)
            assert len(policies) >= 2
            org_ids = [p.org_id for p in policies]
            assert "clinic_alpha" in org_ids
            assert "va_bravo" in org_ids
