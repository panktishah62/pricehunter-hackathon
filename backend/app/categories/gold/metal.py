from __future__ import annotations

import re
from typing import Any


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def infer_bullion_metal(text: str) -> str:
    """Return 'silver' or 'gold' from buyer or script text. Silver wins if named."""
    normalized = _normalize_text(text)
    if re.search(r"\b(silver|xag|slv)\b", normalized):
        return "silver"
    return "gold"


def snapshot_metal(snapshot: dict[str, Any]) -> str:
    product_type = str(snapshot.get("product_type") or "").strip().lower()
    if product_type in {"silver", "gold"}:
        return product_type
    return infer_bullion_metal(
        f"{snapshot.get('script_name') or ''} {snapshot.get('purity') or ''}"
    )


def dedupe_rate_snapshots(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one row per script/price/purity. Pollers often store the same board many times."""
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for snapshot in snapshots or []:
        key = (
            str(snapshot.get("script_name") or "").strip().lower(),
            snapshot.get("sell_rate"),
            str(snapshot.get("purity") or "").strip().lower(),
            snapshot.get("quantity_grams"),
        )
        if key not in unique:
            unique[key] = snapshot
            order.append(key)
    return [unique[key] for key in order][:150]
