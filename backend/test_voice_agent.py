"""Tests for voice provider resolution in `app.services.voice_agent`.

Covers the additive `gemini_live` provider introduced by the
gemini-live-call-provider feature:

  * Property 2 - resolution totality (case/whitespace folding, total over all
    inputs, empty/whitespace -> default with no warning).
  * Property 3 - per-call override isolation (a per-call override never mutates
    the global `settings.voice_provider`).
  * Property 4 - adapter clamp (concurrency >= 1, spacing >= 0.1, provider_id
    and display_name well-formed) over arbitrary int/float settings.

Run from the backend directory:  .venv/bin/python -m pytest test_voice_agent.py -q
"""
from __future__ import annotations

from unittest.mock import patch

from hypothesis import assume, given, settings as hyp_settings, strategies as st

import app.services.voice_agent as voice_agent
from app.config import settings
from app.services.voice_agent import _voice_provider_adapter, _voice_provider_id

# The full set of recognized provider ids after gemini_live was added.
KNOWN_PROVIDER_IDS = {"bolna", "elevenlabs_plivo", "plivo_bridge", "pipecat", "gemini_live"}
DEFAULT_PROVIDER_ID = "bolna"


@st.composite
def gemini_live_variants(draw):
    """Case-folded, whitespace-padded variants of the literal "gemini_live"."""
    base = "gemini_live"
    cased = "".join(draw(st.sampled_from([ch.lower(), ch.upper()])) for ch in base)
    lead = draw(st.text(alphabet=" \t\n\r", max_size=4))
    trail = draw(st.text(alphabet=" \t\n\r", max_size=4))
    return f"{lead}{cased}{trail}"


# Feature: gemini-live-call-provider, Property 2
@hyp_settings(max_examples=25)
@given(override=gemini_live_variants())
def test_resolution_folds_gemini_live_variants(override):
    """Any case/whitespace variant of "gemini_live" resolves to "gemini_live".

    Validates: Requirements 2.1, 2.2
    """
    assert _voice_provider_id(override) == "gemini_live"


# Feature: gemini-live-call-provider, Property 2
@hyp_settings(max_examples=25)
@given(override=st.text())
def test_resolution_is_total_over_arbitrary_text(override):
    """Resolution always returns one of the known provider ids for any string.

    Validates: Requirements 2.1, 2.3
    """
    # Pin the global so an empty/whitespace override (which falls back to the
    # global) has a deterministic, recognized resolution.
    saved = settings.voice_provider
    try:
        settings.voice_provider = DEFAULT_PROVIDER_ID
        assert _voice_provider_id(override) in KNOWN_PROVIDER_IDS
    finally:
        settings.voice_provider = saved


# Feature: gemini-live-call-provider, Property 2
@hyp_settings(max_examples=25)
@given(override=st.text(alphabet=" \t\n\r", max_size=8))
def test_empty_or_whitespace_resolves_to_default_without_warning(override):
    """Empty/whitespace overrides resolve to the default with no warning.

    Validates: Requirements 2.6
    """
    saved = settings.voice_provider
    try:
        # With the global pinned to the default, both the empty-string fallback
        # path and the whitespace-trim path must yield the default and warn for
        # neither.
        settings.voice_provider = DEFAULT_PROVIDER_ID
        with patch.object(voice_agent.logger, "warning") as mock_warning:
            result = _voice_provider_id(override)
        assert result == DEFAULT_PROVIDER_ID
        mock_warning.assert_not_called()
    finally:
        settings.voice_provider = saved


# Feature: gemini-live-call-provider, Property 3
@hyp_settings(max_examples=25)
@given(
    override=st.one_of(
        gemini_live_variants(),
        st.sampled_from(sorted(KNOWN_PROVIDER_IDS)),
        st.text(),
    )
)
def test_per_call_override_leaves_global_unchanged(override):
    """A per-call override never mutates the global `settings.voice_provider`.

    Validates: Requirements 2.2
    """
    saved = settings.voice_provider
    try:
        settings.voice_provider = "pipecat"  # a recognized non-gemini global
        before = settings.voice_provider

        _voice_provider_id(override)
        _voice_provider_adapter(override)

        assert settings.voice_provider == before == "pipecat"
    finally:
        settings.voice_provider = saved


