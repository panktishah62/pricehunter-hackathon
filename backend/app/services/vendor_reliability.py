"""
Vendor reliability scoring (electronics, generic).

Tracks two Beta-Binomial signals per vendor:
- pickup: did the call connect and stay connected?
- quote:  did the vendor (when picked up and stocked) actually quote a price?

Plus an availability counter and history. Updated in real time after each call.
Decay (exponential) is applied lazily on read so we don't need a separate batch job
in the hot path.

Also exposes a Thompson-sampling helper for selecting which vendors to call next,
and a quote cache with category-specific TTLs.

Schema (collection: pricehunter.vendor_reliability):
    vendor_key            : str (unique)              — same shape as vendor_profiles.vendor_key
    phone                 : str (optional, indexed)
    place_id              : str (optional, sparse index)
    category              : str (e.g. "electronics")
    source                : str (google_places | indiamart | justdial | flash_compare | ...)
    source_prior          : { alpha: float, beta: float } — never changes after creation
    pickup                : { alpha: float, beta: float, last_decay_at: datetime, n_calls: int }
    quote                 : { alpha: float, beta: float, last_decay_at: datetime, n_quotes_attempted: int }
    availability          : { available_count: int, oos_count: int, wrong_product_count: int }
    last_called_at        : datetime
    last_outcome          : str
    daily_call_count      : int
    daily_call_count_date : str (YYYY-MM-DD)
    hard_flags            : { wrong_number, deactivated, manual_review, last_flagged_at }
    created_at            : datetime
    updated_at            : datetime

Schema (collection: pricehunter.vendor_quote_cache):
    cache_key             : str (unique)              — vendor_key + product_key + location_pincode
    vendor_key            : str
    product_key           : str
    location              : { raw, normalized, pincode }
    price                 : float
    currency              : str
    delivery_time         : str | null
    availability          : bool
    notes                 : str | null
    confidence            : float
    expires_at            : datetime (TTL index)
    created_at            : datetime
"""

from __future__ import annotations

import logging
import math
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal, Optional

from pymongo import ASCENDING, DESCENDING

from app.database import db
from app.models.schemas import UnifiedResult, VendorInfo, VoiceCallResult
from app.services.price_parsing import parse_price

logger = logging.getLogger(__name__)

vendor_reliability_collection = db["vendor_reliability"]
vendor_quote_cache_collection = db["vendor_quote_cache"]

# ────────────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────────────

# Source priors: encode "how much do we trust a fresh vendor from this source"
# as a Beta(alpha, beta). Mean = alpha / (alpha + beta).
# Higher alpha+beta = stronger prior = more calls needed to override it.
SOURCE_PRIORS: dict[str, dict[str, float]] = {
    "indiamart": {"alpha": 4.0, "beta": 3.0},          # mean ≈ 0.57
    "justdial":  {"alpha": 3.0, "beta": 3.0},          # mean ≈ 0.50
    "google_places": {"alpha": 4.0, "beta": 3.0},      # mean ≈ 0.57
    "flash_compare": {"alpha": 6.0, "beta": 2.0},      # mean = 0.75 (verified online)
    "amazon":   {"alpha": 8.0, "beta": 1.0},           # online platforms quote reliably
    "flipkart": {"alpha": 8.0, "beta": 1.0},
    "default":  {"alpha": 3.0, "beta": 3.0},           # mean = 0.50
}

# Quote-cache TTL by category (in minutes). Reflects how fast prices move.
QUOTE_CACHE_TTL_MINUTES: dict[str, int] = {
    "gold": 30,
    "electronics": 6 * 60,          # phones, laptops — 6h
    "electronics_accessory": 24 * 60,
    "medicine": 12 * 60,
    "default": 4 * 60,
}

# Weekly decay factor on (alpha, beta). Per-category, since vendor behavior
# drifts at different rates by domain. Half-life is computed as
# ln(0.5) / ln(factor) weeks. Lower factor → faster forgetting.
DECAY_FACTOR_PER_WEEK_BY_CATEGORY: dict[str, float] = {
    "electronics": 0.94,   # ~11 week half-life — phones move fast
    "medicine":    0.95,   # ~13 week half-life
    "gold":        0.97,   # ~22 week half-life — bullion vendors stable
    "default":     0.96,   # ~17 week half-life
}
DECAY_FACTOR_PER_WEEK = DECAY_FACTOR_PER_WEEK_BY_CATEGORY["default"]
DECAY_INTERVAL = timedelta(days=7)


