"""Tests for catalog variance probing (Phase 3)."""

from __future__ import annotations

from app.services import catalog_variance


def test_analyze_title_variance_detects_mah_spread() -> None:
    titles = [
        "Anker 5000 mAh Power Bank",
        "Mi 10000 mAh Power Bank",
        "boAt 20000 mAh Power Bank",
        "Portronics 10000mAh Bank",
        "Zebronics 5000mAh Power Bank",
    ]
    result = catalog_variance.analyze_title_variance(titles)
    assert result.high_variance
    assert result.axes[0].unit == "mah"
    assert result.axes[0].spread_ratio >= 3.0


def test_analyze_title_variance_low_spread() -> None:
    titles = ["Widget A 100 mm", "Widget B 100mm", "Widget C 100 MM"]
    result = catalog_variance.analyze_title_variance(titles)
    assert not result.high_variance


def test_needs_spec_question_before_answer() -> None:
    hint = {
        "high_variance": True,
        "questions_asked": 0,
        "still_high": True,
        "axes": [{"unit": "mah", "dimension": "capacity"}],
    }
    assert catalog_variance.needs_spec_question(hint, conversation_history=[])


def test_needs_spec_question_satisfied_after_mah_answer() -> None:
    hint = {
        "high_variance": True,
        "questions_asked": 1,
        "still_high": True,
        "axes": [{"unit": "mah", "dimension": "capacity"}],
    }
    history = [
        {"role": "assistant", "content": "What capacity?"},
        {"role": "user", "content": "10000 mAh"},
    ]
    assert not catalog_variance.needs_spec_question(hint, conversation_history=history)


def test_build_spec_question_includes_range() -> None:
    hint = {
        "high_variance": True,
        "questions_asked": 0,
        "axes": [
            {
                "dimension": "capacity",
                "unit": "mah",
                "min_value": 5000,
                "max_value": 20000,
                "examples": ["5000", "10000", "20000"],
            }
        ],
    }
    question = catalog_variance.build_spec_question(hint)
    assert question is not None
    assert "capacity" in question.lower()
    assert "5,000" in question or "5000" in question