# Feature: gemini-live-call-provider, Property 4
@hyp_settings(max_examples=25)
@given(
    max_concurrent=st.integers(min_value=-1000, max_value=1000),
    call_spacing=st.floats(
        min_value=-1000.0,
        max_value=1000.0,
        allow_nan=False,
        allow_infinity=False,
    ),
)
def test_gemini_live_adapter_clamps(max_concurrent, call_spacing):
    """The gemini_live adapter clamps concurrency >= 1 and spacing >= 0.1.

    Validates: Requirements 2.4, 8.5
    """
    saved_max = settings.gemini_live_max_concurrent_calls
    saved_spacing = settings.gemini_live_call_spacing_seconds
    try:
        settings.gemini_live_max_concurrent_calls = max_concurrent
        settings.gemini_live_call_spacing_seconds = call_spacing

        adapter = _voice_provider_adapter("gemini_live")

        assert adapter.provider_id == "gemini_live"
        assert isinstance(adapter.display_name, str) and adapter.display_name.strip()
        assert adapter.concurrency_limit >= 1
        assert adapter.call_spacing_seconds >= 0.1
    finally:
        settings.gemini_live_max_concurrent_calls = saved_max
        settings.gemini_live_call_spacing_seconds = saved_spacing


# ---------------------------------------------------------------------------
# Task 5.1 - routing exclusivity, E.164 gate, mock attribution, cascade payload
#
#   * Property 5 (satisfies R9.7) - routing exclusivity + payload shape: exactly
#     one Gemini-mode `/start` request for `gemini_live`, zero for every other
#     provider, and the cascade (`pipecat`) payload is byte-equal to the
#     pre-feature payload with NO `mode` key.
#   * Property 6 - E.164 gate: invalid numbers -> failed/`invalid_number`, no HTTP.
#   * Property 7 - mock attribution: `mock_voice_calls` -> result attributed to
#     `gemini_live` with no HTTP.
#   * Edge tests - error-status / timeout / send-exception failure attribution.
# ---------------------------------------------------------------------------
import asyncio
import contextlib
import re

import httpx
import pytest

from app.models.schemas import VendorInfo
from app.services.voice_agent import _call_vendor_gemini_live, call_vendor_with_provider

# A valid E.164 destination (already-`+` so `_normalize_indian_phone` keeps it).
_VALID_E164 = "+14155550100"

# Settings fields the task 5.1 tests mutate.
_ROUTING_SETTINGS_FIELDS = (
    "mock_voice_calls",
    "pipecat_agent_base_url",
    "pipecat_telephony_provider",
    "gemini_live_telephony_provider",
    "test_call_phone",
    "voice_webhook_base_url",
    "voice_provider",
    "bolna_api_key",
    "bolna_agent_id",
    "elevenlabs_api_key",
    "elevenlabs_agent_id",
    "elevenlabs_agent_phone_number_id",
    "plivo_bridge_base_url",
)


@contextlib.contextmanager
def _snapshot_settings(fields=_ROUTING_SETTINGS_FIELDS):
    saved = {field: getattr(settings, field) for field in fields}
    try:
        yield
    finally:
        for field, value in saved.items():
            setattr(settings, field, value)


class _FakeResponse:
    """Minimal stand-in for an ``httpx.Response`` used by the agent handlers."""

    def __init__(self, *, json_data=None, status_code=200, is_error=False, raise_exc=None):
        self._json = json_data or {"client_call_id": "agent-cid", "call_sid": "sid-1"}
        self.status_code = status_code
        self.is_error = is_error
        self.text = ""
        self._raise_exc = raise_exc

    def json(self):
        return self._json

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc


@contextlib.contextmanager
def _stub_httpx(behavior=None):
    """Patch ``voice_agent.httpx.AsyncClient`` and capture every POST.

    ``behavior`` (optional) is ``callable(url, json) -> _FakeResponse`` and may
    raise to simulate timeout / transport errors. Yields the captured-request
    list (each entry is ``{"url": ..., "json": ...}``).
    """
    captured: list[dict] = []

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, timeout=None, headers=None, **kwargs):
            captured.append({"url": url, "json": json})
            if behavior is not None:
                return behavior(url, json)
            return _FakeResponse()

    with patch.object(voice_agent.httpx, "AsyncClient", _FakeClient):
        yield captured


def _vendor(name="Test Vendor", phone=_VALID_E164, address="1 Test Rd", place_id="place-1"):
    return VendorInfo(name=name, phone=phone, address=address, place_id=place_id)


def _gemini_start_requests(captured):
    """Requests that are Gemini-mode `/start` posts (carry mode=gemini_live)."""
    return [
        req
        for req in captured
        if req["url"].endswith("/start") and (req["json"] or {}).get("mode") == "gemini_live"
    ]


# Feature: gemini-live-call-provider, Property 5
def test_property5_routing_exclusivity_only_gemini_sends_gemini_request():
    """Exactly one Gemini-mode `/start` for `gemini_live`, zero for others.

    Validates: Requirements 3.1, 9.7
    """
    campaign_config = {"product": "999 gold", "slot_order": ["availability", "price"]}
    other_providers = ["pipecat", "bolna", "elevenlabs_plivo", "plivo_bridge", "exotel"]

    with _snapshot_settings():
        settings.mock_voice_calls = False
        settings.pipecat_agent_base_url = "https://agent.example.com"
        settings.pipecat_telephony_provider = "plivo"
        settings.gemini_live_telephony_provider = "plivo"
        settings.test_call_phone = _VALID_E164
        settings.voice_webhook_base_url = "https://hooks.example.com"
        # Force the non-pipecat providers down their mock branch (no creds), so
        # they never emit HTTP at all - still zero Gemini-mode requests.
        settings.bolna_api_key = ""
        settings.bolna_agent_id = ""
        settings.elevenlabs_api_key = ""
        settings.plivo_bridge_base_url = ""

        # gemini_live -> exactly one Gemini-mode /start request.
        with _stub_httpx() as captured:
            asyncio.run(
                call_vendor_with_provider(
                    _vendor(), "999 gold", "gemini_live", campaign_config=campaign_config
                )
            )
        assert len(_gemini_start_requests(captured)) == 1

        # every other provider -> zero Gemini-mode /start requests.
        for provider_id in other_providers:
            with _stub_httpx() as captured:
                asyncio.run(
                    call_vendor_with_provider(
                        _vendor(), "999 gold", provider_id, campaign_config=campaign_config
                    )
                )
            assert _gemini_start_requests(captured) == [], (
                f"provider {provider_id} emitted a Gemini-mode /start request"
            )


# Feature: gemini-live-call-provider, Property 5
def test_property5_gemini_payload_contains_required_fields():
    """The Gemini `/start` payload carries every required field + the rail.

    Validates: Requirements 3.1, 3.2
    """
    campaign_config = {"product": "999 gold", "slot_order": ["availability", "price"]}
    vendor = _vendor(name="Acme Gold", phone=_VALID_E164, address="42 Bullion St", place_id="pl-9")

    with _snapshot_settings():
        settings.mock_voice_calls = False
        settings.pipecat_agent_base_url = "https://agent.example.com"
        settings.gemini_live_telephony_provider = "exotel"
        settings.test_call_phone = _VALID_E164
        settings.voice_webhook_base_url = "https://hooks.example.com"

        with _stub_httpx() as captured:
            asyncio.run(
                call_vendor_with_provider(
                    vendor, "999 gold", "gemini_live", campaign_config=campaign_config
                )
            )

    gemini_requests = _gemini_start_requests(captured)
    assert len(gemini_requests) == 1
    payload = gemini_requests[0]["json"]

    assert payload["mode"] == "gemini_live"
    assert payload["phone_number"] == _VALID_E164
    assert payload["client_call_id"].startswith("gemini_live-")
    assert payload["provider"] == "exotel"  # the selected telephony rail
    assert payload["product_name"] == "999 gold"
    assert payload["vendor"] == {
        "name": "Acme Gold",
        "phone": _VALID_E164,
        "address": "42 Bullion St",
        "place_id": "pl-9",
    }
    assert payload["campaign_config"] == campaign_config
    assert payload["callback_url"] == "https://hooks.example.com/api/webhooks/voice/pipecat"