def _decay_factor(category: str | None) -> float:
    return DECAY_FACTOR_PER_WEEK_BY_CATEGORY.get(
        (category or "").strip().lower(),
        DECAY_FACTOR_PER_WEEK_BY_CATEGORY["default"],
    )

# Cooldowns / caps
MAX_DAILY_CALLS_PER_VENDOR = 3
SAME_QUERY_RECALL_BLOCK_HOURS = 24

# Diversity quotas for vendor selection (sums to 1.0)
QUOTA_KNOWN_GOOD = 0.70
QUOTA_EXPLORE = 0.20
QUOTA_RECHECK = 0.10

# Min calls before Thompson sampling treats a vendor as "established"
MIN_CALLS_ESTABLISHED = 3


# ────────────────────────────────────────────────────────────────────────────
# Outcome classification
# ────────────────────────────────────────────────────────────────────────────


CallOutcome = Literal[
    "no_answer",            # busy, no_answer, dropped before connect
    "picked_up_quoted",     # picked up + gave a usable price
    "picked_up_no_quote",   # picked up + has product + refused/can't quote
    "picked_up_no_stock",   # picked up + doesn't carry / out of stock
    "picked_up_wrong_product",  # picked up + wrong fit (sweet shop got iPhone query)
    "picked_up_closed",     # picked up + "we're closed, call later"
    "wrong_number",         # disconnected / wrong number
]


@dataclass(frozen=True)
class ClassifiedOutcome:
    outcome: CallOutcome
    quoted_price: Optional[float] = None
    benchmark_price: Optional[float] = None  # filled by caller if known


_NO_STOCK_PHRASES = (
    "out of stock",
    "stock nahi",
    "stock nahin",
    "khatam",
    "khatm",
    "nahi hai",
    "nahin hai",
    "नहीं है",
    "no stock",
    "not available",
    "currently unavailable",
    "sold out",
)
# NOTE: a bare standalone negation ("नहीं") is intentionally NOT a no-stock
# phrase. It is far too loose — it appears in the agent's own questions
# ("है या नहीं?") and in vendor replies that are not about stock
# ("नहीं भाई, यह तो है"). Phrase matching is additionally restricted to the
# vendor's turns only (see `_vendor_only_text`) so the agent's wording — e.g.
# the gold "wrong number हो गया" closing line — can never trip a label.
_CLOSED_PHRASES = (
    "we're closed",
    "we are closed",
    "shop closed",
    "band hai",
    "बंद है",
    "call back",
    "call later",
    "kal aana",
    "tomorrow",
    "lunch break",
)
_WRONG_NUMBER_PHRASES = (
    "wrong number",
    "galat number",
    "this is not",
    "ye galat",
)
_REFUSE_QUOTE_PHRASES = (
    "whatsapp",
    "visit shop",
    "store visit",
    "aakar dekho",
    "aakar lo",
    "shop pe aao",
    "phone par price nahi",
)


