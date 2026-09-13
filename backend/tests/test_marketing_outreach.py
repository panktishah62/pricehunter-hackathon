"""Unit tests for marketing outreach helpers (no Mongo)."""

from app.services.marketing_outreach import (
    build_product_query,
    is_get_quotes_cta,
    is_opt_out_cta,
    normalize_wa_id,
    resolve_audience,
    search_started_copy,
    template_body_params,
)


def test_cta_detection():
    assert is_get_quotes_cta("Get Live Quotes")
    assert is_get_quotes_cta("get live quotes")
    assert not is_get_quotes_cta("gold rates")
    assert is_opt_out_cta("STOP")
    assert is_opt_out_cta("Stop updates")


def test_search_started_copy():
    assert search_started_copy("gold live rates in Mumbai") == (
        "Noted: gold live rates in Mumbai. Contacting all the suppliers for best prices."
    )


def test_normalize_wa_id():
    assert normalize_wa_id("9876543210") == "919876543210"
    assert normalize_wa_id("919876543210") == "919876543210"
    assert normalize_wa_id("+91 98765 43210") == "919876543210"


def test_resolve_audience_prefers_purchase():
    audience = resolve_audience(["gold_bullion"], "electronics")
    assert audience is not None
    assert audience["product_label"] == "gold bullion"
    assert "gold live rates" in audience["query_template"]


def test_build_query_and_template_params():
    audience = resolve_audience(["pharma"], None)
    assert audience is not None
    q = build_product_query(query_template=audience["query_template"], city="Hyderabad")
    assert q == "sultamycin in Hyderabad"
    body_1, body_2 = template_body_params({"business_type": "Manufacturer"}, audience)
    assert "pharma" in body_1.lower()
    assert body_2 == "pharma API"
