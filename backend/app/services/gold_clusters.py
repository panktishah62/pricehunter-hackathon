"""Gold bullion vendor *cluster* resolution.

Vendors carry no geo coordinates, so we group them into a small set of city
clusters (Zaveri Bazaar / Dariba Kalan / etc.) and resolve a searcher's
location to the nearest cluster. A buyer in Jamnagar (few local suppliers)
should see Rajkot/Saurashtra vendors; a buyer in Thrissur should see
Coimbatore, and so on.

Resolution order (cheapest first):
  1. exact / alias city lookup  (no network)
  2. state lookup               (no network)
  3. Google Places geocode      (network, cached) -> nearest cluster by haversine

The vendor `cluster_id` we store is a *canonical city key* (e.g. "mumbai",
"rajkot"), NOT a micro-market id, so vicinity matching is city-level and
stable even though the clusters collection holds finer-grained bazaars.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any

import httpx

from app.config import settings
from app.database import zwig_vendor_clusters_collection

logger = logging.getLogger(__name__)

# Canonical cluster keys (== lowercased cluster city). Anything resolved to one
# of these will match vendors whose `cluster_id` is the same key.
CLUSTER_MUMBAI = "mumbai"
CLUSTER_DELHI = "delhi"
CLUSTER_BANGALORE = "bangalore"
CLUSTER_CHENNAI = "chennai"
CLUSTER_HYDERABAD = "hyderabad"
CLUSTER_AHMEDABAD = "ahmedabad"
CLUSTER_RAJKOT = "rajkot"
CLUSTER_SURAT = "surat"
CLUSTER_COIMBATORE = "coimbatore"
CLUSTER_JAIPUR = "jaipur"
CLUSTER_KOLKATA = "kolkata"

# Representative coordinates per canonical cluster (used for haversine fallback
# and seeded into the clusters collection for the 5 new ones).
CLUSTER_COORDS: dict[str, tuple[float, float]] = {
    CLUSTER_MUMBAI: (18.951808, 72.830697),
    CLUSTER_DELHI: (28.6553, 77.2342),
    CLUSTER_BANGALORE: (12.97186, 77.57721),
    CLUSTER_CHENNAI: (13.0418, 80.2341),
    CLUSTER_HYDERABAD: (17.366, 78.476),
    CLUSTER_AHMEDABAD: (23.016667, 72.590833),
    CLUSTER_RAJKOT: (22.3039, 70.8022),
    CLUSTER_SURAT: (21.1702, 72.8311),
    CLUSTER_COIMBATORE: (11.0168, 76.9558),
    CLUSTER_JAIPUR: (26.9124, 75.7873),
    CLUSTER_KOLKATA: (22.5726, 88.3639),
}

# City / district aliases -> canonical cluster key. Keys are normalized (lower,
# alnum+space). Add the buyer's city here when a regional hub should serve it.
CITY_TO_CLUSTER: dict[str, str] = {
    # Maharashtra
    "mumbai": CLUSTER_MUMBAI, "navi mumbai": CLUSTER_MUMBAI, "thane": CLUSTER_MUMBAI,
    "mumabi": CLUSTER_MUMBAI,
    "pune": CLUSTER_MUMBAI, "nashik": CLUSTER_MUMBAI, "nasik": CLUSTER_MUMBAI,
    "aurangabad": CLUSTER_MUMBAI, "kolhapur": CLUSTER_MUMBAI, "nagpur": CLUSTER_MUMBAI,
    "solapur": CLUSTER_MUMBAI,
    # Delhi NCR + north
    "delhi": CLUSTER_DELHI, "new delhi": CLUSTER_DELHI, "noida": CLUSTER_DELHI,
    "gurgaon": CLUSTER_DELHI, "gurugram": CLUSTER_DELHI, "faridabad": CLUSTER_DELHI,
    "ghaziabad": CLUSTER_DELHI, "ludhiana": CLUSTER_DELHI, "amritsar": CLUSTER_DELHI,
    "jalandhar": CLUSTER_DELHI, "chandigarh": CLUSTER_DELHI, "agra": CLUSTER_DELHI,
    "lucknow": CLUSTER_DELHI, "kanpur": CLUSTER_DELHI, "varanasi": CLUSTER_DELHI,
    "meerut": CLUSTER_DELHI, "jhansi": CLUSTER_DELHI, "allahabad": CLUSTER_DELHI,
    "prayagraj": CLUSTER_DELHI, "bareilly": CLUSTER_DELHI,
    # Karnataka
    "bangalore": CLUSTER_BANGALORE, "bengaluru": CLUSTER_BANGALORE, "bengalore": CLUSTER_BANGALORE,
    "mysore": CLUSTER_BANGALORE, "mysuru": CLUSTER_BANGALORE, "hubli": CLUSTER_BANGALORE,
    "dharwad": CLUSTER_BANGALORE, "belgaum": CLUSTER_BANGALORE, "mangalore": CLUSTER_BANGALORE,
    # Tamil Nadu (Chennai side)
    "chennai": CLUSTER_CHENNAI, "vellore": CLUSTER_CHENNAI, "kanchipuram": CLUSTER_CHENNAI,
    "tiruvallur": CLUSTER_CHENNAI, "pondicherry": CLUSTER_CHENNAI, "puducherry": CLUSTER_CHENNAI,
    # Tamil Nadu (Coimbatore / west) + Kerala
    "coimbatore": CLUSTER_COIMBATORE, "tirupur": CLUSTER_COIMBATORE, "erode": CLUSTER_COIMBATORE,
    "salem": CLUSTER_COIMBATORE, "madurai": CLUSTER_COIMBATORE, "trichy": CLUSTER_COIMBATORE,
    "tiruchirappalli": CLUSTER_COIMBATORE, "thrissur": CLUSTER_COIMBATORE, "trichur": CLUSTER_COIMBATORE,
    "kochi": CLUSTER_COIMBATORE, "cochin": CLUSTER_COIMBATORE, "ernakulam": CLUSTER_COIMBATORE,
    "kozhikode": CLUSTER_COIMBATORE, "calicut": CLUSTER_COIMBATORE, "palakkad": CLUSTER_COIMBATORE,
    "thiruvananthapuram": CLUSTER_COIMBATORE, "trivandrum": CLUSTER_COIMBATORE,
    # Telangana / Andhra
    "hyderabad": CLUSTER_HYDERABAD, "secunderabad": CLUSTER_HYDERABAD,
    "vijayawada": CLUSTER_HYDERABAD, "visakhapatnam": CLUSTER_HYDERABAD, "vizag": CLUSTER_HYDERABAD,
    "nellore": CLUSTER_HYDERABAD, "guntur": CLUSTER_HYDERABAD, "warangal": CLUSTER_HYDERABAD,
    "tirupati": CLUSTER_HYDERABAD, "tenali": CLUSTER_HYDERABAD, "anantapur": CLUSTER_HYDERABAD,
    "vishakhapatnam": CLUSTER_HYDERABAD, "kakinada": CLUSTER_HYDERABAD, "kurnool": CLUSTER_HYDERABAD,
    # Gujarat (Ahmedabad / central + north)
    "ahmedabad": CLUSTER_AHMEDABAD, "gandhinagar": CLUSTER_AHMEDABAD, "nadiad": CLUSTER_AHMEDABAD,
    "anand": CLUSTER_AHMEDABAD, "vadodara": CLUSTER_AHMEDABAD, "baroda": CLUSTER_AHMEDABAD,
    "palanpur": CLUSTER_AHMEDABAD, "mehsana": CLUSTER_AHMEDABAD, "himmatnagar": CLUSTER_AHMEDABAD,
    # Gujarat (Saurashtra -> Rajkot)
    "rajkot": CLUSTER_RAJKOT, "jamnagar": CLUSTER_RAJKOT, "bhavnagar": CLUSTER_RAJKOT,
    "junagadh": CLUSTER_RAJKOT, "morbi": CLUSTER_RAJKOT, "gondal": CLUSTER_RAJKOT,
    "porbandar": CLUSTER_RAJKOT, "amreli": CLUSTER_RAJKOT, "surendranagar": CLUSTER_RAJKOT,
    # Gujarat (South -> Surat)
    "surat": CLUSTER_SURAT, "navsari": CLUSTER_SURAT, "bharuch": CLUSTER_SURAT,
    "valsad": CLUSTER_SURAT, "vapi": CLUSTER_SURAT, "ankleshwar": CLUSTER_SURAT,
    # Rajasthan
    "jaipur": CLUSTER_JAIPUR, "jodhpur": CLUSTER_JAIPUR, "udaipur": CLUSTER_JAIPUR,
    "kota": CLUSTER_JAIPUR, "ajmer": CLUSTER_JAIPUR, "bikaner": CLUSTER_JAIPUR,
    "neemuch": CLUSTER_JAIPUR, "alwar": CLUSTER_JAIPUR, "barmer": CLUSTER_JAIPUR,
    "jaisalmer": CLUSTER_JAIPUR, "sikar": CLUSTER_JAIPUR, "bhilwara": CLUSTER_JAIPUR,
    # West Bengal / East
    "kolkata": CLUSTER_KOLKATA, "calcutta": CLUSTER_KOLKATA, "howrah": CLUSTER_KOLKATA,
    "siliguri": CLUSTER_KOLKATA, "durgapur": CLUSTER_KOLKATA, "asansol": CLUSTER_KOLKATA,
    "cuttack": CLUSTER_KOLKATA, "bhubaneswar": CLUSTER_KOLKATA, "patna": CLUSTER_KOLKATA,
    "ranchi": CLUSTER_KOLKATA, "guwahati": CLUSTER_KOLKATA,
}

# State -> canonical cluster, used when only a state token is present.
STATE_TO_CLUSTER: dict[str, str] = {
    "maharashtra": CLUSTER_MUMBAI,
    "goa": CLUSTER_MUMBAI,
    "delhi": CLUSTER_DELHI,
    "punjab": CLUSTER_DELHI,
    "haryana": CLUSTER_DELHI,
    "uttar pradesh": CLUSTER_DELHI,
    "uttarakhand": CLUSTER_DELHI,
    "himachal pradesh": CLUSTER_DELHI,
    "jammu and kashmir": CLUSTER_DELHI,
    "karnataka": CLUSTER_BANGALORE,
    "telangana": CLUSTER_HYDERABAD,
    "andhra pradesh": CLUSTER_HYDERABAD,
    "tamil nadu": CLUSTER_CHENNAI,
    "kerala": CLUSTER_COIMBATORE,
    "gujarat": CLUSTER_AHMEDABAD,
    "rajasthan": CLUSTER_JAIPUR,
    "madhya pradesh": CLUSTER_JAIPUR,
    "west bengal": CLUSTER_KOLKATA,
    "odisha": CLUSTER_KOLKATA,
    "orissa": CLUSTER_KOLKATA,
    "bihar": CLUSTER_KOLKATA,
    "jharkhand": CLUSTER_KOLKATA,
    "assam": CLUSTER_KOLKATA,
    "chhattisgarh": CLUSTER_HYDERABAD,
}

_CLUSTERS_CACHE: list[dict[str, Any]] | None = None
_GEOCODE_CACHE: dict[str, tuple[float, float] | None] = {}


def _normalize(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _tokens(location: str | None) -> list[str]:
    """Split a messy location into candidate phrases (full string + comma/dash
    separated parts), normalized, longest-first so multi-word cities win."""
    raw = (location or "").strip()
    if not raw:
        return []
    parts = re.split(r"[,/]", raw)
    extra: list[str] = []
    for part in parts:
        extra.extend(re.split(r"[-]", part))
    phrases = [raw, *parts, *extra]
    seen: list[str] = []
    for phrase in phrases:
        norm = _normalize(phrase)
        if norm and norm not in seen:
            seen.append(norm)
    return seen


def cluster_for_city(location: str | None) -> str | None:
    """Sync resolution via the alias + state tables only (no network).

    Used by the backfill to stamp vendor `cluster_id`, and as the cheap first
    pass of `resolve_cluster`.
    """
    for phrase in _tokens(location):
        cluster = CITY_TO_CLUSTER.get(phrase)
        if cluster:
            return cluster
    for phrase in _tokens(location):
        cluster = STATE_TO_CLUSTER.get(phrase)
        if cluster:
            return cluster
    return None


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1 = math.radians(a[0]), math.radians(a[1])
    lat2, lng2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


async def load_clusters(force: bool = False) -> list[dict[str, Any]]:
    """Load clusters from the collection, collapsed to one entry per canonical
    city key with a representative coordinate. Falls back to CLUSTER_COORDS."""
    global _CLUSTERS_CACHE
    if _CLUSTERS_CACHE is not None and not force:
        return _CLUSTERS_CACHE

    by_key: dict[str, dict[str, Any]] = {}
    try:
        docs = await zwig_vendor_clusters_collection.find(
            {"is_active": {"$ne": False}},
            {"cluster_id": 1, "city": 1, "state": 1, "lat": 1, "lng": 1},
        ).to_list(length=500)
    except Exception as exc:  # pragma: no cover - external service
        logger.warning("load_clusters failed, using static coords: %s", exc)
        docs = []

    for doc in docs:
        key = _normalize(doc.get("city"))
        if not key:
            continue
        lat, lng = doc.get("lat"), doc.get("lng")
        if not (isinstance(lat, (int, float)) and isinstance(lng, (int, float))):
            continue
        by_key.setdefault(key, {"cluster_id": key, "lat": float(lat), "lng": float(lng)})

    # Ensure every canonical cluster is present even if not yet seeded.
    for key, (lat, lng) in CLUSTER_COORDS.items():
        by_key.setdefault(key, {"cluster_id": key, "lat": lat, "lng": lng})

    _CLUSTERS_CACHE = list(by_key.values())
    return _CLUSTERS_CACHE


async def _geocode(location: str) -> tuple[float, float] | None:
    norm = _normalize(location)
    if not norm:
        return None
    if norm in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[norm]

    api_key = settings.google_places_api_key
    if not api_key:
        _GEOCODE_CACHE[norm] = None
        return None

    coords: tuple[float, float] | None = None
    try:
        async with httpx.AsyncClient(timeout=8.0) as http:
            resp = await http.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": f"{location}, India", "region": "in", "key": api_key},
            )
            data = resp.json()
        results = data.get("results") or []
        if results:
            loc = results[0]["geometry"]["location"]
            coords = (float(loc["lat"]), float(loc["lng"]))
    except Exception as exc:  # pragma: no cover - external service
        logger.warning("geocode failed for %r: %s", location, exc)
        coords = None

    _GEOCODE_CACHE[norm] = coords
    return coords


async def _nearest_cluster(coords: tuple[float, float]) -> str | None:
    clusters = await load_clusters()
    best: tuple[float, str] | None = None
    for cluster in clusters:
        dist = _haversine_km(coords, (cluster["lat"], cluster["lng"]))
        if best is None or dist < best[0]:
            best = (dist, cluster["cluster_id"])
    return best[1] if best else None


async def resolve_cluster(location: str | None) -> str | None:
    """Resolve a searcher location to a canonical cluster key.

    Table-first (free), then Google Places geocode + nearest cluster.
    """
    if not location:
        return None
    table_hit = cluster_for_city(location)
    if table_hit:
        return table_hit
    coords = await _geocode(location)
    if coords is None:
        return None
    return await _nearest_cluster(coords)