def _vendor_only_text(transcript: str) -> str:
    """Return only the VENDOR's spoken words from a labelled transcript, lowercased.

    Transcripts are labelled with ``Agent:`` / ``Vendor:`` speaker tags (the
    Gemini and cascade paths both emit this shape), either one-per-line OR
    inline on a single line (e.g. "Agent: hai? Vendor: out of stock"). Phrase
    heuristics below must match only what the VENDOR said — matching the agent's
    own lines produced false positives (e.g. the agent asking "है या नहीं?"
    tripping the no-stock list, or the gold closing line "wrong number हो गया"
    tripping wrong_number).

    We split on the speaker labels wherever they occur and keep only the
    segments spoken after a vendor label. When no labels are present
    (older/unlabelled transcripts) we fall back to the whole transcript so
    behavior is unchanged for those.
    """
    if not transcript:
        return ""

    # Speaker tags we recognize. Only VENDOR segments are kept; AGENT segments
    # are skipped. (Agent labels are listed in the regex below for splitting, but
    # we never collect their text.)
    _VENDOR_LABELS = ("vendor", "user", "customer", "seller")

    # Split into (label, text) segments by locating each "<label>:" marker.
    label_re = re.compile(
        r"(?i)\b(agent|bot|assistant|vendor|user|customer|seller)\s*:",
    )
    matches = list(label_re.finditer(transcript))
    if not matches:
        # Unlabelled transcript: cannot separate speakers, use the whole thing.
        return transcript.lower()

    vendor_chunks: list[str] = []
    for idx, match in enumerate(matches):
        label = match.group(1).lower()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(transcript)
        segment = transcript[start:end].strip()
        if label in _VENDOR_LABELS and segment:
            vendor_chunks.append(segment)

    if not vendor_chunks:
        # Labels were present but none were the vendor's (e.g. the agent spoke
        # but the vendor never did, or only agent-side synonyms appeared). Phrase
        # heuristics will see an empty string and the call falls through to
        # picked_up_no_quote — which is correct for "no vendor speech", but log
        # it so the silent-empty case is observable for drift monitoring.
        logger.debug(
            "Transcript had speaker labels but no vendor turns; phrase "
            "heuristics will see empty vendor text."
        )
    return "\n".join(vendor_chunks).lower()


def classify_call_outcome(
    call: VoiceCallResult,
    *,
    quoted_price: float | None = None,
) -> ClassifiedOutcome:
    """Map a VoiceCallResult to a single CallOutcome label.

    Prefers a pre-normalized `quoted_price` (produced by the LLM extraction
    boundary) when the caller supplies one, so we never re-parse noisy
    transcript strings like "52,000 है।". Falls back to parsing
    `extracted_data` only when no normalized price is available.
    """

    status = (call.status or "").strip().lower()
    if status in ("busy", "no_answer", "failed"):
        return ClassifiedOutcome(outcome="no_answer")

    # Phrase heuristics must only see the VENDOR's words, never the agent's
    # questions/closing lines (which previously caused false no-stock /
    # wrong-number labels).
    vendor_transcript = _vendor_only_text(call.transcript or "")
    extracted = call.extracted_data or {}

    if quoted_price is not None and quoted_price <= 0:
        quoted_price = None
    if quoted_price is None:
        # Prefer the pre-normalized numeric field written at extraction time.
        numeric = extracted.get("price_value")
        if isinstance(numeric, (int, float)) and numeric > 0:
            quoted_price = float(numeric)
        else:
            quoted_price = parse_price(
                extracted.get("quoted_price") or extracted.get("price")
            )

    if any(phrase in vendor_transcript for phrase in _WRONG_NUMBER_PHRASES):
        return ClassifiedOutcome(outcome="wrong_number")

    if any(phrase in vendor_transcript for phrase in _CLOSED_PHRASES) and quoted_price is None:
        return ClassifiedOutcome(outcome="picked_up_closed")

    if quoted_price is not None:
        return ClassifiedOutcome(outcome="picked_up_quoted", quoted_price=quoted_price)

    # Extracted data may say availability is False
    extracted_available = extracted.get("product_available")
    if extracted_available is None:
        extracted_available = extracted.get("availability")
    if extracted_available is False:
        return ClassifiedOutcome(outcome="picked_up_no_stock")

    if any(phrase in vendor_transcript for phrase in _NO_STOCK_PHRASES):
        return ClassifiedOutcome(outcome="picked_up_no_stock")

    if any(phrase in vendor_transcript for phrase in _REFUSE_QUOTE_PHRASES):
        return ClassifiedOutcome(outcome="picked_up_no_quote")

    # Picked up but transcript empty / inconclusive → conservative no_quote
    return ClassifiedOutcome(outcome="picked_up_no_quote")


