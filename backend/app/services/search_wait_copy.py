"""Customer-facing wait copy shared by web chat and WhatsApp."""

from __future__ import annotations

# Shown once when a supplier search starts (after Noted / search launch).
SEARCH_WAIT_START = (
    "I’m contacting suppliers in real time for the best prices. "
    "This usually takes a few minutes and won’t take more than about 5 — hang tight."
)

# Shown if the search is still running with a quiet gap (no new updates).
SEARCH_WAIT_NUDGE = (
    "Still contacting suppliers in real time — please hold on a bit longer. "
    "This typically finishes within a few minutes."
)

# WhatsApp timeout when the poll window expires.
SEARCH_WAIT_TIMEOUT = (
    "This search is taking longer than usual. I’m still contacting suppliers — "
    "please check back in a few minutes if you need updated quotes."
)
