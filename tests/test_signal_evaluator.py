"""
Tests for acuitybridge.signal_evaluator -- Workflow Risk Flagging.
"""

import pytest

from acuitybridge.config import EscalationThresholds, PartnerPolicy
from acuitybridge.models import BiomarkerReading, CheckIn, RiskFlag
from acuitybridge.signal_evaluator import SignalEvaluationResult, evaluate_check_in


def _make_policy(**kwargs) -> PartnerPolicy:
    return PartnerPolicy(org_id="test_org", org_name="Test Org", **kwargs)


def _make_checkin(**kwargs) -> CheckIn:
    defaults = {"participant_id": "p1", "org_id": "test_org"}
    defaults.update(kwargs)
    return CheckIn(**defaults)


class TestSignalEvaluator:
    def test_green_flag_for_normal_values(self):
        policy = _make_policy()
        check_in = _make_checkin(mood_score=7.0, sleep_quality=8.0, distress_level=1.0)
        result = evaluate_check_in(check_in, policy)
        assert result.flag == RiskFlag.GREEN
        assert not result.requires_human_review()

    def test_yellow_flag_for_moderate_distress(self):
        policy = _make_policy()
        check_in = _make_checkin(distress_level=5.0)
        result = evaluate_check_in(check_in, policy)
        assert result.flag == RiskFlag.YELLOW
        assert result.requires_human_review()

    def test_red_flag_for_high_distress(self):
        policy = _make_policy()
        check_in = _make_checkin(distress_level=9.0)
        result = evaluate_check_in(check_in, policy)
        assert result.flag == RiskFlag.RED
        assert result.requires_human_review()

    def test_keyword_triggers_red(self):
        policy = _make_policy(escalation_keyword_overrides=["overdose"])
        check_in = _make_checkin(distress_level=1.0, keyword_flags=["overdose"])
        result = evaluate_check_in(check_in, policy)
        assert result.flag == RiskFlag.RED

    def test_low_mood_elevates_to_yellow(self):
        policy = _make_policy()
        check_in = _make_checkin(mood_score=2.0, distress_level=0.0)
        result = evaluate_check_in(check_in, policy)
        assert result.flag == RiskFlag.YELLOW

    def test_biomarker_low_hrv_elevates_flag(self):
        policy = _make_policy()
        check_in = _make_checkin(distress_level=2.0)
        biomarker = BiomarkerReading(
            participant_id="p1",
            org_id="test_org",
            metric_name="heart_rate_variability",
            value=15.0,
            unit="ms",
        )
        result = evaluate_check_in(check_in, policy, biomarker_readings=[biomarker])
        assert result.flag in (RiskFlag.ORANGE, RiskFlag.RED)
        assert result.requires_human_review()
