"""Tests for wearable stub normalization and sandbox ingest."""

import pytest

from acuitybridge.console import ApiError, PilotApp
from acuitybridge.sessions import authenticate
from acuitybridge.wearables import WearableSample, normalize_samples


ADMIN = authenticate("Bearer sandbox-admin")
PARTICIPANT = authenticate("Bearer sandbox-participant")
CLINICIAN = authenticate("Bearer sandbox-clinician")


def test_healthkit_and_fitbit_names_normalize():
    apple = normalize_samples(
        "apple_healthkit",
        [
            WearableSample(type="HKQuantityTypeIdentifierHeartRateVariabilitySDNN", value=16),
            WearableSample(type="HKCategoryTypeIdentifierSleepAnalysis", value=2.4, unit="hr"),
        ],
    )
    assert [(item.metric_name, item.unit, item.value) for item in apple] == [
        ("heart_rate_variability", "ms", 16),
        ("sleep_hours", "hours", 2.4),
    ]

    fitbit = normalize_samples(
        "fitbit",
        [WearableSample(type="sleep", value=7.2), WearableSample(type="steps", value=8400)],
    )
    assert [item.metric_name for item in fitbit] == ["sleep_hours", "step_count"]


def test_unknown_vendor_type_is_rejected():
    with pytest.raises(ValueError, match="Unsupported fitbit sample type"):
        normalize_samples("fitbit", [WearableSample(type="bloodGlucose", value=90)])


def test_low_hrv_opens_a_case_and_survives_restart(tmp_path):
    db = tmp_path / "pilot.sqlite"
    app = PilotApp(db)
    app.create_policy(ADMIN, {"org_id": "clinic_test", "org_name": "Test Clinic"})
    with pytest.raises(PermissionError):
        app.ingest_wearable(
            CLINICIAN,
            {
                "source": "apple_healthkit",
                "org_id": "clinic_test",
                "samples": [
                    {"type": "HKQuantityTypeIdentifierHeartRateVariabilitySDNN", "value": 16}
                ],
            },
        )

    result = app.ingest_wearable(
        PARTICIPANT,
        {
            "source": "apple_healthkit",
            "org_id": "clinic_test",
            "display_name": "Synthetic wearable participant",
            "samples": [
                {"type": "HKQuantityTypeIdentifierHeartRateVariabilitySDNN", "value": 16},
                {"type": "HKCategoryTypeIdentifierSleepAnalysis", "value": 2.4},
            ],
        },
    )
    assert result["evaluation"]["flag"] == "ORANGE"
    assert result["evaluation"]["requires_human_review"] is True
    assert result["case"]["state"] == "CLINICIAN_NOTIFIED"
    assert "No Apple or Fitbit API was called" in result["disclaimer"]
    assert {item["metric_name"] for item in result["readings"]} == {
        "heart_rate_variability",
        "sleep_hours",
    }
    app.close()

    reopened = PilotApp(db)
    listed = reopened.list_wearables(CLINICIAN, "clinic_test")
    assert {item["metric_name"] for item in listed["readings"]} == {
        "heart_rate_variability",
        "sleep_hours",
    }
    assert reopened.audit.verify_chain()[0] is True
    reopened.close()


def test_steady_fitbit_sample_stays_green(tmp_path):
    app = PilotApp(tmp_path / "pilot.sqlite")
    app.create_policy(ADMIN, {"org_id": "clinic_test", "org_name": "Test Clinic"})
    result = app.ingest_wearable(
        PARTICIPANT,
        {
            "source": "fitbit",
            "org_id": "clinic_test",
            "samples": [
                {"type": "hrv", "value": 48},
                {"type": "sleep", "value": 7.2},
                {"type": "steps", "value": 8400},
            ],
        },
    )
    assert result["evaluation"]["flag"] == "GREEN"
    assert result["case"] is None
    with pytest.raises(ApiError) as caught:
        app.ingest_wearable(
            PARTICIPANT,
            {
                "source": "fitbit",
                "org_id": "clinic_test",
                "samples": [{"type": "bloodGlucose", "value": 90}],
            },
        )
    assert caught.value.status == 400
    app.close()
