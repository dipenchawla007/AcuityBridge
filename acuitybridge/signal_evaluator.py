"""
Signal Evaluator -- Workflow Risk Flagging for Human Review.

Produces decision-support workflow flags (GREEN, YELLOW, ORANGE, RED) based
on participant check-in data and optional biomarker readings, evaluated
against partner-defined policy thresholds.

**This is not a clinical assessment tool.**  Outputs are policy-driven
routing signals for human review.  They do not constitute diagnoses,
risk stratification scores, or clinical recommendations.

**Human review model:**

* GREEN  -- human review optional.
* YELLOW -- human review **required** before any action.
* ORANGE -- human review **required**; automated interaction restricted.
* RED    -- human review **required**; escalation and crisis interface
  protocols activated per partner policy.

DISCLAIMER: This module does not perform clinical assessment, diagnosis,
or treatment.  All elevated flags require review by a licensed clinician
or authorized human reviewer before any action is taken.
"""

from __future__ import annotations

from acuitybridge.config import PartnerPolicy
from acuitybridge.models import BiomarkerReading, CheckIn, RiskFlag


class SignalEvaluationResult:
    """Result of evaluating a participant's signals against policy thresholds.

    Contains the computed workflow flag and a human-readable list of reasons
    explaining why the flag was raised.
    """

    def __init__(self, flag: RiskFlag, reasons: list[str]) -> None:
        self.flag = flag
        self.reasons = reasons

    def requires_human_review(self) -> bool:
        """Whether this flag level requires mandatory human review."""
        return self.flag in (RiskFlag.YELLOW, RiskFlag.ORANGE, RiskFlag.RED)

    def __repr__(self) -> str:
        return f"SignalEvaluationResult(flag={self.flag.value}, reasons={self.reasons})"


def evaluate_check_in(
    check_in: CheckIn,
    policy: PartnerPolicy,
    biomarker_readings: list[BiomarkerReading] | None = None,
) -> SignalEvaluationResult:
    """Evaluate a participant check-in against policy-defined thresholds.

    The evaluator starts at GREEN and escalates based on threshold breaches.
    Multiple signals can compound to raise the flag further.

    Args:
        check_in: The participant's self-report check-in.
        policy: The partner's workflow policy (defines thresholds).
        biomarker_readings: Optional supplementary wearable data.

    Returns:
        A ``SignalEvaluationResult`` with the computed flag and reasons.
    """
    thresholds = policy.escalation_thresholds
    flag = RiskFlag.GREEN
    reasons: list[str] = []

    # --- Check distress level ---
    if check_in.distress_level is not None:
        if check_in.distress_level >= thresholds.red_min_distress:
            flag = RiskFlag.RED
            reasons.append(
                f"Distress level ({check_in.distress_level}) >= RED threshold "
                f"({thresholds.red_min_distress}). Human review required."
            )
        elif check_in.distress_level >= thresholds.orange_min_distress:
            flag = _max_flag(flag, RiskFlag.ORANGE)
            reasons.append(
                f"Distress level ({check_in.distress_level}) >= ORANGE threshold "
                f"({thresholds.orange_min_distress}). Human review required."
            )
        elif check_in.distress_level >= thresholds.yellow_min_distress:
            flag = _max_flag(flag, RiskFlag.YELLOW)
            reasons.append(
                f"Distress level ({check_in.distress_level}) >= YELLOW threshold "
                f"({thresholds.yellow_min_distress}). Human review required."
            )

    # --- Check mood score ---
    if check_in.mood_score is not None:
        if check_in.mood_score <= thresholds.low_mood_threshold:
            flag = _max_flag(flag, RiskFlag.YELLOW)
            reasons.append(
                f"Mood score ({check_in.mood_score}) <= low mood threshold "
                f"({thresholds.low_mood_threshold}). Elevated for human review."
            )

    # --- Check sleep quality ---
    if check_in.sleep_quality is not None:
        if check_in.sleep_quality <= thresholds.low_sleep_threshold:
            flag = _max_flag(flag, RiskFlag.YELLOW)
            reasons.append(
                f"Sleep quality ({check_in.sleep_quality}) <= low sleep threshold "
                f"({thresholds.low_sleep_threshold}). Elevated for human review."
            )

    # --- Check keyword flags ---
    if check_in.keyword_flags:
        all_keywords = set(policy.escalation_keyword_overrides)
        matched = [kw for kw in check_in.keyword_flags if kw in all_keywords]
        if matched:
            flag = RiskFlag.RED
            reasons.append(
                f"Escalation keywords detected: {matched}. "
                "Immediate human review required per partner policy."
            )

    # --- Check biomarker readings (supplementary signals) ---
    if biomarker_readings:
        for reading in biomarker_readings:
            if reading.metric_name == "heart_rate_variability" and reading.value < 20:
                flag = _max_flag(flag, RiskFlag.ORANGE)
                reasons.append(
                    f"Low HRV reading ({reading.value} {reading.unit}). "
                    "Supplementary signal elevated for human review."
                )
            if reading.metric_name == "sleep_hours" and reading.value < 3:
                flag = _max_flag(flag, RiskFlag.ORANGE)
                reasons.append(
                    f"Very low sleep ({reading.value} hours). "
                    "Supplementary signal elevated for human review."
                )

    if not reasons:
        reasons.append("All signals within GREEN thresholds. Human review optional.")

    return SignalEvaluationResult(flag=flag, reasons=reasons)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FLAG_ORDER = {RiskFlag.GREEN: 0, RiskFlag.YELLOW: 1, RiskFlag.ORANGE: 2, RiskFlag.RED: 3}


def _max_flag(a: RiskFlag, b: RiskFlag) -> RiskFlag:
    """Return the higher (more severe) of two flags."""
    return a if _FLAG_ORDER[a] >= _FLAG_ORDER[b] else b
