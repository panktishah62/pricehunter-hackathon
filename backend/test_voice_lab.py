"""Tests for the Voice Lab provider options.

Covers the additive `gemini_live` provider entry introduced by the
gemini-live-call-provider feature: its enabled-flag truth table and its
contract, plus a guard that the five pre-existing entries are unchanged.

Run from the backend directory:  python -m pytest test_voice_lab.py -q
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings as hyp_settings, strategies as st

from app.config import settings
from app.services.voice_lab import provider_options

# The five providers that existed before gemini_live was added, in order.
EXISTING_PROVIDER_IDS = [
    "plivo_bridge",
    "pipecat",
    "bolna",
    "elevenlabs_plivo",
    "exotel",
]


def _gemini_entry(options: list[dict]) -> dict | None:
    return next((opt for opt in options if opt["id"] == "gemini_live"), None)


@pytest.fixture
def restore_settings():
    """Snapshot and restore the settings fields the truth table mutates."""
    saved = {
        "mock_voice_calls": settings.mock_voice_calls,
        "pipecat_agent_base_url": settings.pipecat_agent_base_url,
        "gemini_live_enabled": settings.gemini_live_enabled,
    }
    try:
        yield
    finally:
        for key, value in saved.items():
            setattr(settings, key, value)


# Feature: gemini-live-call-provider, Property 1
@hyp_settings(max_examples=25)
@given(
    mock=st.booleans(),
    base_url_present=st.booleans(),
    gemini_enabled=st.booleans(),
)
def test_gemini_live_enabled_truth_table(mock, base_url_present, gemini_enabled):
    """`gemini_live` enabled == mock OR (base_url AND gemini_enabled).

    Validates: Requirements 1.2, 1.3, 1.4
    """
    saved = {
        "mock_voice_calls": settings.mock_voice_calls,
        "pipecat_agent_base_url": settings.pipecat_agent_base_url,
        "gemini_live_enabled": settings.gemini_live_enabled,
    }
    try:
        settings.mock_voice_calls = mock
        settings.pipecat_agent_base_url = "https://agent.example.com" if base_url_present else ""
        settings.gemini_live_enabled = gemini_enabled

        entry = _gemini_entry(provider_options())
        assert entry is not None

        expected = mock or (base_url_present and gemini_enabled)
        assert entry["enabled"] is expected
    finally:
        for key, value in saved.items():
            setattr(settings, key, value)


def test_gemini_live_entry_contract(restore_settings):
    """The gemini_live entry exists with the right id/label/detail contract.

    Validates: Requirements 1.1, 1.6
    """
    settings.mock_voice_calls = True  # ensure a deterministic enabled value
    settings.gemini_live_enabled = True

    options = provider_options()
    entry = _gemini_entry(options)

    assert entry is not None, "gemini_live provider option must be present"
    assert entry["id"] == "gemini_live"
    assert "Gemini Agent" in entry["label"]
    assert isinstance(entry["label"], str) and entry["label"].strip()
    assert isinstance(entry.get("detail"), str) and entry["detail"].strip()
    assert "enabled" in entry


def test_existing_provider_entries_unchanged(restore_settings):
    """The five pre-existing entries keep their ids, order, and semantics.

    Validates: Requirements 1.5
    """
    options = provider_options()
    ids = [opt["id"] for opt in options]

    # The five existing entries appear first, in their original order.
    assert ids[:5] == EXISTING_PROVIDER_IDS
    # gemini_live is the only addition.
    assert set(ids) == set(EXISTING_PROVIDER_IDS) | {"gemini_live"}
    assert len(options) == 6

    by_id = {opt["id"]: opt for opt in options}

    # Spot-check that the existing entries retained their labels/contract.
    assert by_id["plivo_bridge"]["label"] == "Plivo + ElevenLabs bridge"
    assert by_id["pipecat"]["label"] == "Pipecat agent"
    assert by_id["bolna"]["label"] == "Bolna"
    assert by_id["elevenlabs_plivo"]["label"] == "ElevenLabs SIP"
    assert by_id["exotel"]["label"] == "Exotel direct"
    # exotel remains hard-disabled (adapter not wired).
    assert by_id["exotel"]["enabled"] is False


# ---------------------------------------------------------------------------
# Bulk parallel calling — concurrency clamp
# ---------------------------------------------------------------------------
import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import voice_lab as voice_lab_module


class _FakeVendor:
    def __init__(self, vid: str) -> None:
        self.phone = "+910000000000"
        self._id = vid


def _make_fake_session(n_vendors: int):
    return SimpleNamespace(
        search_id="s1",
        query=SimpleNamespace(product="widget"),
        benchmark_price=None,
        vendors=[_FakeVendor(f"v{i}") for i in range(n_vendors)],
    )


@contextlib.contextmanager
def _snapshot_bulk_settings():
    saved = {
        "voice_lab_bulk_max_concurrency": settings.voice_lab_bulk_max_concurrency,
        "pipecat_call_spacing_seconds": settings.pipecat_call_spacing_seconds,
    }
    try:
        yield
    finally:
        for key, value in saved.items():
            setattr(settings, key, value)


def test_bulk_concurrency_is_clamped_to_hard_cap():
    """A requested concurrency above the hard cap is clamped; peak in-flight
    dials never exceed settings.voice_lab_bulk_max_concurrency."""
    with _snapshot_bulk_settings():
        settings.voice_lab_bulk_max_concurrency = 3
        settings.pipecat_call_spacing_seconds = 0.0  # no stagger sleep in the test

        state = {"in_flight": 0, "peak": 0}

        async def _fake_call(*args, **kwargs):
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])
            await asyncio.sleep(0.02)  # hold the slot so overlap is observable
            state["in_flight"] -= 1
            return SimpleNamespace(call_id=f"call-{state['peak']}-{id(object())}")

        session = _make_fake_session(8)

        with patch.object(voice_lab_module, "ensure_session", new=AsyncMock(return_value=session)), \
             patch.object(voice_lab_module, "_provider_enabled", return_value=True), \
             patch.object(voice_lab_module, "_find_vendor", side_effect=lambda s, vid: next((v for v in s.vendors if v._id == vid), None)), \
             patch.object(voice_lab_module, "_vendor_key", side_effect=lambda v: v._id), \
             patch.object(voice_lab_module, "call_vendor_with_provider", new=AsyncMock(side_effect=_fake_call)), \
             patch.object(voice_lab_module.persistence, "record_call_attempt", new=AsyncMock()), \
             patch.object(voice_lab_module, "_complete_call_attempt", new=AsyncMock()), \
             patch.object(voice_lab_module, "session_payload", new=AsyncMock(return_value={})):
            result = asyncio.run(
                voice_lab_module.call_vendors_bulk(
                    search_id="s1",
                    vendor_ids=[f"v{i}" for i in range(8)],
                    provider_id="gemini_live",
                    concurrency=50,  # way over the cap
                )
            )

        assert result["concurrency"] == 3
        assert state["peak"] <= 3
        assert len(result["started"]) == 8


def test_bulk_concurrency_defaults_to_cap_when_unset():
    """When no concurrency is requested, it defaults to the hard cap."""
    with _snapshot_bulk_settings():
        settings.voice_lab_bulk_max_concurrency = 5
        settings.pipecat_call_spacing_seconds = 0.0
        session = _make_fake_session(2)

        with patch.object(voice_lab_module, "ensure_session", new=AsyncMock(return_value=session)), \
             patch.object(voice_lab_module, "_provider_enabled", return_value=True), \
             patch.object(voice_lab_module, "_find_vendor", side_effect=lambda s, vid: next((v for v in s.vendors if v._id == vid), None)), \
             patch.object(voice_lab_module, "_vendor_key", side_effect=lambda v: v._id), \
             patch.object(voice_lab_module, "call_vendor_with_provider", new=AsyncMock(side_effect=lambda *a, **k: SimpleNamespace(call_id="c"))), \
             patch.object(voice_lab_module.persistence, "record_call_attempt", new=AsyncMock()), \
             patch.object(voice_lab_module, "_complete_call_attempt", new=AsyncMock()), \
             patch.object(voice_lab_module, "session_payload", new=AsyncMock(return_value={})):
            result = asyncio.run(
                voice_lab_module.call_vendors_bulk(
                    search_id="s1",
                    vendor_ids=["v0", "v1"],
                    provider_id="gemini_live",
                    concurrency=None,
                )
            )
        assert result["concurrency"] == 5

