"""Tests for search spec extraction and numeric-aware re-ranking."""

from __future__ import annotations

from app.services import product_relevance, search_specs


def test_enrich_bare_capacity_after_question() -> None:
    history = [
        {"role": "assistant", "content": "What capacity do you need?"},
        {"role": "user", "content": "10000"},
    ]
    specs = search_specs.enrich_search_specs([], conversation_history=history)
    assert "10000" in specs

    history = [
        {"role": "assistant", "content": "What capacity power bank do you need?"},
        {"role": "user", "content": "10000 mAh"},
        {"role": "assistant", "content": "Which city?"},
        {"role": "user", "content": "Delhi"},
    ]
    specs = search_specs.enrich_search_specs([], conversation_history=history)
    assert any("10000" in item for item in specs)


def test_numeric_penalty_prefers_matching_capacity() -> None:
    low = search_specs.numeric_spec_penalty(["10000 mAh"], "Anker 10000mAh Power Bank")
    high = search_specs.numeric_spec_penalty(["10000 mAh"], "Small 1000 mAh Power Bank")
    assert low < high


def test_rank_key_orders_by_capacity_within_tier() -> None:
    titles = [
        "Mi 1000 mAh Power Bank",
        "Anker 10000 mAh Power Bank",
        "boAt 10000mAh Fast Charge Power Bank",
    ]
    ranked = sorted(
        titles,
        key=lambda title: search_specs.rank_key(
            "power bank",
            title,
            search_specs=["10000 mAh"],
            match_tier_fn=product_relevance.match_tier,
        ),
    )
    assert ranked[0] != "Mi 1000 mAh Power Bank"
    assert "10000" in ranked[0]


def test_parse_numeric_specs_bare_number() -> None:
    parsed = search_specs.parse_numeric_specs(["10000"])
    assert len(parsed) == 1
    assert parsed[0].value == 10000.0


def test_optional_search_tokens_include_specs() -> None:
    tokens = search_specs.optional_search_tokens([], ["10000 mAh", "Anker"])
    assert "10000" in tokens
    assert "anker" in tokens
