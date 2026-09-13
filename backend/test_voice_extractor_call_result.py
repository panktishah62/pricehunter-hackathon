"""Tests for `extract_from_call_result` schema branching (Task 9).

These cover the correctness properties from the dynamic-extraction-schema design
that govern the extraction-precedence wiring inside `extract_from_call_result`:

  * Property 6 - fallback preserves the contract: with the flag OFF, no schema,
    or `extract_with_schema` returning None, the call routes through the fixed
    `extract_from_transcript` extractor (shape unchanged).
  * Property 1 - core fields (price/availability/negotiated) are always present
    on the result, on both the schema path and the fallback path; the record is
    never dropped/None.
  * Property 7 - gold invariant: when `call.extracted_data` carries gold fields,
    the result still yields the full `gold_terms` key set unchanged, and schema
    extraction is NOT invoked (the extracted_data path wins).
  * Property 9 - dynamic-field omission never fails: a schema whose dynamic
    fields are absent from the transcript still extracts a valid result.

The OpenAI client is never hit: `extract_with_schema`, `extract_from_transcript`
and `record_call_for_reliability` are patched as needed. Async entrypoints are
driven via `asyncio.run` to match the existing backend test convention
(pytest-asyncio is not installed).

Run from the backend directory:  python -m pytest test_voice_extractor_call_result.py -q
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from hypothesis import HealthCheck, given, settings as hyp_settings, strategies as st

from app.models.schemas import (
    ExtractionField,
    ExtractionSchema,
    UnifiedResult,
    VendorInfo,
    VoiceCallResult,
)
from app.services import voice_extractor

# The complete gold_terms key set assembled by `_build_gold_terms`.
GOLD_TERMS_KEYS = {
    "cash_rate",
    "bill_rate",
    "rate_valid_window",
    "payment_terms",
    "delivery_location",
    "confirm_channel",
    "benchmark_rate_per_gram",
    "quote_vs_benchmark",
}


def _vendor() -> VendorInfo:
    # No vendor_id -> record_call_for_reliability short-circuits (no side effect).
    return VendorInfo(name="Acme Electronics", phone="+910000000000", address="1 Main St")


def _schema() -> ExtractionSchema:
    return ExtractionSchema(
        schema_version="v1-test",
        fields=[
            ExtractionField(name="price", type="number", is_core=True),
            ExtractionField(name="availability", type="boolean", is_core=True),
            ExtractionField(name="negotiated", type="boolean", is_core=True),
            ExtractionField(name="wattage", type="number", is_core=False),
            ExtractionField(name="warranty", type="string", is_core=False),
        ],
    )


def _call(*, transcript="agent: price? vendor: 1500 rupees", extracted_data=None, metadata=None):
    return VoiceCallResult(
        vendor=_vendor(),
        call_id="call-1",
        provider="pipecat",
        status="completed",
        transcript=transcript,
        extracted_data=extracted_data,
        provider_metadata=metadata,
    )


def _run(coro):
    return asyncio.run(coro)


def _assert_core_present(result: UnifiedResult) -> None:
    """Property 1: core fields always present with documented defaults."""
    assert result is not None
    # `price` attribute always exists (may be None per the default).
    assert hasattr(result, "price")
    # availability/negotiated are always booleans.
    assert isinstance(result.availability, bool)
    assert isinstance(result.negotiated, bool)


# --------------------------------------------------------------------------
# Property 6: fallback preserves the contract
# --------------------------------------------------------------------------
# Feature: dynamic-extraction-schema, Property 6
def test_flag_off_routes_through_fixed_extractor_and_skips_schema():
    """Flag OFF -> fixed extractor used; schema path is never taken.

    Validates: Requirements 6.2, 4.4
    """
    sentinel = UnifiedResult(source_type="offline", name="fixed", price=1.0)

    with patch.object(voice_extractor.settings, "dynamic_extraction_enabled", False), patch.object(
        voice_extractor, "extract_with_schema", new=AsyncMock()
    ) as mock_schema, patch.object(
        voice_extractor, "extract_from_transcript", new=AsyncMock(return_value=sentinel)
    ) as mock_fixed, patch.object(
        voice_extractor, "record_call_for_reliability", new=AsyncMock()
    ) as mock_record:
        result = _run(
            voice_extractor.extract_from_call_result(
                _call(), "LED bulb", extraction_schema=_schema()
            )
        )

    assert result is sentinel
    mock_schema.assert_not_awaited()  # schema path NOT taken when flag off
    mock_fixed.assert_awaited_once()
    mock_record.assert_awaited_once()  # reliability still recorded on fallback


# Feature: dynamic-extraction-schema, Property 6
def test_no_schema_routes_through_fixed_extractor_even_with_flag_on():
    """Flag ON but no schema present -> fixed extractor used.

    Validates: Requirements 6.2, 4.4
    """
    sentinel = UnifiedResult(source_type="offline", name="fixed", price=2.0)

    with patch.object(voice_extractor.settings, "dynamic_extraction_enabled", True), patch.object(
        voice_extractor, "extract_with_schema", new=AsyncMock()
    ) as mock_schema, patch.object(
        voice_extractor, "extract_from_transcript", new=AsyncMock(return_value=sentinel)
    ) as mock_fixed, patch.object(
        voice_extractor, "record_call_for_reliability", new=AsyncMock()
    ) as mock_record:
        # No extraction_schema param AND no schema on the call metadata.
        result = _run(voice_extractor.extract_from_call_result(_call(), "LED bulb"))

    assert result is sentinel
    mock_schema.assert_not_awaited()
    mock_fixed.assert_awaited_once()
    mock_record.assert_awaited_once()


# Feature: dynamic-extraction-schema, Property 6
def test_schema_extraction_none_falls_back_to_fixed_extractor():
    """Flag ON + schema present but extract_with_schema returns None (retries
    exhausted) -> fixed extractor used and its result returned.

    Validates: Requirements 4.4, 6.2
    """
    sentinel = UnifiedResult(source_type="offline", name="fixed", price=3.0)

    with patch.object(voice_extractor.settings, "dynamic_extraction_enabled", True), patch.object(
        voice_extractor, "extract_with_schema", new=AsyncMock(return_value=None)
    ) as mock_schema, patch.object(
        voice_extractor, "extract_from_transcript", new=AsyncMock(return_value=sentinel)
    ) as mock_fixed, patch.object(
        voice_extractor, "record_call_for_reliability", new=AsyncMock()
    ) as mock_record:
        result = _run(
            voice_extractor.extract_from_call_result(
                _call(), "LED bulb", extraction_schema=_schema()
            )
        )

    assert result is sentinel
    mock_schema.assert_awaited_once()  # schema path attempted...
    mock_fixed.assert_awaited_once()  # ...then fell back to the fixed extractor
    mock_record.assert_awaited_once()


# Feature: dynamic-extraction-schema, Property 6
@hyp_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    flag_on=st.booleans(),
    has_schema=st.booleans(),
    schema_returns_none=st.booleans(),
)
def test_fallback_taken_whenever_schema_path_unavailable(flag_on, has_schema, schema_returns_none):
    """Across the flag/schema/None matrix, the fixed extractor is used exactly
    when the schema path is unavailable (flag off, no schema, or None result).

    Validates: Requirements 4.4, 6.2
    """
    fixed_sentinel = UnifiedResult(source_type="offline", name="fixed", price=4.0)
    schema_sentinel = UnifiedResult(source_type="offline", name="schema", price=5.0)

    schema_return = None if schema_returns_none else schema_sentinel
    schema_arg = _schema() if has_schema else None

    with patch.object(voice_extractor.settings, "dynamic_extraction_enabled", flag_on), patch.object(
        voice_extractor, "extract_with_schema", new=AsyncMock(return_value=schema_return)
    ) as mock_schema, patch.object(
        voice_extractor, "extract_from_transcript", new=AsyncMock(return_value=fixed_sentinel)
    ) as mock_fixed, patch.object(
        voice_extractor, "record_call_for_reliability", new=AsyncMock()
    ):
        result = _run(
            voice_extractor.extract_from_call_result(
                _call(), "LED bulb", extraction_schema=schema_arg
            )
        )

    schema_path_used = flag_on and has_schema and not schema_returns_none
    if schema_path_used:
        assert result is schema_sentinel
        mock_fixed.assert_not_awaited()
    else:
        # Fixed extractor used whenever schema path is unavailable.
        assert result is fixed_sentinel
        mock_fixed.assert_awaited_once()
        # Schema extraction only attempted when flag on AND schema present.
        if flag_on and has_schema:
            mock_schema.assert_awaited_once()
        else:
            mock_schema.assert_not_awaited()


# --------------------------------------------------------------------------
# Property 1: core fields always present (schema path AND fallback path)
# --------------------------------------------------------------------------
# Feature: dynamic-extraction-schema, Property 1
def test_core_fields_present_on_fallback_path():
    """The fallback path (no API key -> deterministic) yields core fields.

    Validates: Requirements 3.1, 3.2
    """
    with patch.object(voice_extractor.settings, "dynamic_extraction_enabled", False), patch.object(
        voice_extractor.settings, "openai_api_key", ""
    ), patch.object(voice_extractor, "record_call_for_reliability", new=AsyncMock()):
        result = _run(
            voice_extractor.extract_from_call_result(
                _call(transcript="vendor: 1500 rupees, in stock"), "LED bulb"
            )
        )

    _assert_core_present(result)
    assert result.price == 1500.0  # deterministic fallback parsed the price


# Feature: dynamic-extraction-schema, Property 1
def test_core_fields_present_on_schema_path():
    """The schema path yields core fields on their usual columns.

    Validates: Requirements 3.1
    """
    schema = _schema()
    Model = voice_extractor.build_dynamic_model(schema)
    parsed = Model(price=999.0, availability=True, negotiated=True, wattage=60.0)

    from types import SimpleNamespace

    client = SimpleNamespace()
    client.responses = SimpleNamespace()
    client.responses.parse = AsyncMock(return_value=SimpleNamespace(output_parsed=parsed))

    with patch.object(voice_extractor.settings, "dynamic_extraction_enabled", True), patch.object(
        voice_extractor.settings, "openai_api_key", "sk-test"
    ), patch.object(voice_extractor, "AsyncOpenAI", return_value=client), patch.object(
        voice_extractor, "record_call_for_reliability", new=AsyncMock()
    ):
        result = _run(
            voice_extractor.extract_from_call_result(
                _call(), "LED bulb", extraction_schema=schema
            )
        )

    _assert_core_present(result)
    assert result.price == 999.0
    assert result.availability is True
    assert result.negotiated is True
    assert result.attributes == {"wattage": 60.0}


# --------------------------------------------------------------------------
# Property 7: gold invariant (extracted_data path wins, schema NOT invoked)
# --------------------------------------------------------------------------
# Feature: dynamic-extraction-schema, Property 7
def test_gold_extracted_data_wins_and_schema_not_invoked():
    """When call.extracted_data carries gold fields, the result still yields the
    full gold_terms key set unchanged, and schema extraction is NOT invoked.

    Validates: Requirements 6.1, 6.2
    """
    extracted_data = {
        "price": 60000.0,
        "availability": True,
        "negotiated": True,
        "cash_rate": 5800.0,
        "bill_rate": 5950.0,
        "rate_valid_window": "1 minute",
        "payment_terms": "full cash only",
        "delivery_location": "Mumbai",
        "confirm_channel": "WhatsApp",
    }
    call = _call(extracted_data=extracted_data)

    with patch.object(voice_extractor.settings, "dynamic_extraction_enabled", True), patch.object(
        voice_extractor, "extract_with_schema", new=AsyncMock()
    ) as mock_schema, patch.object(
        voice_extractor, "extract_from_transcript", new=AsyncMock()
    ) as mock_fixed, patch.object(
        voice_extractor, "record_call_for_reliability", new=AsyncMock()
    ) as mock_record:
        result = _run(
            voice_extractor.extract_from_call_result(
                call,
                "22K gold",
                extraction_schema=_schema(),
                benchmark_rate_per_gram=5900.0,
            )
        )

    # The gold/structured-data path wins: neither schema nor transcript run.
    mock_schema.assert_not_awaited()
    mock_fixed.assert_not_awaited()
    mock_record.assert_awaited_once()

    # The full gold_terms key set is present and unchanged.
    assert result.gold_terms is not None
    assert set(result.gold_terms.keys()) == GOLD_TERMS_KEYS
    assert result.gold_terms["cash_rate"] == 5800.0
    assert result.gold_terms["bill_rate"] == 5950.0
    assert result.gold_terms["rate_valid_window"] == "1 minute"
    assert result.gold_terms["payment_terms"] == "full cash only"
    assert result.gold_terms["delivery_location"] == "Mumbai"
    assert result.gold_terms["confirm_channel"] == "WhatsApp"
    assert result.gold_terms["benchmark_rate_per_gram"] == 5900.0
    # quote_vs_benchmark is derived from cash_rate vs benchmark, rounded to 4dp.
    assert result.gold_terms["quote_vs_benchmark"] == round(
        (5800.0 - 5900.0) / 5900.0, 4
    )


# --------------------------------------------------------------------------
# Property 9: dynamic-field omission never fails extraction
# --------------------------------------------------------------------------
# Feature: dynamic-extraction-schema, Property 9
def test_dynamic_field_omission_does_not_fail_extraction():
    """A schema with dynamic fields the transcript lacks still extracts a valid
    result (the dynamic fields are simply omitted from attributes).

    Validates: Requirements 3.4
    """
    schema = _schema()
    Model = voice_extractor.build_dynamic_model(schema)
    # Mocked client returns only core fields; dynamic fields default to None.
    parsed = Model(price=500.0, availability=False, negotiated=False)

    from types import SimpleNamespace

    client = SimpleNamespace()
    client.responses = SimpleNamespace()
    client.responses.parse = AsyncMock(return_value=SimpleNamespace(output_parsed=parsed))

    with patch.object(voice_extractor.settings, "dynamic_extraction_enabled", True), patch.object(
        voice_extractor.settings, "openai_api_key", "sk-test"
    ), patch.object(voice_extractor, "AsyncOpenAI", return_value=client), patch.object(
        voice_extractor, "record_call_for_reliability", new=AsyncMock()
    ):
        result = _run(
            voice_extractor.extract_from_call_result(
                _call(transcript="vendor: out of stock"),
                "LED bulb",
                extraction_schema=schema,
            )
        )

    _assert_core_present(result)
    assert result.price == 500.0
    assert result.availability is False
    # Missing dynamic fields simply omitted -> no attributes, no failure.
    assert result.attributes is None


# --------------------------------------------------------------------------
# Schema path taken on success + reliability still recorded
# --------------------------------------------------------------------------
# Feature: dynamic-extraction-schema, Property 1 / Req 5.2
def test_schema_path_used_when_flag_on_and_good_result():
    """Flag ON + schema present + extract_with_schema returns a good result ->
    that result is used and record_call_for_reliability still runs.

    Validates: Requirements 5.2
    """
    schema_result = UnifiedResult(
        source_type="offline", name="schema", price=7.0, attributes={"wattage": 9.0}
    )

    with patch.object(voice_extractor.settings, "dynamic_extraction_enabled", True), patch.object(
        voice_extractor, "extract_with_schema", new=AsyncMock(return_value=schema_result)
    ) as mock_schema, patch.object(
        voice_extractor, "extract_from_transcript", new=AsyncMock()
    ) as mock_fixed, patch.object(
        voice_extractor, "record_call_for_reliability", new=AsyncMock()
    ) as mock_record:
        result = _run(
            voice_extractor.extract_from_call_result(
                _call(), "LED bulb", extraction_schema=_schema()
            )
        )

    assert result is schema_result
    mock_schema.assert_awaited_once()
    mock_fixed.assert_not_awaited()  # schema path won; fixed extractor not used
    mock_record.assert_awaited_once()
    # Reliability is recorded with the schema-path result.
    _, kwargs = mock_record.await_args
    assert kwargs["result"] is schema_result


# Feature: dynamic-extraction-schema, Property 1 / Req 5.2
def test_schema_read_from_call_metadata_when_param_absent():
    """When no extraction_schema param is passed, the schema is read off the
    call's provider_metadata (provider_metadata is the source of truth).

    Validates: Requirements 5.1, 5.2
    """
    schema = _schema()
    schema_result = UnifiedResult(source_type="offline", name="schema", price=8.0)
    call = _call(metadata={"campaign_config": {"extraction_schema": schema.model_dump()}})

    with patch.object(voice_extractor.settings, "dynamic_extraction_enabled", True), patch.object(
        voice_extractor, "extract_with_schema", new=AsyncMock(return_value=schema_result)
    ) as mock_schema, patch.object(
        voice_extractor, "extract_from_transcript", new=AsyncMock()
    ) as mock_fixed, patch.object(
        voice_extractor, "record_call_for_reliability", new=AsyncMock()
    ):
        result = _run(voice_extractor.extract_from_call_result(call, "LED bulb"))

    assert result is schema_result
    mock_schema.assert_awaited_once()
    mock_fixed.assert_not_awaited()
    # The schema passed to extract_with_schema came from the call metadata.
    args, _ = mock_schema.await_args
    passed_schema = args[1]
    assert isinstance(passed_schema, ExtractionSchema)
    assert passed_schema.schema_version == "v1-test"
