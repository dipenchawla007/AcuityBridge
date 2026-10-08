"""
Tests for acuitybridge.transparency_report -- Decision Transparency Reports.
"""

from acuitybridge.escalation import EscalationCase
from acuitybridge.models import EscalationState, RiskFlag
from acuitybridge.transparency_report import generate_transparency_report


def _make_case(**kwargs) -> EscalationCase:
    defaults = {
        "participant_id": "p1",
        "org_id": "org_a",
        "flag_level": RiskFlag.RED,
        "triggering_indicators": ["high_distress", "keyword_match"],
    }
    defaults.update(kwargs)
    return EscalationCase(**defaults)


class TestTransparencyReport:
    def test_report_contains_required_fields(self):
        case = _make_case()
        report = generate_transparency_report(case)
        d = report.to_dict()
        assert d["case_id"] == case.case_id
        assert d["participant_id"] == "p1"
        assert d["flag_level"] == "RED"
        assert "disclaimer" in d
        assert len(d["triggering_indicators"]) == 2

    def test_report_includes_timeline(self):
        case = _make_case()
        report = generate_transparency_report(case)
        assert len(report.timeline) >= 1
        assert report.timeline[0]["state"] == "DETECTED"

    def test_report_with_custom_reasons(self):
        case = _make_case()
        reasons = ["Distress >= RED threshold.", "Keyword 'overdose' detected."]
        report = generate_transparency_report(case, evaluation_reasons=reasons)
        assert report.reasoning_chain == reasons

    def test_report_disclaimer_present(self):
        case = _make_case()
        report = generate_transparency_report(case)
        d = report.to_dict()
        assert "not constitute" in d["disclaimer"].lower()