# Feature: gemini-live-call-provider, Property 5
def test_property5_cascade_payload_is_byte_equal_with_no_mode_key():
    """The cascade (`pipecat`) `/start` payload is unchanged and has no `mode`.

    Validates: Requirements 7.2, 7.6
    """
    campaign_config = {"product": "999 gold", "slot_order": ["availability", "price"]}
    vendor = _vendor(name="Acme Gold", phone=_VALID_E164, address="42 Bullion St", place_id="pl-9")
    fixed_uuid = "fixed-uuid-1234"

    with _snapshot_settings():
        settings.mock_voice_calls = False
        settings.pipecat_agent_base_url = "https://agent.example.com"
        settings.pipecat_telephony_provider = "plivo"
        settings.test_call_phone = _VALID_E164
        settings.voice_webhook_base_url = "https://hooks.example.com"

        with _stub_httpx() as captured:
            with patch.object(voice_agent.uuid, "uuid4", return_value=fixed_uuid):
                asyncio.run(
                    call_vendor_with_provider(
                        vendor, "999 gold", "pipecat", campaign_config=campaign_config
                    )
                )

    start_requests = [req for req in captured if req["url"].endswith("/start")]
    assert len(start_requests) == 1
    payload = start_requests[0]["json"]

    # The exact pre-feature cascade payload (no `mode` key, ever).
    expected_payload = {
        "client_call_id": f"pipecat-{fixed_uuid}",
        "phone_number": _VALID_E164,
        "provider": "plivo",
        "product_name": "999 gold",
        "callback_url": "https://hooks.example.com/api/webhooks/voice/pipecat",
        "vendor": {
            "name": "Acme Gold",
            "phone": _VALID_E164,
            "address": "42 Bullion St",
            "place_id": "pl-9",
        },
        "campaign_config": campaign_config,
    }
    assert "mode" not in payload
    assert payload == expected_payload


# A string that is NOT a valid E.164 number ("+" + 8..15 digits).
@st.composite
def invalid_phone_numbers(draw):
    candidate = draw(
        st.one_of(
            st.text(max_size=20),
            st.from_regex(r"\A\+\d{0,7}\Z", fullmatch=True),  # too few digits
            st.from_regex(r"\A\+\d{16,20}\Z", fullmatch=True),  # too many digits
            st.from_regex(r"\A\d{8,15}\Z", fullmatch=True),  # missing leading +
        )
    )
    assume(re.fullmatch(r"\+\d{8,15}", candidate) is None)
    return candidate


# Feature: gemini-live-call-provider, Property 6
@hyp_settings(max_examples=25)
@given(phone=invalid_phone_numbers())
def test_property6_invalid_number_fails_without_http(phone):
    """An invalid E.164 number -> failed/`invalid_number` and no HTTP request.

    Validates: Requirements 3.3
    """
    with _snapshot_settings():
        settings.mock_voice_calls = False
        settings.pipecat_agent_base_url = "https://agent.example.com"
        settings.gemini_live_telephony_provider = "plivo"

        # Isolate the E.164 gate: feed the generated string straight to the gate
        # rather than through the Indian-number normalizer.
        with patch.object(voice_agent, "_call_destination_phone", return_value=phone):
            with _stub_httpx() as captured:
                result = asyncio.run(
                    _call_vendor_gemini_live(_vendor(phone=phone), "999 gold", None, {"product": "x"})
                )

    assert result.status == "failed"
    assert result.provider == "gemini_live"
    assert result.provider_metadata["failure_type"] == "invalid_number"
    assert captured == []  # no outbound request was sent


