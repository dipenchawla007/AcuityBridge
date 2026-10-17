"""
Notification delivery stubs.

Partner policies name the channels that should receive a clinician alert
(dashboard, SMS, email, webhook). This module records that a delivery
was queued. It does not call Twilio, send email, or make outbound HTTP
requests.

DISCLAIMER: A recorded notification is not a clinical alert and does not
contact any person or crisis service.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from acuitybridge.config import PartnerPolicy
from acuitybridge.escalation import EscalationCase


class NotificationReceipt(BaseModel):
    """Record of a notification that was queued and not delivered."""

    channel: str = Field(..., description="Policy channel name.")
    status: str = Field(
        default="recorded_stub",
        description="Always a stub status. Nothing is transmitted.",
    )
    detail: str = Field(..., description="What the stub recorded.")


_CHANNEL_DETAILS = {
    "dashboard": "Queued for the local pilot console case list. No external delivery.",
    "sms": "SMS stub recorded. No text message was sent.",
    "email": "Email stub recorded. No email was sent.",
    "webhook": "Webhook stub recorded. No outbound request was made.",
}


def queue_clinician_notifications(
    policy: PartnerPolicy,
    case: EscalationCase,
    clinician_id: str,
) -> list[NotificationReceipt]:
    """Record one stub receipt per channel on the partner policy.

    Args:
        policy: Partner policy that names the notification channels.
        case: Escalation case the notification refers to.
        clinician_id: Clinician the alert is assigned to.

    Returns:
        Receipts in policy channel order. The list is empty when the
        policy has no channels.
    """
    receipts: list[NotificationReceipt] = []
    for channel in policy.notification_channels:
        detail = _CHANNEL_DETAILS.get(
            channel,
            f"Channel '{channel}' recorded as a stub. Nothing was sent.",
        )
        receipts.append(
            NotificationReceipt(
                channel=channel,
                detail=(
                    f"{detail} Case {case.case_id} assigned to {clinician_id} "
                    f"for org {policy.org_id}."
                ),
            )
        )
    return receipts
