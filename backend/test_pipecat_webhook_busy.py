"""Regression tests for USER_BUSY hangup reconciliation.

A telephony "busy" status is ambiguous: on an in-progress callback it means
"still dialing", but on a hangup event it means the vendor's line was busy and
the call never connected (a terminal outcome). Previously the latter left the
call_attempts row stuck at status="busy" forever, which the batch runner then
reported as a bogus "timeout".
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app.services import persistence


def _apply(payload: dict) -> dict | None:
    """Run apply_pipecat_webhook_to_call_attempt and return the update doc
    passed to update_one (or None if no update was issued)."""
    captured: dict = {}

    async def _fake_update_one(filt, update):
        captured["filter"] = filt
        captured["update"] = update

    with patch.object(
        persistence.call_attempts_collection,
        "update_one",
        new=AsyncMock(side_effect=_fake_update_one),
    ):
        asyncio.run(
            persistence.apply_pipecat_webhook_to_call_attempt(
                call_id="gemini_live-test", payload=payload
            )
        )
    return captured.get("update")


def test_user_busy_hangup_marks_no_answer():
    """A hangup event with status=busy + USER_BUSY becomes terminal no_answer."""
    update = _apply(
        {
            "event": "hangup",
            "status": "busy",
            "hangup_cause": "USER_BUSY",
            "duration": 0,
        }
    )
    assert update is not None, "expected a terminal update for a USER_BUSY hangup"
    set_fields = update["$set"]
    assert set_fields["status"] == "no_answer"
    assert set_fields["completed_at"] is not None
    assert set_fields["outcome.label"] == "no_answer"
    assert set_fields["outcome.notes"] == "USER_BUSY"


def test_hangup_with_no_status_marks_no_answer():
    """A hangup event with NO status key (status normalizes to None) is also
    terminal. e.g. {event: hangup, hangup_cause: CALL_REJECTED} — the guard
    fires on the `status is None` arm and records no_answer."""
    update = _apply(
        {
            "event": "hangup",
            "hangup_cause": "CALL_REJECTED",
        }
    )
    assert update is not None, "expected a terminal update for a status-less hangup"
    set_fields = update["$set"]
    assert set_fields["status"] == "no_answer"
    assert set_fields["completed_at"] is not None
    assert set_fields["outcome.label"] == "no_answer"
    assert set_fields["outcome.notes"] == "CALL_REJECTED"


def test_in_progress_busy_does_not_flip():
    """A non-hangup busy callback (still dialing) must NOT write a terminal row."""
    update = _apply({"status": "busy"})
    assert update is None


def test_recording_ready_no_status_does_not_flip():
    """A recording-ready callback (no status, no hangup) must not flip the row."""
    update = _apply({"recording_url": "https://example/rec.mp3"})
    assert update is None


def test_completed_hangup_still_completes():
    """A normal completed callback is unaffected by the busy-hangup handling."""
    update = _apply(
        {
            "event": "hangup",
            "status": "completed",
            "transcript": "Agent: hi\nVendor: 950 rupees",
            "duration": 42,
        }
    )
    assert update is not None
    assert update["$set"]["status"] == "completed"
    assert update["$set"]["duration_seconds"] == 42