# Feature: gemini-live-call-provider, Property 6
@hyp_settings(max_examples=25)
@given(digits=st.text(alphabet="0123456789", min_size=8, max_size=15))
def test_property6_valid_number_sends_one_request(digits):
    """A valid E.164 number -> exactly one Gemini-mode `/start` request.

    Validates: Requirements 3.1, 3.3
    """
    phone = f"+{digits}"
    with _snapshot_settings():
        settings.mock_voice_calls = False
        settings.pipecat_agent_base_url = "https://agent.example.com"
        settings.gemini_live_telephony_provider = "plivo"

        with patch.object(voice_agent, "_call_destination_phone", return_value=phone):
            with _stub_httpx() as captured:
                result = asyncio.run(
                    _call_vendor_gemini_live(_vendor(phone=phone), "999 gold", None, {"product": "x"})
                )

    assert len(_gemini_start_requests(captured)) == 1
    assert result.provider == "gemini_live"
    assert result.status == "busy"


# Feature: gemini-live-call-provider, Property 7
@hyp_settings(max_examples=25, deadline=None)
@given(
    name=st.text(min_size=1, max_size=40),
    phone=st.text(min_size=1, max_size=20),
    address=st.text(max_size=60),
    product=st.text(min_size=1, max_size=40),
)
def test_property7_mock_attributed_to_gemini_live_without_http(name, phone, address, product):
    """With `mock_voice_calls`, a Gemini call is attributed to `gemini_live`, no HTTP.

    Validates: Requirements 3.8
    """
    with _snapshot_settings():
        settings.mock_voice_calls = True
        # base url present to prove the mock branch wins regardless.
        settings.pipecat_agent_base_url = "https://agent.example.com"

        with _stub_httpx() as captured:
            result = asyncio.run(
                _call_vendor_gemini_live(
                    _vendor(name=name, phone=phone, address=address), product, None, {"product": "x"}
                )
            )

    assert result.provider == "gemini_live"
    assert result.is_mock is True
    assert result.status == "completed"
    assert captured == []  # no contact with the agent


def test_edge_error_status_attributes_failure_to_gemini_live():
    """An error status -> `pipecat_request_failed` attribution to `gemini_live`.

    Validates: Requirements 3.9
    """
    req = httpx.Request("POST", "https://agent.example.com/start")
    resp = httpx.Response(500, request=req)
    err = httpx.HTTPStatusError("server error", request=req, response=resp)

    def behavior(url, json):
        return _FakeResponse(status_code=500, is_error=True, raise_exc=err)

    with _snapshot_settings():
        settings.mock_voice_calls = False
        settings.pipecat_agent_base_url = "https://agent.example.com"
        settings.test_call_phone = _VALID_E164

        with patch.object(voice_agent, "capture_message") as mock_capture:
            with _stub_httpx(behavior=behavior):
                with pytest.raises(httpx.HTTPStatusError):
                    asyncio.run(
                        _call_vendor_gemini_live(_vendor(), "999 gold", None, {"product": "x"})
                    )

    # The failure was attributed to gemini_live with the right failure type.
    assert mock_capture.call_count == 1
    kwargs = mock_capture.call_args.kwargs
    assert kwargs["provider"] == "gemini_live"
    assert kwargs["call_failure_type"] == "pipecat_request_failed"


