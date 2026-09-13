"""Property-based tests for dynamic-extraction field sanitation/normalization.

Covers the correctness properties from the dynamic-extraction-schema design:

  * Property 5 - name sanitation is safe and idempotent; ``normalize_schema``
    never emits duplicate or empty field names.
  * Property 4 - the dynamic-field cap is respected and the three core fields
    are always retained, even when the LLM proposes more fields than the cap.

Run from the backend directory:  python -m pytest test_extraction_schema.py -q
"""
from __future__ import annotations

import re

from hypothesis import given, settings as hyp_settings, strategies as st

from app.services.extraction_schema import (
    CORE_FIELD_NAMES,
    sanitize_field_name,
    normalize_schema,
)

_VALID_NAME_RE = re.compile(r"^[a-z0-9_]+$")

# Core field names that must always be present after normalization.
EXPECTED_CORE_NAMES = {"price", "availability", "negotiated"}


# Arbitrary, possibly-hostile text for field names: unicode, punctuation,
# whitespace, control chars, and the empty string are all in range.
_arbitrary_text = st.text(max_size=80)

_field_types = st.sampled_from(["string", "number", "boolean", "enum", "weird", ""])


@st.composite
def _raw_field(draw):
    """An untrusted LLM-proposed field dict with arbitrary keys/values."""
    return {
        "name": draw(_arbitrary_text),
        "type": draw(_field_types),
        "description": draw(st.one_of(st.none(), _arbitrary_text)),
        "enum_values": draw(
            st.one_of(st.none(), st.lists(_arbitrary_text, max_size=5))
        ),
    }


_raw_field_lists = st.lists(_raw_field(), max_size=30)


# --------------------------------------------------------------------------
# Property 5: sanitation is safe and idempotent
# --------------------------------------------------------------------------
# Feature: dynamic-extraction-schema, Property 5
@hyp_settings(max_examples=100)
@given(raw=_arbitrary_text)
def test_sanitize_is_safe_and_idempotent(raw):
    """sanitize_field_name returns None or a valid identifier, idempotently.

    Validates: Requirements 8.3
    """
    once = sanitize_field_name(raw)

    # Safety: output is either None or matches the restricted alphabet.
    if once is not None:
        assert _VALID_NAME_RE.match(once), once
        assert once == once.strip("_")  # no leading/trailing underscores
        assert len(once) <= 64

    # Idempotence: sanitizing an already-sanitized value is a no-op
    # (handling the None case explicitly).
    twice = sanitize_field_name(once) if once is not None else None
    assert twice == once


# Feature: dynamic-extraction-schema, Property 5
@hyp_settings(max_examples=100)
@given(raw_fields=_raw_field_lists)
def test_normalize_has_no_duplicate_or_empty_names(raw_fields):
    """normalize_schema yields only valid, unique, non-empty field names.

    Validates: Requirements 8.3
    """
    schema = normalize_schema(raw_fields, max_fields=12)

    names = [f.name for f in schema.fields]

    # No empties / Nones.
    assert all(isinstance(n, str) and n for n in names)
    # Every name is a valid sanitized identifier.
    assert all(_VALID_NAME_RE.match(n) for n in names)
    # No duplicates.
    assert len(names) == len(set(names))
    # schema_version is present and model-independent (a v1- hash prefix).
    assert schema.schema_version.startswith("v1-")


# --------------------------------------------------------------------------
# Property 4: field cap respected, core fields always retained
# --------------------------------------------------------------------------
# Feature: dynamic-extraction-schema, Property 4
@hyp_settings(max_examples=100)
@given(raw_fields=_raw_field_lists, max_fields=st.integers(min_value=0, max_value=20))
def test_field_cap_and_core_retention(raw_fields, max_fields):
    """Dynamic count <= max_fields; the three core fields are always present.

    Validates: Requirements 8.2
    """
    schema = normalize_schema(raw_fields, max_fields=max_fields)

    dynamic = [f for f in schema.fields if not f.is_core]
    core = [f for f in schema.fields if f.is_core]
    core_names = {f.name for f in core}

    # Cap: never more dynamic fields than allowed.
    assert len(dynamic) <= max_fields

    # Core retention: all three core fields are present even when truncating,
    # with their fixed types, and they are not counted against the cap.
    assert EXPECTED_CORE_NAMES <= core_names
    assert EXPECTED_CORE_NAMES == set(CORE_FIELD_NAMES)
    by_name = {f.name: f for f in core}
    assert by_name["price"].type == "number"
    assert by_name["availability"].type == "boolean"
    assert by_name["negotiated"].type == "boolean"


