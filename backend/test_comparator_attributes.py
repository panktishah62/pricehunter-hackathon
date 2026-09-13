"""Comparator stability with the new optional `UnifiedResult` fields (Task 11).

dynamic-extraction-schema added three optional fields to `UnifiedResult`:
`attributes`, `schema_version`, `model_version`. These must NOT perturb
`comparator.rank()`, which sorts purely on the core fields (price, delivery,
availability, confidence, source_type, negotiated).

This is a pure unit test - `comparator.rank()` has no external dependencies.

Run from the backend directory:  python -m pytest test_comparator_attributes.py -q

Validates: Requirements 3.5, 6.4
"""
from __future__ import annotations

from app.models.schemas import UnifiedResult
from app.services import comparator

INTENTS = ["cheapest", "fastest", "best_value", "nearest"]
CATEGORIES = [None, "electronics", "general"]


def _base_results() -> list[dict]:
    """A spread of vendors that exercises price/delivery/confidence/availability."""
    return [
        dict(
            id="r1",
            source_type="offline",
            name="Alpha",
            price=1500.0,
            delivery_time="pickup in 20 minutes",
            availability=True,
            negotiated=True,
            confidence=0.9,
        ),
        dict(
            id="r2",
            source_type="online",
            name="Bravo",
            price=1299.0,
            delivery_time="2-3 days",
            availability=True,
            negotiated=False,
            confidence=0.6,
        ),
        dict(
            id="r3",
            source_type="offline",
            name="Charlie",
            price=1750.0,
            delivery_time="same day",
            availability=False,
            negotiated=False,
            confidence=0.75,
        ),
        dict(
            id="r4",
            source_type="online",
            name="Delta",
            price=999.0,
            delivery_time=None,
            availability=True,
            negotiated=True,
            confidence=0.4,
        ),
        dict(
            id="r5",
            source_type="offline",
            name="Echo",
            price=None,  # missing price -> exercises the inf/fallback path
            delivery_time="next morning",
            availability=True,
            negotiated=False,
            confidence=0.55,
        ),
    ]


def _without_attributes() -> list[UnifiedResult]:
    return [UnifiedResult(**spec) for spec in _base_results()]


def _with_attributes() -> list[UnifiedResult]:
    enriched = []
    for spec in _base_results():
        enriched.append(
            UnifiedResult(
                **spec,
                attributes={"wattage": 60, "warranty": "1 year", "brand": "acme"},
                schema_version="v1-abc123",
                model_version="gpt-4o",
            )
        )
    return enriched


def test_rank_order_identical_with_and_without_attributes():
    """rank() produces the SAME ordering whether or not results carry the new
    optional `attributes`/`schema_version`/`model_version` fields.

    Validates: Requirements 3.5, 6.4
    """
    for category in CATEGORIES:
        for intent in INTENTS:
            ranked_plain = comparator.rank(
                _without_attributes(), intent, category=category
            )
            ranked_enriched = comparator.rank(
                _with_attributes(), intent, category=category
            )

            order_plain = [r.id for r in ranked_plain]
            order_enriched = [r.id for r in ranked_enriched]

            assert order_plain == order_enriched, (
                f"ranking diverged for intent={intent!r} category={category!r}: "
                f"{order_plain} != {order_enriched}"
            )


def test_rank_preserves_all_results_and_attributes_passthrough():
    """rank() returns the same set of results and leaves `attributes` intact
    (the new fields ride along untouched; only ordering is computed on core)."""
    ranked = comparator.rank(_with_attributes(), "best_value", category="electronics")

    assert {r.id for r in ranked} == {"r1", "r2", "r3", "r4", "r5"}
    for r in ranked:
        assert r.attributes == {"wattage": 60, "warranty": "1 year", "brand": "acme"}
        assert r.schema_version == "v1-abc123"
        assert r.model_version == "gpt-4o"


def test_empty_input_returns_empty():
    """Guard: empty input ranks to empty regardless of the new fields."""
    assert comparator.rank([], "cheapest") == []