def test_edge_timeout_is_transport_failure_not_connected():
    """A timeout propagates as a transport failure; no connected result.

    Validates: Requirements 3.10
    """
    def behavior(url, json):
        raise httpx.TimeoutException("timed out")

    with _snapshot_settings():
        settings.mock_voice_calls = False
        settings.pipecat_agent_base_url = "https://agent.example.com"
        settings.test_call_phone = _VALID_E164

        with _stub_httpx(behavior=behavior) as captured:
            with pytest.raises(httpx.TimeoutException):
                asyncio.run(_call_vendor_gemini_live(_vendor(), "999 gold", None, {"product": "x"}))

    # The attempt went out as a Gemini-mode request and never became connected.
    assert len(_gemini_start_requests(captured)) == 1


def test_edge_send_exception_returns_no_connected_result():
    """A send-time exception propagates; the call is never reported connected.

    Validates: Requirements 3.11
    """
    def behavior(url, json):
        raise RuntimeError("send failed")

    with _snapshot_settings():
        settings.mock_voice_calls = False
        settings.pipecat_agent_base_url = "https://agent.example.com"
        settings.test_call_phone = _VALID_E164

        with _stub_httpx(behavior=behavior) as captured:
            with pytest.raises(RuntimeError):
                asyncio.run(_call_vendor_gemini_live(_vendor(), "999 gold", None, {"product": "x"}))

    assert len(_gemini_start_requests(captured)) == 1


# ===========================================================================
# Feature: dynamic-extraction-schema, Task 5.1
#
# Wiring schema generation into the campaign-config builder. These tests
# exercise `default_campaign_config_for_query` (flag-gated) and
# `generate_campaign_and_schema` (always enriches), with the LLM calls mocked
# so they are deterministic and offline:
#
#   * the schema-generation call (`voice_agent._generate_schema_payload`) and
#   * the opening-line polish call (`voice_campaign_polish.polish_campaign`)
#     are both patched away.
#
#   * Property 2 - schema ⊇ slots: any config that HAS an `extraction_schema`
#     has, for every `slot_order` entry, a matching field in the schema.
#   * Property 3 - agent slots stay known: every agent-facing `slot_order`
#     entry is a member of `KNOWN_CAMPAIGN_SLOTS`.
#   * Example (Req 1.3) - schema-gen failure/timeout omits `extraction_schema`
#     while leaving an otherwise-valid campaign config.
#   * Example (Req 6.2/6.3) - flag OFF returns today's config unchanged.
# ===========================================================================
import app.services.voice_campaign_polish as voice_campaign_polish
from app.models.schemas import StructuredQuery
from app.services.extraction_schema import sanitize_field_name
from app.services.voice_agent import (
    KNOWN_CAMPAIGN_SLOTS,
    _build_base_campaign_config,
    default_campaign_config_for_query,
    generate_campaign_and_schema,
)

# Settings fields the dynamic-extraction tests mutate.
_DYNEX_SETTINGS_FIELDS = (
    "dynamic_extraction_enabled",
    "openai_api_key",
    "dynamic_extraction_max_fields",
)


@contextlib.contextmanager
def _snapshot_dynex_settings():
    saved = {f: getattr(settings, f) for f in _DYNEX_SETTINGS_FIELDS}
    try:
        yield
    finally:
        for f, v in saved.items():
            setattr(settings, f, v)


# Products spanning gold (special procurement persona) and non-gold (electronics
# price-check) presets so both base configs are exercised.
_DYNEX_PRODUCTS = [
    "guitar",
    "office chair",
    "CNC machine",
    "split air conditioner",
    "laptop",
    "steel pipe",
    "running shoes",
    "999 gold coin",
    "22k gold necklace",
    "gold bullion 100 gram",
]