# ────────────────────────────────────────────────────────────────────────────
# Decay helpers (lazy)
# ────────────────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _apply_decay(
    state: dict[str, Any],
    now: datetime,
    *,
    category: str | None = None,
) -> dict[str, Any]:
    """Apply weekly exponential decay to (alpha, beta) since last_decay_at.

    Decay is multiplicative on both, so the *mean* doesn't shift, only the
    confidence (sharpness) of the posterior shrinks. This makes recent
    evidence dominate without artificially flipping the mean. Decay rate
    varies by category — see DECAY_FACTOR_PER_WEEK_BY_CATEGORY.
    """
    last = state.get("last_decay_at")
    if not isinstance(last, datetime):
        state["last_decay_at"] = now
        return state
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    weeks = max(0.0, (now - last).total_seconds() / DECAY_INTERVAL.total_seconds())
    if weeks < 1.0:
        return state
    factor = _decay_factor(category) ** weeks
    state["alpha"] = max(0.5, float(state.get("alpha", 1.0)) * factor)
    state["beta"] = max(0.5, float(state.get("beta", 1.0)) * factor)
    state["last_decay_at"] = now
    return state


# ────────────────────────────────────────────────────────────────────────────
# Public API: reading reliability
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class ReliabilitySnapshot:
    vendor_key: str
    pickup_alpha: float
    pickup_beta: float
    quote_alpha: float
    quote_beta: float
    n_calls: int
    last_called_at: Optional[datetime]
    deactivated: bool

    @property
    def pickup_mean(self) -> float:
        a, b = self.pickup_alpha, self.pickup_beta
        return a / (a + b) if (a + b) > 0 else 0.5

    @property
    def quote_mean(self) -> float:
        a, b = self.quote_alpha, self.quote_beta
        return a / (a + b) if (a + b) > 0 else 0.5

    @property
    def reliability_score(self) -> float:
        """Combined vendor reliability in [0, 1]: pickup × quote."""
        return self.pickup_mean * self.quote_mean

    def thompson_sample(self, rng: random.Random | None = None) -> float:
        """Single Thompson-sampling draw from pickup × quote posterior.

        Returns a single scalar in [0, 1]. Vendors below MIN_CALLS_ESTABLISHED
        get extra exploration variance via a small alpha/beta floor.
        """
        rng = rng or random
        if self.n_calls < MIN_CALLS_ESTABLISHED:
            # Wider posterior for cold vendors → more exploration
            pa, pb = max(self.pickup_alpha, 1.0), max(self.pickup_beta, 1.0)
            qa, qb = max(self.quote_alpha, 1.0), max(self.quote_beta, 1.0)
        else:
            pa, pb = self.pickup_alpha, self.pickup_beta
            qa, qb = self.quote_alpha, self.quote_beta
        pickup_draw = rng.betavariate(max(pa, 0.5), max(pb, 0.5))
        quote_draw = rng.betavariate(max(qa, 0.5), max(qb, 0.5))
        return pickup_draw * quote_draw


def _source_prior(source: str | None) -> dict[str, float]:
    return SOURCE_PRIORS.get((source or "").strip().lower(), SOURCE_PRIORS["default"])


def _new_reliability_doc(
    *,
    vendor_key: str,
    category: str,
    source: str | None,
    phone: str | None,
    place_id: str | None,
    now: datetime,
) -> dict[str, Any]:
    prior = _source_prior(source)
    return {
        "vendor_key": vendor_key,
        "category": category,
        "source": (source or "").strip().lower() or None,
        "phone": phone,
        "place_id": place_id,
        "source_prior": dict(prior),
        "pickup": {
            "alpha": float(prior["alpha"]),
            "beta": float(prior["beta"]),
            "last_decay_at": now,
            "n_calls": 0,
        },
        "quote": {
            "alpha": float(prior["alpha"]),
            "beta": float(prior["beta"]),
            "last_decay_at": now,
            "n_quotes_attempted": 0,
        },
        "availability": {
            "available_count": 0,
            "oos_count": 0,
            "wrong_product_count": 0,
        },
        "last_called_at": None,
        "last_outcome": None,
        "daily_call_count": 0,
        "daily_call_count_date": now.strftime("%Y-%m-%d"),
        "hard_flags": {
            "wrong_number": False,
            "deactivated": False,
            "manual_review": False,
            "last_flagged_at": None,
        },
        "created_at": now,
        "updated_at": now,
    }


