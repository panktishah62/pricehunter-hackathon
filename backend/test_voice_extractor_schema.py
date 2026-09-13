"""Unit tests for the schema-driven extractor (extract_with_schema).

The OpenAI client is fully mocked — no network access — so these tests exercise
the mapping, retry, and fallback logic only. Async entrypoints are driven via
``asyncio.run`` to match the existing backend test convention.

Run from the backend directory:  python -m pytest test_voice_extractor_schema.py -q
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models.schemas import ExtractionField, ExtractionSchema, VendorInfo
from app.services import voice_extractor


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


def _vendor() -> VendorInfo:
    return VendorInfo(
        vendor_id="v1",
        name="Acme Electronics",
        phone="+910000000000",
        address="1 Main St",
    )


def _mock_client_returning(parsed_obj):
    """Build a fake AsyncOpenAI whose responses.parse returns parsed_obj."""
    client = SimpleNamespace()
    client.responses = SimpleNamespace()
    client.responses.parse = AsyncMock(
        return_value=SimpleNamespace(output_parsed=parsed_obj)
    )
    return client


def test_extract_with_schema_maps_core_and_dynamic():
    schema = _schema()
    Model = voice_extractor.build_dynamic_model(schema)
    parsed = Model(
        price=1500.0, availability=True, negotiated=True, wattage=60.0, warranty="2 years"
    )
    client = _mock_client_returning(parsed)

    with patch.object(voice_extractor.settings, "openai_api_key", "sk-test"), patch.object(
        voice_extractor, "AsyncOpenAI", return_value=client
    ):
        result = asyncio.run(
            voice_extractor.extract_with_schema(
                "agent: price? vendor: 1500 rupees, 60 watt, 2 year warranty",
                schema,
                _vendor(),
                "LED bulb",
            )
        )

    assert result is not None
    # Core fields land on the usual columns.
    assert result.price == 1500.0
    assert result.availability is True
    assert result.negotiated is True
    # Dynamic fields land in attributes.
    assert result.attributes == {"wattage": 60.0, "warranty": "2 years"}


def test_extract_with_schema_omits_missing_dynamic_fields():
    schema = _schema()
    Model = voice_extractor.build_dynamic_model(schema)
    # Dynamic fields omitted -> default None.
    parsed = Model(price=500.0, availability=False, negotiated=False)
    client = _mock_client_returning(parsed)

    with patch.object(voice_extractor.settings, "openai_api_key", "sk-test"), patch.object(
        voice_extractor, "AsyncOpenAI", return_value=client
    ):
        result = asyncio.run(
            voice_extractor.extract_with_schema(
                "agent: ... vendor: out of stock", schema, _vendor(), "LED bulb"
            )
        )

    assert result is not None
    assert result.price == 500.0
    assert result.availability is False
    # No dynamic values present -> attributes is None (omitted, not failing).
    assert result.attributes is None


def test_extract_with_schema_returns_none_without_api_key():
    with patch.object(voice_extractor.settings, "openai_api_key", ""):
        result = asyncio.run(
            voice_extractor.extract_with_schema(
                "transcript", _schema(), _vendor(), "LED bulb"
            )
        )
    assert result is None


def test_extract_with_schema_retries_then_succeeds():
    schema = _schema()
    Model = voice_extractor.build_dynamic_model(schema)
    good = Model(price=10.0, availability=True, negotiated=False)

    client = SimpleNamespace()
    client.responses = SimpleNamespace()
    # First attempt returns None (parse failure), second returns a valid object.
    client.responses.parse = AsyncMock(
        side_effect=[
            SimpleNamespace(output_parsed=None),
            SimpleNamespace(output_parsed=good),
        ]
    )

    with patch.object(voice_extractor.settings, "openai_api_key", "sk-test"), patch.object(
        voice_extractor, "AsyncOpenAI", return_value=client
    ):
        result = asyncio.run(
            voice_extractor.extract_with_schema(
                "transcript", schema, _vendor(), "LED bulb", max_retries=2
            )
        )

    assert result is not None
    assert result.price == 10.0
    assert client.responses.parse.await_count == 2


def test_extract_with_schema_returns_none_when_retries_exhausted():
    schema = _schema()

    client = SimpleNamespace()
    client.responses = SimpleNamespace()
    # Always returns None -> never validates -> retries exhausted.
    client.responses.parse = AsyncMock(return_value=SimpleNamespace(output_parsed=None))

    with patch.object(voice_extractor.settings, "openai_api_key", "sk-test"), patch.object(
        voice_extractor, "AsyncOpenAI", return_value=client
    ):
        result = asyncio.run(
            voice_extractor.extract_with_schema(
                "transcript", schema, _vendor(), "LED bulb", max_retries=1
            )
        )

    assert result is None
    # max_retries=1 -> 2 total attempts.
    assert client.responses.parse.await_count == 2


# --------------------------------------------------------------------------
# schema_from_call: pull a serialized schema off a VoiceCallResult (Task 6)
# --------------------------------------------------------------------------

from app.models.schemas import VoiceCallResult


def _call_with_metadata(metadata) -> VoiceCallResult:
    return VoiceCallResult(
        vendor=_vendor(),
        call_id="call-1",
        provider="pipecat",
        status="completed",
        provider_metadata=metadata,
    )


def test_schema_from_call_parses_serialized_schema():
    schema = _schema()
    call = _call_with_metadata(
        {"campaign_config": {"extraction_schema": schema.model_dump()}}
    )
    parsed = voice_extractor.schema_from_call(call)
    assert parsed is not None
    assert parsed.schema_version == "v1-test"
    assert {f.name for f in parsed.fields} == {f.name for f in schema.fields}


def test_schema_from_call_none_when_flag_off():
    # Flag off => no extraction_schema key in campaign_config.
    call = _call_with_metadata({"campaign_config": {"slot_order": ["price"]}})
    assert voice_extractor.schema_from_call(call) is None


def test_schema_from_call_none_when_no_metadata():
    assert voice_extractor.schema_from_call(_call_with_metadata(None)) is None


def test_schema_from_call_none_on_parse_error():
    # Malformed serialized schema => parse error => None (graceful).
    call = _call_with_metadata(
        {"campaign_config": {"extraction_schema": {"not": "a valid schema"}}}
    )
    assert voice_extractor.schema_from_call(call) is None


# --------------------------------------------------------------------------
# Property 8: versioning totality (Task 10.1)
# --------------------------------------------------------------------------
# Every record extracted via the schema-driven path carries a non-empty
# schema_version AND model_version, and schema_version is independent of the
# model name (schema_version != model_version, and != the model string).

from hypothesis import HealthCheck, given, settings as hyp_settings, strategies as st

# Realistic extraction model names. None of these collide with a "v1-..." schema
# version, so schema_version is independent of the model by construction.
_MODEL_NAMES = ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1-preview", "gpt-4.1"]


def _schema_with_version(version: str) -> ExtractionSchema:
    return ExtractionSchema(
        schema_version=version,
        fields=[
            ExtractionField(name="price", type="number", is_core=True),
            ExtractionField(name="availability", type="boolean", is_core=True),
            ExtractionField(name="negotiated", type="boolean", is_core=True),
            ExtractionField(name="wattage", type="number", is_core=False),
        ],
    )


# Feature: dynamic-extraction-schema, Property 8
@hyp_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    # Model-independent schema version in the real "v1-<hash>" shape.
    version_suffix=st.text(alphabet="0123456789abcdef", min_size=4, max_size=16),
    model=st.sampled_from(_MODEL_NAMES),
    price=st.floats(min_value=1.0, max_value=1e7, allow_nan=False, allow_infinity=False),
    availability=st.booleans(),
    negotiated=st.booleans(),
    populate_dynamic=st.booleans(),
)
def test_versioning_totality_on_schema_path(
    version_suffix, model, price, availability, negotiated, populate_dynamic
):
    """Every schema-driven extraction stamps a non-empty schema_version and
    model_version, with schema_version independent of the model name.

    Validates: Requirements 7.1, 7.2
    """
    schema_version = f"v1-{version_suffix}"
    schema = _schema_with_version(schema_version)
    Model = voice_extractor.build_dynamic_model(schema)
    kwargs = {"price": price, "availability": availability, "negotiated": negotiated}
    if populate_dynamic:
        kwargs["wattage"] = 42.0
    parsed = Model(**kwargs)
    client = _mock_client_returning(parsed)

    with patch.object(voice_extractor.settings, "openai_api_key", "sk-test"), patch.object(
        voice_extractor, "AsyncOpenAI", return_value=client
    ):
        result = asyncio.run(
            voice_extractor.extract_with_schema(
                "agent: price? vendor: ...", schema, _vendor(), "LED bulb", model=model
            )
        )

    assert result is not None
    # Both versions present and non-empty.
    assert result.schema_version
    assert result.model_version
    assert result.schema_version == schema_version
    assert result.model_version == model
    # schema_version is independent of the model (Property 8 core invariant).
    assert result.schema_version != result.model_version
    assert result.schema_version != model


# Feature: dynamic-extraction-schema, Property 8
def test_versioning_defaults_to_settings_model_when_unspecified():
    """When no model override is passed, model_version is the resolved extraction
    model from settings (still non-empty, still != schema_version).

    Validates: Requirements 7.1, 7.2
    """
    schema = _schema_with_version("v1-deadbeef")
    Model = voice_extractor.build_dynamic_model(schema)
    parsed = Model(price=1500.0, availability=True, negotiated=False)
    client = _mock_client_returning(parsed)

    with patch.object(voice_extractor.settings, "openai_api_key", "sk-test"), patch.object(
        voice_extractor.settings, "dynamic_extraction_model", "gpt-4o"
    ), patch.object(voice_extractor, "AsyncOpenAI", return_value=client):
        result = asyncio.run(
            voice_extractor.extract_with_schema(
                "transcript", schema, _vendor(), "LED bulb"
            )
        )

    assert result is not None
    assert result.model_version == "gpt-4o"
    assert result.schema_version == "v1-deadbeef"
    assert result.schema_version != result.model_version


# Feature: dynamic-extraction-schema, Property 8
def test_versioning_present_via_extract_from_call_result_schema_path():
    """The end-to-end schema path (extract_from_call_result, flag on + schema)
    yields a result stamped with non-empty schema_version + model_version.

    Validates: Requirements 7.1, 7.2
    """
    schema = _schema_with_version("v1-abc123")
    Model = voice_extractor.build_dynamic_model(schema)
    parsed = Model(price=2500.0, availability=True, negotiated=True, wattage=9.0)
    client = _mock_client_returning(parsed)

    call = VoiceCallResult(
        vendor=_vendor(),
        call_id="call-ver-1",
        provider="pipecat",
        status="completed",
        transcript="agent: price? vendor: 2500 rupees",
    )

    with patch.object(voice_extractor.settings, "dynamic_extraction_enabled", True), patch.object(
        voice_extractor.settings, "openai_api_key", "sk-test"
    ), patch.object(voice_extractor.settings, "dynamic_extraction_model", "gpt-4o"), patch.object(
        voice_extractor, "AsyncOpenAI", return_value=client
    ), patch.object(
        voice_extractor, "record_call_for_reliability", new=AsyncMock()
    ):
        result = asyncio.run(
            voice_extractor.extract_from_call_result(
                call, "LED bulb", extraction_schema=schema
            )
        )

    assert result is not None
    assert result.schema_version == "v1-abc123"
    assert result.model_version == "gpt-4o"
    assert result.schema_version != result.model_version


# Feature: dynamic-extraction-schema, Property 8 (sanity / contrast)
def test_versioning_absent_on_fixed_extractor_flag_off_path():
    """Sanity: the fixed-extractor path (flag off) leaves schema_version and
    model_version as None — versioning is specific to the schema-driven path.

    Validates: Requirements 7.1, 7.2
    """
    call = VoiceCallResult(
        vendor=VendorInfo(name="Acme", phone="+910000000000", address="1 Main St"),
        call_id="call-ver-2",
        provider="pipecat",
        status="completed",
        transcript="vendor: 1500 rupees, in stock",
    )

    with patch.object(voice_extractor.settings, "dynamic_extraction_enabled", False), patch.object(
        voice_extractor.settings, "openai_api_key", ""
    ), patch.object(voice_extractor, "record_call_for_reliability", new=AsyncMock()):
        result = asyncio.run(
            voice_extractor.extract_from_call_result(call, "LED bulb")
        )

    assert result is not None
    assert result.schema_version is None
    assert result.model_version is None
