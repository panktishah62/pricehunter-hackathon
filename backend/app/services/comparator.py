from __future__ import annotations

import math
import re

from app.models.schemas import UnifiedResult


def _delivery_to_minutes(value: str | None) -> float | None:
    if not value:
        return None

    lowered = value.strip().lower()
    if "pickup" in lowered:
        return 0
    if "same day" in lowered:
        return 6 * 60
    if "next morning" in lowered:
        return 12 * 60

    range_match = re.search(r"(\d+)\s*-\s*(\d+)\s*(mins?|minutes|hours?|days?)", lowered)
    if range_match:
        first, second, unit = range_match.groups()
        return _unit_to_minutes((int(first) + int(second)) / 2, unit)

    single_match = re.search(r"(\d+)\s*(mins?|minutes|hours?|days?)", lowered)
    if single_match:
        quantity, unit = single_match.groups()
        return _unit_to_minutes(int(quantity), unit)

    return None


def _unit_to_minutes(quantity: float, unit: str) -> float:
    if unit.startswith("day"):
        return quantity * 1440
    if unit.startswith("hour"):
        return quantity * 60
    return quantity


def _normalize(values: list[float], invert: bool = False) -> list[float]:
    if not values:
        return []
    minimum = min(values)
    maximum = max(values)
    if math.isclose(minimum, maximum):
        return [1.0 for _ in values]

    normalized = [(value - minimum) / (maximum - minimum) for value in values]
    if invert:
        return [1 - value for value in normalized]
    return normalized


def rank(
    results: list[UnifiedResult],
    intent: str,
    *,
    category: str | None = None,
) -> list[UnifiedResult]:
    if not results:
        return []

    prices = [result.price if result.price is not None else float("inf") for result in results]
    finite_prices = [price for price in prices if math.isfinite(price)]
    max_finite_price = max(finite_prices) if finite_prices else 1.0
    normalized_prices = _normalize(
        [price if math.isfinite(price) else max_finite_price * 1.2 for price in prices],
        invert=True,
    )

    delivery_values = [_delivery_to_minutes(result.delivery_time) for result in results]
    observed_deliveries = [value for value in delivery_values if value is not None]
    fallback_delivery = sorted(observed_deliveries)[len(observed_deliveries) // 2] if observed_deliveries else 240.0
    normalized_delivery = _normalize(
        [value if value is not None else fallback_delivery for value in delivery_values],
        invert=True,
    )

    confidence_values = [max(0.0, min(result.confidence, 1.0)) for result in results]
    availability_values = [1.0 if result.availability else 0.0 for result in results]
    offline_bonus_values = [1.0 if result.source_type == "offline" else 0.0 for result in results]
    db_backed_values = [1.0 if result.result_type in {"vendor_offering", "vendor_capability"} else 0.0 for result in results]
    negotiated_values = [1.0 if result.negotiated else 0.0 for result in results]

    cat_norm = (category or "").strip().lower()

    if cat_norm == "electronics":
        # Electronics tilt: weight calibrated confidence (vendor reliability)
        # higher than the generic blend, since live SerpAPI prices are already
        # very tight and reliability is the strongest discriminator we have
        # for offline calls.
        # User priority order: source/reliability > price > delivery,
        # so for best_value we put confidence ahead of price outright.
        scoring_map = {
            "cheapest": lambda idx: (
                normalized_prices[idx] * 0.55
                + confidence_values[idx] * 0.30
                + availability_values[idx] * 0.15
            ),
            "fastest": lambda idx: (
                normalized_delivery[idx] * 0.45
                + confidence_values[idx] * 0.25
                + normalized_prices[idx] * 0.20
                + availability_values[idx] * 0.10
            ),
            "best_value": lambda idx: (
                confidence_values[idx] * 0.45
                + normalized_prices[idx] * 0.25
                + normalized_delivery[idx] * 0.15
                + negotiated_values[idx] * 0.10
                + availability_values[idx] * 0.05
            ),
            "nearest": lambda idx: (
                offline_bonus_values[idx] * 0.30
                + confidence_values[idx] * 0.25
                + normalized_delivery[idx] * 0.25
                + normalized_prices[idx] * 0.20
            ),
        }
    else:
        scoring_map = {
            "cheapest": lambda idx: (
                normalized_prices[idx] * 0.60
                + confidence_values[idx] * 0.20
                + availability_values[idx] * 0.20
            ),
            "fastest": lambda idx: (
                normalized_delivery[idx] * 0.50
                + normalized_prices[idx] * 0.20
                + confidence_values[idx] * 0.15
                + availability_values[idx] * 0.15
            ),
            "best_value": lambda idx: (
                normalized_prices[idx] * 0.35
                + confidence_values[idx] * 0.25
                + normalized_delivery[idx] * 0.20
                + negotiated_values[idx] * 0.10
                + availability_values[idx] * 0.10
            ),
            "nearest": lambda idx: (
                offline_bonus_values[idx] * 0.30
                + normalized_delivery[idx] * 0.30
                + normalized_prices[idx] * 0.20
                + confidence_values[idx] * 0.20
            ),
        }

    scorer = scoring_map.get(intent, scoring_map["best_value"])
    scored = []
    for index, result in enumerate(results):
        score = scorer(index)
        if result.negotiated:
            score *= 1.05
        if db_backed_values[index]:
            score *= 1.08
        scored.append((score, result))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored]