def snapshot_from_doc(doc: dict[str, Any]) -> ReliabilitySnapshot:
    pickup = doc.get("pickup") or {}
    quote = doc.get("quote") or {}
    return ReliabilitySnapshot(
        vendor_key=doc.get("vendor_key", ""),
        pickup_alpha=float(pickup.get("alpha", 1.0)),
        pickup_beta=float(pickup.get("beta", 1.0)),
        quote_alpha=float(quote.get("alpha", 1.0)),
        quote_beta=float(quote.get("beta", 1.0)),
        n_calls=int(pickup.get("n_calls", 0)),
        last_called_at=doc.get("last_called_at"),
        deactivated=bool((doc.get("hard_flags") or {}).get("deactivated")),
    )


async def get_or_create_reliability(
    vendor_key: str,
    *,
    category: str,
    source: str | None = None,
    phone: str | None = None,
    place_id: str | None = None,
) -> ReliabilitySnapshot:
    """Fetch reliability state, applying lazy decay. Creates a doc with the
    source prior on first sight."""
    now = _now()
    doc = await vendor_reliability_collection.find_one({"vendor_key": vendor_key})
    if doc is None:
        doc = _new_reliability_doc(
            vendor_key=vendor_key,
            category=category,
            source=source,
            phone=phone,
            place_id=place_id,
            now=now,
        )
        try:
            await vendor_reliability_collection.insert_one(doc)
        except Exception as exc:  # pragma: no cover - race on first insert
            logger.debug("vendor_reliability insert race for %s: %s", vendor_key, exc)
            doc = await vendor_reliability_collection.find_one({"vendor_key": vendor_key}) or doc
    else:
        # Lazy decay (category-aware)
        pickup = _apply_decay(dict(doc.get("pickup") or {}), now, category=category)
        quote = _apply_decay(dict(doc.get("quote") or {}), now, category=category)
        if (
            pickup.get("alpha") != (doc.get("pickup") or {}).get("alpha")
            or pickup.get("beta") != (doc.get("pickup") or {}).get("beta")
            or quote.get("alpha") != (doc.get("quote") or {}).get("alpha")
            or quote.get("beta") != (doc.get("quote") or {}).get("beta")
        ):
            await vendor_reliability_collection.update_one(
                {"vendor_key": vendor_key},
                {"$set": {"pickup": pickup, "quote": quote, "updated_at": now}},
            )
            doc["pickup"] = pickup
            doc["quote"] = quote

    return snapshot_from_doc(doc)


async def bulk_load_reliability(
    vendor_keys: Iterable[str],
    *,
    category: str | None = None,
) -> dict[str, ReliabilitySnapshot]:
    """Bulk fetch with in-memory (non-persisted) decay so callers see fresh
    posteriors without a per-vendor write on the hot selection path.

    The persistent decay still happens lazily on the next single-vendor read
    (`get_or_create_reliability`) or update (`update_after_call`).
    """
    keys = [k for k in vendor_keys if k]
    if not keys:
        return {}
    cursor = vendor_reliability_collection.find({"vendor_key": {"$in": keys}})
    out: dict[str, ReliabilitySnapshot] = {}
    now = _now()
    async for doc in cursor:
        # Apply decay in-memory only — no write — to avoid thundering-herd
        # writes during a single search batch.
        pickup_state = _apply_decay(dict(doc.get("pickup") or {}), now, category=category)
        quote_state = _apply_decay(dict(doc.get("quote") or {}), now, category=category)
        doc["pickup"] = pickup_state
        doc["quote"] = quote_state
        snap = snapshot_from_doc(doc)
        out[snap.vendor_key] = snap
    return out


# ────────────────────────────────────────────────────────────────────────────
# Public API: post-call update
# ────────────────────────────────────────────────────────────────────────────