@st.composite
def _dynex_queries(draw):
    """Arbitrary `StructuredQuery` objects across gold/non-gold + bulk/GST."""
    product = draw(st.sampled_from(_DYNEX_PRODUCTS))
    intent = draw(st.sampled_from(["cheapest", "fastest", "best_value", "nearest"]))
    urgency = draw(st.sampled_from(["immediate", "1-2 days", "10 days", "no rush"]))
    quantity = draw(
        st.one_of(
            st.none(),
            st.sampled_from(["10 pieces", "1 litre", "100 gram", "2 dozen", "5 units"]),
        )
    )
    use_case = draw(
        st.one_of(
            st.none(),
            st.sampled_from(
                ["consumer_single", "consumer_bulk", "industrial", "b2b_bulk"]
            ),
        )
    )
    gst_required = draw(st.one_of(st.none(), st.booleans()))
    return StructuredQuery(
        product=product,
        category="general",
        location="Mumbai",
        intent=intent,
        urgency=urgency,
        raw_query=product,
        quantity=quantity,
        use_case=use_case,
        gst_required=gst_required,
    )


# An arbitrary/untrusted LLM-proposed extraction field (mirrors the hostile
# inputs in test_extraction_schema.py): unicode, punctuation, empty, bad types.
_dynex_arbitrary_text = st.text(max_size=40)
_dynex_field_types = st.sampled_from(
    ["string", "number", "boolean", "enum", "weird", ""]
)


@st.composite
def _dynex_raw_field(draw):
    return {
        "name": draw(
            st.one_of(
                _dynex_arbitrary_text,
                st.sampled_from(
                    ["wattage", "lead_time", "fabric", "part_number", "amc_terms"]
                ),
            )
        ),
        "type": draw(_dynex_field_types),
        "description": draw(st.one_of(st.none(), _dynex_arbitrary_text)),
        "enum_values": draw(
            st.one_of(st.none(), st.lists(_dynex_arbitrary_text, max_size=4))
        ),
    }


_dynex_field_lists = st.lists(_dynex_raw_field(), max_size=12)


def _fake_polish_factory():
    """An offline `polish_campaign` stand-in that returns the fallback verbatim.

    The real polish path makes an OpenAI call when `openai_api_key` is set; the
    dynamic-extraction tests set that key (so schema enrichment proceeds), so we
    must neutralize polish to stay deterministic and offline.
    """

    async def _fake_polish(*, product, category, fallback):
        return fallback

    return _fake_polish


@contextlib.contextmanager
def _mock_schema_gen(payload=None, *, raises=None):
    """Patch the schema-generation LLM call and the polish call.

    ``payload`` is returned from `_generate_schema_payload`; ``raises`` (an
    exception instance) is raised instead, simulating a failure/timeout.
    """

    async def _fake_payload(*, query, product, benchmark_price):
        if raises is not None:
            raise raises
        return payload

    with patch.object(voice_agent, "_generate_schema_payload", _fake_payload):
        with patch.object(
            voice_campaign_polish, "polish_campaign", _fake_polish_factory()
        ):
            yield


# Feature: dynamic-extraction-schema, Property 2
@hyp_settings(max_examples=100, deadline=None)
@given(query=_dynex_queries(), llm_fields=_dynex_field_lists)
def test_property2_schema_is_superset_of_slots(query, llm_fields):
    """Any config with an `extraction_schema` has a field for every slot.

    For every entry in the agent-facing `slot_order`, the sanitized slot name
    appears in the generated `extraction_schema` field-name set (core fields
    `price`/`availability` count, dynamic fields cover the rest).

    Validates: Requirements 2.2
    """
    payload = {"extraction_schema": llm_fields}
    with _snapshot_dynex_settings():
        settings.dynamic_extraction_enabled = True
        settings.openai_api_key = "sk-test"
        settings.dynamic_extraction_max_fields = 12
        with _mock_schema_gen(payload):
            config = asyncio.run(
                default_campaign_config_for_query(
                    query=query, product=query.product, benchmark_price=None
                )
            )

    # Only assert the property when a schema is present (success path).
    assert "extraction_schema" in config
    schema_names = {f["name"] for f in config["extraction_schema"]["fields"]}
    for slot in config["slot_order"]:
        sanitized = sanitize_field_name(slot)
        assert sanitized is not None
        assert sanitized in schema_names, (
            f"slot {slot!r} ({sanitized!r}) has no matching schema field; "
            f"schema names = {sorted(schema_names)}"
        )


