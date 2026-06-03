"""Brand-voice response templates for the CS agent.

All replies sign off with the brand signature, never AJ's name
(per [[feedback_anonymous_to_customers]]).

These are not used directly when the agent goes full-Claude; they're
the fallback for cases where Claude isn't called (errors, deterministic
flows) and the seed examples for the Claude prompt.
"""
from __future__ import annotations

try:
    from .policy import BRAND_SIGNATURE
except ImportError:
    from policy import BRAND_SIGNATURE  # type: ignore[no-redef]


def _sign(body: str, first_name: str | None = None) -> str:
    greet = f"Hi {first_name}," if first_name else "Hi there,"
    return f"{greet}\n\n{body}\n\n{BRAND_SIGNATURE}"


def where_is_my_order(first_name: str | None, order_name: str,
                      tracking_url: str | None, tracking_number: str | None,
                      fulfillment_status: str | None) -> str:
    if tracking_url:
        body = (
            f"Thanks for checking in on order {order_name}.\n\n"
            f"It's on the way — here's your tracking link: {tracking_url}\n"
            f"(Tracking number: {tracking_number})\n\n"
            "If the carrier hasn't shown an update in a few days, it's usually "
            "moving but not scanned at every facility. Reply to this email if "
            "you see no movement by the expected delivery date and we'll dig in."
        )
    elif fulfillment_status == "fulfilled":
        body = (
            f"Order {order_name} shows as fulfilled but I don't have a clean "
            "tracking link on file. Let me pull it from the carrier and follow "
            "up shortly — replying to this email reaches the same inbox."
        )
    else:
        body = (
            f"Order {order_name} hasn't shipped from our warehouse yet. It's "
            "in the queue — typical processing is 1-2 business days. You'll get "
            "an automated email with tracking the moment it leaves the building."
        )
    return _sign(body, first_name)


def return_or_exchange(first_name: str | None) -> str:
    body = (
        "Happy to help with a return or exchange. Start the request at our "
        "self-service portal here: https://elmandrye.com/a/return\n\n"
        "Once you submit, you'll get a prepaid label and instructions right "
        "to your email. Any questions, just reply here."
    )
    return _sign(body, first_name)


def order_cancel_pre_ship(first_name: str | None, order_name: str) -> str:
    body = (
        f"Order {order_name} has been cancelled and refunded in full. "
        "You should see the refund post back to your original payment method "
        "within 5-10 business days, depending on your bank.\n\n"
        "If you change your mind, just place a new order — we're here whenever."
    )
    return _sign(body, first_name)


def order_cancel_post_ship(first_name: str | None, order_name: str) -> str:
    body = (
        f"Order {order_name} has already shipped, so I can't cancel it. The "
        "fastest path is to refuse delivery (or accept it and start a return) "
        "using our portal: https://elmandrye.com/a/return\n\n"
        "Once the package is back en route to us, we'll process the refund "
        "automatically."
    )
    return _sign(body, first_name)


def subscription_cancel(first_name: str | None) -> str:
    body = (
        "You can cancel, skip, or change frequency on your subscription "
        "directly from your account at https://elmandrye.com/account — "
        "click \"Manage Subscriptions\" near the top.\n\n"
        "If you want me to do it for you, just reply with which subscription "
        "and what you'd like to change."
    )
    return _sign(body, first_name)


def need_more_info(first_name: str | None, what: str) -> str:
    body = (
        f"Thanks for reaching out. To help fastest, I need a bit more info: "
        f"{what}\n\nReply to this email with that and we'll take it from there."
    )
    return _sign(body, first_name)


def damaged_or_missing_acknowledgment(first_name: str | None) -> str:
    """Sent immediately on a damaged/missing-item ticket while we kick the
    warehouse/supplier handoff in the background."""
    body = (
        "Sorry about that — we'll make it right. I'm pulling the order details "
        "and looping in our warehouse to investigate today. You'll hear from us "
        "with a resolution (replacement, refund, or both) within one business "
        "day.\n\n"
        "If you have a photo of the damaged/missing item handy, replying with "
        "it speeds the process up a lot. Not required, but helps."
    )
    return _sign(body, first_name)
