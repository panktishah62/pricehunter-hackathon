"""Shared classification + normalization rules for gold live rates.

A neutral module so the search path (`gold_vendor_flow`) and the preview/board
path (`gold_live_rate_preview`) apply IDENTICAL rules without importing each
other's internals.
"""
from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta
from typing import Any

from app.database import zwig_live_rate_snapshots_collection

# A live rate older than this is stale and must not be shown. Bullion feeds
# refresh intraday; multi-day-old broadcasts must not surface as "live".
FRESH_SNAPSHOT_HOURS = 48

# Broadcast scripts that are NOT an INR retail rate (USD/COMEX spot, futures,
# costing/fix reference lines). Never surface these as a vendor's rate.
SPOT_SCRIPT_TOKENS = ("$", "USD", "SPOT", "FUTURE", "COMEX", "MCX", "COSTING", " FIX", "FIX ")

# Curated bespoke adapters — their extraction is vetted, unlike the generic
# "auto" adapter. Used as a contamination-free anchor for outlier detection so
# a city flooded with unreliable auto rates can't drag the consensus.
TRUSTED_RATE_SOURCES = {"chirayu_api", "lmx_broadcastrates", "liverate_js_socket"}

# Sub-bullion jewellery purities. The bullion rate board is 999/995 (24K); a
# 22K/18K/14K rate is a different (cheaper) product and must not sit on it,
# where it would otherwise look like the "cheapest" gold.
NON_BULLION_PURITIES = {"22K", "22 K", "22KT", "916", "18K", "18 K", "18KT", "750", "14K", "14 K", "14KT", "585"}

# A normalized ₹/10g this far from the city consensus is almost certainly a
# mis-parse or a different product (e.g. an unlabelled 18K rate); suppress it.
# Gold trades near-uniformly nationwide, so genuine vendor spread is ~1-2%.
PRICE_OUTLIER_TOLERANCE = 0.12  # ±12%
# Need a few independent rates before a median is a trustworthy consensus.
MIN_CONSENSUS_SAMPLE = 4

# Absolute plausibility band for a 24K bullion rate in ₹/10g. This is a
# defense-in-depth BACKSTOP for the consensus-relative outlier check: when the
# trusted feeds are stale and `national_bullion_consensus()` returns None, the
# relative guard no-ops and would otherwise let a wrong-but-in-DB rate (e.g.
# 82,652) surface as "best". These bounds catch such rates even with no
# consensus. The band is deliberately WIDE (real 24K/10g ≈ ₹1.49L today) so it
# only ever rejects clearly-broken values, never a genuine vendor spread.
# NOTE: these are COUPLED to normalize_to_10g's magnitude thresholds (per-gram
# boundary at ₹40k/g, per-kg boundary at ₹40L/10g) and MUST be revisited
# together if 24K gold moves far outside this range. A price normalized UP from
# per-gram lands at 10×; to avoid falsely rejecting a legitimate near-boundary
# per-gram feed, keep PLAUSIBLE_10G_CEILING comfortably below 10×the per-gram
# boundary (10×40k = ₹400k) and PLAUSIBLE_10G_FLOOR comfortably above 10×a
# realistic per-gram floor. At today's ~₹14.9k/g (≈₹1.49L/10g) there is ample
# headroom on both sides.
PLAUSIBLE_10G_FLOOR = 100_000.0
PLAUSIBLE_10G_CEILING = 300_000.0


def is_implausible_gold_10g(value: float | None) -> bool:
    """True if a normalized ₹/10g 24K rate is outside the absolute plausibility
    band. Unlike `is_price_outlier`, this fires WITHOUT a consensus, so a wrong
    rate can't leak while the trusted feeds are stale."""
    if value is None or value <= 0:
        return False  # missing rate is handled elsewhere; don't flag as outlier
    return value < PLAUSIBLE_10G_FLOOR or value > PLAUSIBLE_10G_CEILING


def is_spot_script(script_name: str | None) -> bool:
    name = (script_name or "").upper()
    return any(token in name for token in SPOT_SCRIPT_TOKENS)


def is_bullion_grade(purity: str | None) -> bool:
    """True for bullion-grade (999/995/24K) or unknown purity. False for
    explicit sub-bullion jewellery grades, which don't belong on the board."""
    if not purity:
        return True  # unknown — allowed; the outlier check still guards magnitude
    return purity.strip().upper() not in NON_BULLION_PURITIES


def is_retail_rate(snapshot: dict[str, Any]) -> bool:
    """A usable INR retail rate: NOT a spot/futures/reference line, of a
    bullion grade, AND it identifies a real retail product (a purity like
    999/995 or an explicit weight). Bare 'GOLD' / 'GOLD($)' lines that carry a
    USD/spot value (e.g. 4078.95) have neither and are rejected."""
    if is_spot_script(snapshot.get("script_name")):
        return False
    if not is_bullion_grade(snapshot.get("purity")):
        return False
    if snapshot.get("purity"):
        return True
    qty = snapshot.get("quantity_grams")
    return isinstance(qty, (int, float)) and qty > 0