async def update_after_call(
    *,
    vendor_key: str,
    category: str,
    outcome: ClassifiedOutcome,
    source: str | None = None,
    phone: str | None = None,
    place_id: str | None = None,
    call_id: str | None = None,
) -> ReliabilitySnapshot:
    """Apply a single call's outcome to the vendor's Beta posteriors.

    Real-time, single Mongo write. Mapping:

        no_answer:                pickup_β +1
        picked_up_quoted:         pickup_α +1, quote_α +1, available +1
        picked_up_no_quote:       pickup_α +1, quote_β +1
        picked_up_no_stock:       pickup_α +1, oos +1   (quote untouched)
        picked_up_wrong_product:  pickup_α +1, wrong_product +1   (quote untouched)
        picked_up_closed:         pickup_α +1   (quote untouched, recheck later)
        wrong_number:             hard flag, no Beta update

    Returns the post-update snapshot.
    """
    now = _now()

    # Ensure doc exists (also applies decay)
    snap = await get_or_create_reliability(
        vendor_key,
        category=category,
        source=source,
        phone=phone,
        place_id=place_id,
    )

    if outcome.outcome == "wrong_number":
        await vendor_reliability_collection.update_one(
            {"vendor_key": vendor_key},
            {
                "$set": {
                    "hard_flags.wrong_number": True,
                    "hard_flags.last_flagged_at": now,
                    "last_outcome": outcome.outcome,
                    "last_called_at": now,
                    "updated_at": now,
                }
            },
        )
        return snapshot_from_doc(
            await vendor_reliability_collection.find_one({"vendor_key": vendor_key}) or {}
        )

    inc: dict[str, float] = {}
    set_ops: dict[str, Any] = {
        "last_outcome": outcome.outcome,
        "last_called_at": now,
        "updated_at": now,
    }

    # pickup
    if outcome.outcome == "no_answer":
        inc["pickup.beta"] = 1.0
    else:
        inc["pickup.alpha"] = 1.0
        inc["pickup.n_calls"] = 1.0

    # quote
    if outcome.outcome == "picked_up_quoted":
        inc["quote.alpha"] = 1.0
        inc["quote.n_quotes_attempted"] = 1.0
        inc["availability.available_count"] = 1.0
    elif outcome.outcome == "picked_up_no_quote":
        inc["quote.beta"] = 1.0
        inc["quote.n_quotes_attempted"] = 1.0
    elif outcome.outcome == "picked_up_no_stock":
        inc["availability.oos_count"] = 1.0
    elif outcome.outcome == "picked_up_wrong_product":
        inc["availability.wrong_product_count"] = 1.0
    # picked_up_closed: no quote update; we'll recheck during open hours
    # no_answer: nothing else

    # Daily-call cap bookkeeping
    today = now.strftime("%Y-%m-%d")
    update_doc = {"$set": set_ops}
    if inc:
        update_doc["$inc"] = inc

    await vendor_reliability_collection.update_one({"vendor_key": vendor_key}, update_doc)

    # Reset-or-increment daily call counter atomically using an aggregation
    # pipeline update. If the stored date matches today we increment;
    # otherwise we reset to 1 and stamp today's date. This avoids the
    # race where two sequential update_one calls on a date-rollover day
    # would both fire and double-count the first call.
    await vendor_reliability_collection.update_one(
        {"vendor_key": vendor_key},
        [
            {
                "$set": {
                    "daily_call_count": {
                        "$cond": [
                            {"$eq": ["$daily_call_count_date", today]},
                            {"$add": [{"$ifNull": ["$daily_call_count", 0]}, 1]},
                            1,
                        ]
                    },
                    "daily_call_count_date": today,
                }
            }
        ],
    )

    fresh = await vendor_reliability_collection.find_one({"vendor_key": vendor_key}) or {}
    return snapshot_from_doc(fresh)


# ────────────────────────────────────────────────────────────────────────────
# Quote cache
# ────────────────────────────────────────────────────────────────────────────


def _cache_ttl_minutes(category: str) -> int:
    return QUOTE_CACHE_TTL_MINUTES.get(
        (category or "").strip().lower(), QUOTE_CACHE_TTL_MINUTES["default"]
    )


def _cache_key(*, vendor_key: str, product_key: str, location_pincode: str | None) -> str:
    return f"{vendor_key}::{product_key}::{location_pincode or 'unknown'}"