# Feature: dynamic-extraction-schema, Property 3
@hyp_settings(max_examples=100, deadline=None)
@given(query=_dynex_queries(), llm_fields=_dynex_field_lists)
def test_property3_agent_slots_stay_known(query, llm_fields):
    """Every agent-facing `slot_order` entry is a member of KNOWN_CAMPAIGN_SLOTS.

    Checked for both the flag-gated `default_campaign_config_for_query` and the
    always-enriching `generate_campaign_and_schema`.

    Validates: Requirements 2.3
    """
    payload = {"extraction_schema": llm_fields}
    with _snapshot_dynex_settings():
        settings.dynamic_extraction_enabled = True
        settings.openai_api_key = "sk-test"
        settings.dynamic_extraction_max_fields = 12
        with _mock_schema_gen(payload):
            gated = asyncio.run(
                default_campaign_config_for_query(
                    query=query, product=query.product, benchmark_price=None
                )
            )
            direct = asyncio.run(
                generate_campaign_and_schema(
                    query=query, product=query.product, benchmark_price=None
                )
            )

    for config in (gated, direct):
        for slot in config["slot_order"]:
            assert slot in KNOWN_CAMPAIGN_SLOTS, (
                f"agent slot {slot!r} is not in KNOWN_CAMPAIGN_SLOTS"
            )


# Feature: dynamic-extraction-schema, Req 1.3 (generation fallback)
@hyp_settings(max_examples=25, deadline=None)
@given(query=_dynex_queries())
def test_schema_gen_failure_omits_schema_but_stays_valid(query):
    """When schema generation raises/times out, `extraction_schema` is absent
    and the campaign config is otherwise valid.

    Validates: Requirements 1.3
    """
    with _snapshot_dynex_settings():
        settings.dynamic_extraction_enabled = True
        settings.openai_api_key = "sk-test"
        settings.dynamic_extraction_max_fields = 12
        # Simulate a schema-gen failure (covers raise + timeout per the bounded
        # wait_for wrapper, which surfaces TimeoutError the same way).
        with _mock_schema_gen(raises=asyncio.TimeoutError()):
            config = asyncio.run(
                default_campaign_config_for_query(
                    query=query, product=query.product, benchmark_price=None
                )
            )

    # No schema key on failure.
    assert "extraction_schema" not in config
    # Still a valid campaign config.
    assert isinstance(config.get("slot_order"), list) and config["slot_order"]
    assert isinstance(config.get("opening_line"), str) and config["opening_line"]
    assert isinstance(config.get("product"), str) and config["product"]
    # Agent slots remain known even on the fallback path.
    for slot in config["slot_order"]:
        assert slot in KNOWN_CAMPAIGN_SLOTS


# Feature: dynamic-extraction-schema, Req 6.2/6.3 (flag off = unchanged)
@hyp_settings(max_examples=25, deadline=None)
@given(query=_dynex_queries())
def test_flag_off_returns_base_config_unchanged(query):
    """With the flag OFF, the config equals the base builder's output exactly
    and carries no `extraction_schema` key.

    Validates: Requirements 6.2, 6.3
    """
    with _snapshot_dynex_settings():
        settings.dynamic_extraction_enabled = False
        settings.openai_api_key = "sk-test"
        # Patch polish so the base config is deterministic, and patch schema-gen
        # to a value that WOULD be used if the flag were (incorrectly) honored.
        with _mock_schema_gen({"extraction_schema": [{"name": "wattage", "type": "number"}]}):
            base = asyncio.run(
                _build_base_campaign_config(
                    query=query, product=query.product, benchmark_price=None
                )
            )
            gated = asyncio.run(
                default_campaign_config_for_query(
                    query=query, product=query.product, benchmark_price=None
                )
            )

    assert "extraction_schema" not in gated
    assert gated == base