# Feature: dynamic-extraction-schema, Property 4
@hyp_settings(max_examples=100)
@given(n_fields=st.integers(min_value=0, max_value=40), max_fields=st.integers(min_value=0, max_value=15))
def test_core_present_even_when_input_would_truncate(n_fields, max_fields):
    """Distinct valid fields beyond the cap are truncated; core still present.

    Validates: Requirements 8.2
    """
    # Generate distinct, already-valid dynamic field names so that the only
    # thing limiting the count is the cap (not sanitation/dedup).
    raw_fields = [{"name": f"field_{i}", "type": "string"} for i in range(n_fields)]

    schema = normalize_schema(raw_fields, max_fields=max_fields)

    dynamic = [f for f in schema.fields if not f.is_core]
    core_names = {f.name for f in schema.fields if f.is_core}

    assert len(dynamic) == min(n_fields, max_fields)
    assert EXPECTED_CORE_NAMES <= core_names


# --------------------------------------------------------------------------
# Property 9 support / Req 3.1, 3.4, 4.2: dynamic Pydantic model build
# --------------------------------------------------------------------------
import pytest
from pydantic import ValidationError

from app.models.schemas import ExtractionField, ExtractionSchema
from app.services.extraction_schema import build_dynamic_model

# Already-valid (sanitizable) dynamic field names so the only constraints under
# test are typing/optionality, not sanitation.
_valid_dynamic_name = st.from_regex(r"[a-z][a-z0-9_]{0,20}", fullmatch=True).filter(
    lambda n: n.strip("_") not in EXPECTED_CORE_NAMES and n.strip("_") != ""
)

_dynamic_type = st.sampled_from(["string", "number", "boolean", "enum"])


@st.composite
def _schema_with_dynamic_fields(draw):
    """A normalized-style schema: the three core fields + arbitrary dynamics."""
    names = draw(
        st.lists(_valid_dynamic_name, min_size=0, max_size=6, unique=True)
    )
    dynamic_fields = []
    for name in names:
        ftype = draw(_dynamic_type)
        enum_values = None
        if ftype == "enum":
            enum_values = draw(
                st.lists(
                    st.from_regex(r"[A-Za-z0-9 ]{1,10}", fullmatch=True),
                    min_size=1,
                    max_size=4,
                    unique=True,
                )
            )
        dynamic_fields.append(
            ExtractionField(
                name=name, type=ftype, enum_values=enum_values, is_core=False
            )
        )

    core = [
        ExtractionField(name="price", type="number", is_core=True),
        ExtractionField(name="availability", type="boolean", is_core=True),
        ExtractionField(name="negotiated", type="boolean", is_core=True),
    ]
    return ExtractionSchema(schema_version="v1-test", fields=core + dynamic_fields)


# Feature: dynamic-extraction-schema, Property 9 support
@hyp_settings(max_examples=100)
@given(schema=_schema_with_dynamic_fields())
def test_build_dynamic_model_core_required_dynamic_optional(schema):
    """Core fields are required; dynamic fields are optional and default None.

    Constructing with only core fields validates fine and every dynamic field
    is omitted/None (Property 9 support).

    Validates: Requirements 3.1, 3.4, 4.2
    """
    Model = build_dynamic_model(schema)

    # Core fields are required on the built model.
    required = {n for n, f in Model.model_fields.items() if f.is_required()}
    assert required == EXPECTED_CORE_NAMES

    # Core-only construction (all dynamic fields omitted) validates fine.
    instance = Model(price=199.0, availability=True, negotiated=False)
    for field in schema.fields:
        if not field.is_core:
            assert getattr(instance, field.name) is None