async def get_cached_quote(
    *,
    vendor_key: str,
    product_key: str,
    location_pincode: str | None = None,
) -> Optional[UnifiedResult]:
    """Return a still-valid cached quote, if any."""
    key = _cache_key(
        vendor_key=vendor_key, product_key=product_key, location_pincode=location_pincode
    )
    doc = await vendor_quote_cache_collection.find_one({"cache_key": key})
    if doc is None:
        return None
    expires_at = doc.get("expires_at")
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < _now():
            return None
    try:
        return UnifiedResult(
            source_type="offline",
            name=doc.get("name", ""),
            price=doc.get("price"),
            delivery_time=doc.get("delivery_time"),
            availability=bool(doc.get("availability", True)),
            confidence=float(doc.get("confidence", 0.7)),
            phone=doc.get("phone"),
            address=doc.get("address"),
            notes=(doc.get("notes") or "") + " | cached",
            vendor_id=doc.get("vendor_key"),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("cached quote rebuild failed for %s: %s", key, exc)
        return None


async def cache_quote(
    *,
    vendor_key: str,
    product_key: str,
    category: str,
    result: UnifiedResult,
    location_pincode: str | None = None,
) -> None:
    if result.price is None:
        return
    now = _now()
    ttl_minutes = _cache_ttl_minutes(category)
    key = _cache_key(
        vendor_key=vendor_key, product_key=product_key, location_pincode=location_pincode
    )
    await vendor_quote_cache_collection.update_one(
        {"cache_key": key},
        {
            "$set": {
                "cache_key": key,
                "vendor_key": vendor_key,
                "product_key": product_key,
                "category": category,
                "location_pincode": location_pincode,
                "name": result.name,
                "phone": result.phone,
                "address": result.address,
                "price": result.price,
                "currency": result.currency,
                "delivery_time": result.delivery_time,
                "availability": result.availability,
                "confidence": result.confidence,
                "notes": result.notes,
                "expires_at": now + timedelta(minutes=ttl_minutes),
                "created_at": now,
            }
        },
        upsert=True,
    )


# ────────────────────────────────────────────────────────────────────────────
# Cooldown / eligibility
# ────────────────────────────────────────────────────────────────────────────


async def is_vendor_eligible_to_call(
    vendor_key: str, *, query_started_at: datetime | None = None
) -> tuple[bool, str]:
    """Check cooldowns and hard flags. Returns (eligible, reason_if_not)."""
    doc = await vendor_reliability_collection.find_one({"vendor_key": vendor_key})
    if doc is None:
        return True, ""
    flags = doc.get("hard_flags") or {}
    if flags.get("deactivated") or flags.get("wrong_number"):
        return False, "deactivated"
    today = _now().strftime("%Y-%m-%d")
    if (
        doc.get("daily_call_count_date") == today
        and int(doc.get("daily_call_count", 0)) >= MAX_DAILY_CALLS_PER_VENDOR
    ):
        return False, "daily_cap"
    last_called = doc.get("last_called_at")
    if isinstance(last_called, datetime):
        if last_called.tzinfo is None:
            last_called = last_called.replace(tzinfo=timezone.utc)
        recent_window = _now() - timedelta(hours=SAME_QUERY_RECALL_BLOCK_HOURS)
        if query_started_at is not None:
            if query_started_at.tzinfo is None:
                query_started_at = query_started_at.replace(tzinfo=timezone.utc)
            recent_window = max(recent_window, query_started_at)
        if last_called >= recent_window:
            return False, "recent_call"
    return True, ""


# ────────────────────────────────────────────────────────────────────────────
# Vendor selection: Thompson sampling + diversity
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class CandidateVendor:
    """Light wrapper for selection. The caller provides whatever IDs/handle they want."""
    vendor_key: str
    handle: Any                   # opaque payload (e.g. VendorInfo / dict) returned to caller
    source: str | None = None
    phone: str | None = None
    place_id: str | None = None


async def select_vendors_to_call(
    *,
    candidates: list[CandidateVendor],
    category: str,
    n: int,
    rng: random.Random | None = None,
) -> list[CandidateVendor]:
    """Pick `n` vendors using Thompson sampling with diversity quotas.

    Quota:
      - 70% known-good   (n_calls >= MIN_CALLS_ESTABLISHED, sampled by Thompson)
      - 20% explore      (cold vendors, n_calls < MIN_CALLS_ESTABLISHED)
      - 10% recheck      (known-good but not called in >14 days)
    """
    rng = rng or random
    if not candidates or n <= 0:
        return []

    keys = [c.vendor_key for c in candidates if c.vendor_key]
    snapshots = await bulk_load_reliability(keys, category=category)

    # Build samples
    now = _now()
    fourteen_days_ago = now - timedelta(days=14)

    enriched = []
    for cand in candidates:
        snap = snapshots.get(cand.vendor_key)
        if snap is None:
            # Use source prior for cold-start sampling
            prior = _source_prior(cand.source)
            snap = ReliabilitySnapshot(
                vendor_key=cand.vendor_key,
                pickup_alpha=prior["alpha"],
                pickup_beta=prior["beta"],
                quote_alpha=prior["alpha"],
                quote_beta=prior["beta"],
                n_calls=0,
                last_called_at=None,
                deactivated=False,
            )
        if snap.deactivated:
            continue
        sample = snap.thompson_sample(rng)
        is_established = snap.n_calls >= MIN_CALLS_ESTABLISHED
        last_called = snap.last_called_at
        if last_called and last_called.tzinfo is None:
            last_called = last_called.replace(tzinfo=timezone.utc)
        is_recheck = (
            is_established
            and last_called is not None
            and last_called < fourteen_days_ago
        )
        bucket = "known_good" if is_established else "explore"
        if is_recheck:
            bucket = "recheck"
        enriched.append({"cand": cand, "snap": snap, "sample": sample, "bucket": bucket})

    if not enriched:
        return []

    # Apply quotas
    quotas = {
        "known_good": max(1, math.floor(n * QUOTA_KNOWN_GOOD)),
        "explore": max(1, math.floor(n * QUOTA_EXPLORE)),
        "recheck": max(0, math.floor(n * QUOTA_RECHECK)),
    }
    # Trim quotas if they exceed n (round-up artifacts)
    while sum(quotas.values()) > n:
        for k in ("recheck", "explore", "known_good"):
            if quotas[k] > 0:
                quotas[k] -= 1
                break

    by_bucket: dict[str, list[dict]] = {"known_good": [], "explore": [], "recheck": []}
    for row in enriched:
        by_bucket[row["bucket"]].append(row)
    for bucket in by_bucket.values():
        bucket.sort(key=lambda r: r["sample"], reverse=True)

    selected: list[dict] = []
    for bucket_name, quota in quotas.items():
        selected.extend(by_bucket[bucket_name][:quota])

    # If under-filled (e.g. no recheck candidates), backfill from leftovers by sample
    if len(selected) < n:
        chosen_keys = {row["cand"].vendor_key for row in selected}
        leftovers = [r for r in enriched if r["cand"].vendor_key not in chosen_keys]
        leftovers.sort(key=lambda r: r["sample"], reverse=True)
        for row in leftovers[: n - len(selected)]:
            selected.append(row)

    return [row["cand"] for row in selected[:n]]


# ────────────────────────────────────────────────────────────────────────────
# Index initialization (called from app.database.init_database)
# ────────────────────────────────────────────────────────────────────────────


async def init_indexes() -> None:
    try:
        await vendor_reliability_collection.create_index(
            [("vendor_key", ASCENDING)], unique=True
        )
        await vendor_reliability_collection.create_index([("phone", ASCENDING)])
        await vendor_reliability_collection.create_index(
            [("place_id", ASCENDING)], sparse=True
        )
        await vendor_reliability_collection.create_index(
            [("category", ASCENDING), ("last_called_at", DESCENDING)]
        )
        await vendor_reliability_collection.create_index(
            [("hard_flags.deactivated", ASCENDING)]
        )

        await vendor_quote_cache_collection.create_index(
            [("cache_key", ASCENDING)], unique=True
        )
        await vendor_quote_cache_collection.create_index(
            [("expires_at", ASCENDING)],
            expireAfterSeconds=0,
        )
        await vendor_quote_cache_collection.create_index([("vendor_key", ASCENDING)])
    except Exception as exc:  # pragma: no cover - depends on external service
        logger.warning("vendor_reliability index setup failed: %s", exc)
