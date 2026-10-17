"""
Wearable ingestion stubs for the local sandbox.

Apple HealthKit and Fitbit each use their own sample names. This module
normalizes those names into the metrics the signal evaluator already
understands. It does not call Apple, Fitbit, or any other network service.

Accepted samples are synthetic workflow inputs. They are not clinical
measurements and they do not constitute medical monitoring.

DISCLAIMER: A normalized reading can raise a workflow flag for human
review. It does not diagnose, treat, or contact a device vendor.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


WearableSource = Literal["apple_healthkit", "fitbit"]

# Vendor sample name -> (canonical metric, canonical unit).
_APPLE_TYPES = {
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": ("heart_rate_variability", "ms"),
    "HKCategoryTypeIdentifierSleepAnalysis": ("sleep_hours", "hours"),
    "HKQuantityTypeIdentifierStepCount": ("step_count", "steps"),
    "HKQuantityTypeIdentifierRestingHeartRate": ("resting_heart_rate", "count/min"),
    "heart_rate_variability": ("heart_rate_variability", "ms"),
    "sleep_hours": ("sleep_hours", "hours"),
    "step_count": ("step_count", "steps"),
    "resting_heart_rate": ("resting_heart_rate", "count/min"),
}

_FITBIT_TYPES = {
    "hrv": ("heart_rate_variability", "ms"),
    "sleep": ("sleep_hours", "hours"),
    "steps": ("step_count", "steps"),
    "restingHeartRate": ("resting_heart_rate", "count/min"),
    "heart_rate_variability": ("heart_rate_variability", "ms"),
    "sleep_hours": ("sleep_hours", "hours"),
    "step_count": ("step_count", "steps"),
    "resting_heart_rate": ("resting_heart_rate", "count/min"),
}

_SOURCE_TYPES = {
    "apple_healthkit": _APPLE_TYPES,
    "fitbit": _FITBIT_TYPES,
}

_BOUNDS = {
    "heart_rate_variability": (0.0, 500.0),
    "sleep_hours": (0.0, 24.0),
    "step_count": (0.0, 200_000.0),
    "resting_heart_rate": (0.0, 250.0),
}


class WearableSample(BaseModel):
    """One sample inside a vendor-shaped stub payload."""

    type: str = Field(..., min_length=1, max_length=80)
    value: float
    unit: str = Field(default="", max_length=40)


class WearableIngest(BaseModel):
    """A synthetic wearable payload submitted to the sandbox."""

    source: WearableSource
    org_id: str = Field(..., min_length=1, max_length=80)
    participant_id: str | None = Field(default=None, max_length=80)
    display_name: str = Field(default="Synthetic participant", max_length=120)
    samples: list[WearableSample] = Field(..., min_length=1, max_length=20)


class NormalizedMetric(BaseModel):
    """A vendor sample after it has been mapped to a canonical metric."""

    metric_name: str
    value: float
    unit: str
    vendor_type: str


def supported_types(source: WearableSource) -> list[str]:
    """Return the vendor sample names accepted for a source."""
    return sorted(_SOURCE_TYPES[source])


def normalize_samples(source: WearableSource, samples: list[WearableSample]) -> list[NormalizedMetric]:
    """Map vendor sample names onto canonical metrics.

    Args:
        source: ``apple_healthkit`` or ``fitbit``.
        samples: Vendor-shaped samples from a synthetic payload.

    Returns:
        Normalized metrics in input order.

    Raises:
        ValueError: If a sample type is unknown or a value is outside the
            accepted numeric range.
    """
    catalog = _SOURCE_TYPES[source]
    normalized: list[NormalizedMetric] = []
    for sample in samples:
        mapped = catalog.get(sample.type)
        if mapped is None:
            known = ", ".join(sorted(catalog))
            raise ValueError(
                f"Unsupported {source} sample type '{sample.type}'. "
                f"Accepted types: {known}."
            )
        metric_name, unit = mapped
        low, high = _BOUNDS[metric_name]
        if sample.value < low or sample.value > high:
            raise ValueError(
                f"{metric_name} value {sample.value} is outside {low}..{high}."
            )
        normalized.append(
            NormalizedMetric(
                metric_name=metric_name,
                value=sample.value,
                unit=unit,
                vendor_type=sample.type,
            )
        )
    return normalized
