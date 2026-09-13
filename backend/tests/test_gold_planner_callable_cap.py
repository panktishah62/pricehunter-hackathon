"""Regression test for gold bullion vendor shortlisting.

Bug: `build_gold_query_plan` concatenated three vendor buckets and capped the
result at `MAX_DISCOVERED_VENDORS` (20). The `high_confidence_live` bucket can
hold ~120 vendors and was ordered by confidence only. Because every bullion
vendor has confidence >= 60, callable (phone-bearing) vendors could sit well
past position 20 behind website-only vendors, so the [:20] slice dropped every
vendor the voice/outreach flows can actually call -> Voice Lab showed 0 vendors.

Fix: float `call_available` vendors to the front of each bucket (stable sort)
before applying the cap. This test locks that behaviour in.

Run from the backend directory:  python -m pytest tests/ -q
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.categories.gold import planner as gold_planner
from app.categories.gold.ingestion import GOLD_BULLION_CATEGORY_ID
from app.categories.gold.live_rates import LiveRateRefreshSummary
from app.models.schemas import StructuredQuery

CITY = "Bangalore"
WEBSITE_ONLY_COUNT = 25  # comfortably exceeds MAX_DISCOVERED_VENDORS (20)
CALLABLE_COUNT = 5
NOW = datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Minimal in-memory stand-ins for the motor collections the planner reads.
# Supports the only operators the planner uses: plain equality, $ne, $in.
# --------------------------------------------------------------------------
def _matches(doc: dict, flt: dict) -> bool:
    for key, cond in flt.items():
        value = doc.get(key)
        if isinstance(cond, dict):
            for op, target in cond.items():
                if op == "$ne" and value == target:
                    return False
                if op == "$in" and value not in target:
                    return False
                if op == "$nin" and value in target:
                    return False
        elif value != cond:
            return False
    return True


class _FakeCursor:
    def __init__(self, docs: list[dict]):
        self._docs = list(docs)

    def sort(self, field: str, direction: int = 1) -> "_FakeCursor":
        self._docs.sort(key=lambda d: d.get(field), reverse=direction == -1)
        return self

    async def to_list(self, length: int | None = None) -> list[dict]:
        return self._docs if length is None else self._docs[:length]


class _FakeCollection:
    def __init__(self, docs: list[dict]):
        self._docs = list(docs)

    def find(self, flt: dict | None = None, projection: dict | None = None) -> _FakeCursor:
        flt = flt or {}
        return _FakeCursor([d for d in self._docs if _matches(d, flt)])


def _category_link(vendor_id: str, confidence: int) -> dict:
    return {
        "vendor_id": vendor_id,
        "category_id": GOLD_BULLION_CATEGORY_ID,
        "confidence_score": confidence,
        "is_active": True,
        "blacklisted": False,
    }


def _vendor(vendor_id: str, *, phone: str | None) -> dict:
    doc = {
        "vendor_id": vendor_id,
        "name": vendor_id,
        "address": f"{CITY} address",
        "city": CITY,
        "website": f"https://{vendor_id}.example.com",
        "is_active": True,
    }
    if phone:
        doc["phone_primary"] = phone
    return doc


def _website_channel(vendor_id: str) -> dict:
    return {
        "vendor_id": vendor_id,
        "category_id": GOLD_BULLION_CATEGORY_ID,
        "channel_type": "website",
        "channel_value": f"https://{vendor_id}.example.com",
        "is_active": True,
    }


def _build_dataset() -> dict[str, _FakeCollection]:
    category_docs: list[dict] = []
    vendors: list[dict] = []
    channels: list[dict] = []

    # 1 same-city live vendor (snapshot-backed, website-only, NO phone).
    sclive_id = "sclive-00"
    category_docs.append(_category_link(sclive_id, confidence=95))
    vendors.append(_vendor(sclive_id, phone=None))
    channels.append(_website_channel(sclive_id))
    snapshots = [
        {
            "vendor_id": sclive_id,
            "category_id": GOLD_BULLION_CATEGORY_ID,
            "city": CITY,
            "script_name": "GOLD 999",
            "source_name": "test",
            "sell_rate": 75000.0,
            "buy_rate": 74800.0,
            "product_type": "gold",
            "purity": "999",
            "is_active": True,
            "fetched_at": NOW,
            "rate_timestamp": NOW,
        }
    ]

    # Website-only high-confidence vendors (higher confidence => sorted ahead).
    for i in range(WEBSITE_ONLY_COUNT):
        vid = f"web-{i:02d}"
        category_docs.append(_category_link(vid, confidence=90))
        vendors.append(_vendor(vid, phone=None))
        channels.append(_website_channel(vid))

    # Callable high-confidence vendors: have BOTH a website and a phone, but
    # lower confidence so the raw ordering buries them behind the website-only
    # vendors (this is the condition that produced 0 callable vendors).
    for i in range(CALLABLE_COUNT):
        vid = f"call-{i:02d}"
        category_docs.append(_category_link(vid, confidence=61))
        vendors.append(_vendor(vid, phone=f"90000000{i:02d}"))
        channels.append(_website_channel(vid))

    return {
        "links": _FakeCollection(category_docs),
        "vendors": _FakeCollection(vendors),
        "channels": _FakeCollection(channels),
        "snapshots": _FakeCollection(snapshots),
    }


@pytest.fixture
def patched_planner(monkeypatch):
    data = _build_dataset()
    monkeypatch.setattr(gold_planner, "zwig_vendor_category_links_collection", data["links"])
    monkeypatch.setattr(gold_planner, "zwig_vendors_collection", data["vendors"])
    monkeypatch.setattr(gold_planner, "zwig_vendor_channels_collection", data["channels"])
    monkeypatch.setattr(gold_planner, "zwig_live_rate_snapshots_collection", data["snapshots"])

    async def _no_network_refresh(_query):
        return LiveRateRefreshSummary()

    monkeypatch.setattr(gold_planner, "refresh_website_live_rates_for_query", _no_network_refresh)
    return data


def _run_plan():
    query = StructuredQuery(
        product="24K gold coin 10g",
        category="gold",
        route_handler="gold.bullion",
        location="Bengaluru",
        intent="cheapest",
        raw_query="24K gold coin 10g in Bengaluru",
    )
    return asyncio.run(gold_planner.build_gold_query_plan(query))


def test_callable_vendors_survive_the_discovery_cap(patched_planner):
    """All callable vendors must be retained despite >20 website-only vendors ahead."""
    # Sanity: the scenario actually exercises the cap.
    assert WEBSITE_ONLY_COUNT > gold_planner.MAX_DISCOVERED_VENDORS

    plan = _run_plan()

    assert len(plan.discovered_vendors) == gold_planner.MAX_DISCOVERED_VENDORS

    discovered_ids = {v.vendor_id for v in plan.discovered_vendors}
    callable_ids = {f"call-{i:02d}" for i in range(CALLABLE_COUNT)}

    # Every callable vendor survives the cap (regression: this was 0).
    assert callable_ids <= discovered_ids

    callable_in_shortlist = [v for v in plan.discovered_vendors if v.phone]
    assert len(callable_in_shortlist) == CALLABLE_COUNT

    # outreach_vendors (call_available subset) is what voice/whatsapp flows use.
    assert len(plan.outreach_vendors) == CALLABLE_COUNT


def test_callable_vendors_are_floated_to_front_of_their_bucket(patched_planner):
    """Within the shortlist, callable vendors should precede website-only ones
    from the same bucket (stable sort keeps confidence order within each group)."""
    plan = _run_plan()

    # First entry is the same-city-live vendor (no phone), then callable vendors
    # ahead of the website-only high-confidence vendors.
    non_same_city = [v for v in plan.discovered_vendors if v.bucket != "same_city_live"]
    first_callable_block = non_same_city[:CALLABLE_COUNT]
    assert all(v.phone for v in first_callable_block)
    assert {v.vendor_id for v in first_callable_block} == {f"call-{i:02d}" for i in range(CALLABLE_COUNT)}