# Feature: dynamic-extraction-schema, Property 9 support
def test_build_dynamic_model_missing_core_raises():
    """Omitting any required core field raises ValidationError (Req 3.1).

    Validates: Requirements 3.1
    """
    schema = ExtractionSchema(
        schema_version="v1-core",
        fields=[
            ExtractionField(name="price", type="number", is_core=True),
            ExtractionField(name="availability", type="boolean", is_core=True),
            ExtractionField(name="negotiated", type="boolean", is_core=True),
            ExtractionField(name="brand", type="string", is_core=False),
        ],
    )
    Model = build_dynamic_model(schema)

    # All core present -> ok.
    Model(price=10.0, availability=True, negotiated=False)

    # Drop each core field in turn -> ValidationError.
    base = {"price": 10.0, "availability": True, "negotiated": False}
    for missing in EXPECTED_CORE_NAMES:
        kwargs = {k: v for k, v in base.items() if k != missing}
        with pytest.raises(ValidationError):
            Model(**kwargs)


# Feature: dynamic-extraction-schema, Property 9 support
def test_build_dynamic_model_enum_constrains_values():
    """Enum dynamic fields accept in-set and reject out-of-set values (Req 4.2).

    Validates: Requirements 4.2, 3.4
    """
    schema = ExtractionSchema(
        schema_version="v1-enum",
        fields=[
            ExtractionField(name="price", type="number", is_core=True),
            ExtractionField(name="availability", type="boolean", is_core=True),
            ExtractionField(name="negotiated", type="boolean", is_core=True),
            ExtractionField(
                name="purity",
                type="enum",
                enum_values=["22K", "24K"],
                is_core=False,
            ),
        ],
    )
    Model = build_dynamic_model(schema)
    core = {"price": 10.0, "availability": True, "negotiated": False}

    # In-set value accepted.
    assert Model(**core, purity="22K").purity == "22K"
    # Omitted (optional) validates fine.
    assert Model(**core).purity is None
    # Out-of-set value rejected.
    with pytest.raises(ValidationError):
        Model(**core, purity="18K")


# Feature: dynamic-extraction-schema, Property 9 support
def test_build_dynamic_model_enum_without_values_falls_back_to_str():
    """An enum field with no usable enum_values degrades to a free string.

    Validates: Requirements 4.2
    """
    schema = ExtractionSchema(
        schema_version="v1-enum-empty",
        fields=[
            ExtractionField(name="price", type="number", is_core=True),
            ExtractionField(name="availability", type="boolean", is_core=True),
            ExtractionField(name="negotiated", type="boolean", is_core=True),
            ExtractionField(name="finish", type="enum", enum_values=None, is_core=False),
        ],
    )
    Model = build_dynamic_model(schema)
    core = {"price": 10.0, "availability": True, "negotiated": False}

    # Any string is accepted (no constraint), and it remains optional.
    assert Model(**core, finish="matte").finish == "matte"
    assert Model(**core).finish is None


# --------------------------------------------------------------------------
# Req 8.2/8.3: field descriptions are length-bounded
# --------------------------------------------------------------------------
from app.services.extraction_schema import MAX_FIELD_DESCRIPTION_LENGTH


# Feature: dynamic-extraction-schema, Req 8.2/8.3
@hyp_settings(max_examples=100)
@given(desc=st.text(max_size=4000))
def test_description_is_length_bounded(desc):
    """An LLM-proposed description is truncated to MAX_FIELD_DESCRIPTION_LENGTH.

    Descriptions are injected verbatim into the extraction prompt, so an
    unbounded/adversarial description must not bloat the prompt.

    Validates: Requirements 8.2, 8.3
    """
    schema = normalize_schema(
        [{"name": "wattage", "type": "number", "description": desc}],
        max_fields=12,
    )
    field = next((f for f in schema.fields if f.name == "wattage"), None)
    if field is None or field.description is None:
        # Whitespace-only / empty descriptions are dropped to None — fine.
        return
    assert len(field.description) <= MAX_FIELD_DESCRIPTION_LENGTH


# Feature: dynamic-extraction-schema, Req 8.2/8.3
def test_long_description_truncated_normal_preserved():
    """A long description is capped; a short one is preserved verbatim."""
    long_desc = "x" * 1000
    short_desc = "Wattage of the bulb in watts"
    schema = normalize_schema(
        [
            {"name": "wattage", "type": "number", "description": long_desc},
            {"name": "warranty", "type": "string", "description": short_desc},
        ],
        max_fields=12,
    )
    by_name = {f.name: f for f in schema.fields}
    assert len(by_name["wattage"].description) == MAX_FIELD_DESCRIPTION_LENGTH
    assert by_name["warranty"].description == short_desc