def consensus_10g(values: list[float | None]) -> float | None:
    """Median ₹/10g of the provided rates, or None if too few to trust."""
    nums = [float(v) for v in values if isinstance(v, (int, float)) and v > 0]
    if len(nums) < MIN_CONSENSUS_SAMPLE:
        return None
    return statistics.median(nums)


def is_price_outlier(
    value: float | None,
    consensus: float | None,
    tolerance: float = PRICE_OUTLIER_TOLERANCE,
) -> bool:
    """True if a normalized ₹/10g rate should be suppressed as an outlier.

    Two independent checks:
      1. Absolute plausibility band — fires even with NO consensus, so a
         wrong-but-in-DB rate can't leak while trusted feeds are stale.
      2. Consensus-relative deviation beyond `tolerance` — no-ops when there
         isn't a trustworthy consensus, so a thin board never suppresses
         everything."""
    if value is None:
        return False
    if is_implausible_gold_10g(value):
        return True
    if consensus is None or consensus <= 0:
        return False
    return abs(value - consensus) / consensus > tolerance



def normalize_to_10g(price: float | None, snapshot: dict[str, Any] | None = None) -> float | None:
    """Normalize a rate to a single ₹/10g basis so vendors are comparable.

    Indian bullion is quoted per 10g almost universally; weight tokens in script
    names ("500Gms and ABOVE", "BELOW 50 GM") are ORDER TIERS, not the quote
    basis — and the snapshot's inferred `unit`/`quantity_grams` are derived from
    those same tokens, so they are unreliable for normalization. We therefore
    infer the basis from MAGNITUDE.

    The thresholds encode today's price level (24K ≈ ₹14,900/g ≈ ₹1.49L/10g):
      - price <    40,000  → quoted per gram   (boundary ~₹40k/g)
      - price < 4,000,000  → quoted per 10g    (boundary ~₹40L/10g)
      - otherwise          → quoted per kg
    NOTE: the ₹40k/g per-gram boundary is only ~2.7x today's rate; if 24K gold
    ever approaches ₹40,000/g these bounds MUST be raised or per-gram feeds will
    be misread as per-10g (10x too low). Revisit then.
    """
    if price is None:
        return None
    if price < 40_000:
        return round(price * 10, 2)
    if price < 4_000_000:
        return round(price, 2)
    return round(price / 100, 2)  # per kg → per 10g


def snapshot_price_10g(snapshot: dict[str, Any]) -> float | None:
    """The snapshot's usable rate normalized to ₹/10g (sell preferred over buy)."""
    for key in ("sell_rate", "buy_rate"):
        value = snapshot.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return normalize_to_10g(float(value), snapshot)
    return None


# Brief in-process cache for the national bullion consensus so we don't hit the
# DB on every gold query. It barely moves intraday, so a short TTL is fine.
_consensus_cache: tuple[float, float | None] | None = None  # (monotonic_ts, value)
_CONSENSUS_TTL_SECONDS = 60


async def national_bullion_consensus(category_id: str) -> float | None:
    """A robust ₹/10g gold consensus from recent CURATED bespoke rates across
    ALL cities. Gold trades near-uniformly nationwide, so this is a valid anchor
    everywhere — and unlike a single thin city query it is always well sampled,
    so the outlier guard can fire even for a city with few trusted vendors of
    its own. Cached briefly to avoid a DB read on every query."""
    global _consensus_cache
    now = time.monotonic()
    if _consensus_cache is not None and now - _consensus_cache[0] < _CONSENSUS_TTL_SECONDS:
        return _consensus_cache[1]
    cutoff = datetime.utcnow() - timedelta(hours=FRESH_SNAPSHOT_HOURS)
    docs = await zwig_live_rate_snapshots_collection.find(
        {
            "category_id": category_id,
            "source_name": {"$in": list(TRUSTED_RATE_SOURCES)},
            "$or": [
                {"rate_timestamp": {"$gte": cutoff}},
                {"rate_timestamp": {"$exists": False}, "fetched_at": {"$gte": cutoff}},
            ],
        },
        {"sell_rate": 1, "buy_rate": 1, "script_name": 1, "purity": 1, "quantity_grams": 1, "product_type": 1},
    ).to_list(length=2000)
    prices: list[float] = []
    for doc in docs:
        if doc.get("product_type") == "silver":
            continue
        if not is_retail_rate(doc):
            continue
        price = snapshot_price_10g(doc)
        if price is not None:
            prices.append(price)
    result = consensus_10g(prices)
    _consensus_cache = (now, result)
    return result
