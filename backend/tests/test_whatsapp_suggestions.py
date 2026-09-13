import asyncio

from app.models.schemas import ConversationState
from app.services import chat_session, whatsapp
from app.services.whatsapp import (
    _build_whatsapp_suggestion_entries,
    _extract_interactive_reply_id,
    send_reply_with_suggestions,
)


def test_build_whatsapp_suggestion_entries_unique_ids_and_truncated_titles() -> None:
    suggestions = [
        "Tablets",
        "Injections and long-acting formulations",
        "Capsules",
    ]
    entries, mapping = _build_whatsapp_suggestion_entries(suggestions)

    assert len(entries) == 3
    assert entries[0]["title"] == "Tablets"
    assert entries[1]["title"] == "Injections and long-"
    assert mapping[entries[1]["id"]] == "Injections and long-acting formulations"
    assert len(set(mapping)) == 3


def test_extract_interactive_reply_id_button_and_list() -> None:
    button_payload = {
        "interactive": {
            "type": "button_reply",
            "button_reply": {"id": "sugg_0_tablets", "title": "Tablets"},
        }
    }
    list_payload = {
        "interactive": {
            "type": "list_reply",
            "list_reply": {"id": "sugg_1_injections", "title": "Injections"},
        }
    }
    assert _extract_interactive_reply_id(button_payload) == "sugg_0_tablets"
    assert _extract_interactive_reply_id(list_payload) == "sugg_1_injections"
    assert _extract_interactive_reply_id({"interactive": {"type": "nfm_reply"}}) is None


def test_resolve_whatsapp_suggestion_reply_uses_full_label() -> None:
    session_id = "wa-test-session"
    state = ConversationState(session_id=session_id)
    reply_id = "sugg_0_yes_search_for_christmas_tree"
    full_label = "Yes, search for Christmas tree decorations"
    state.whatsapp_suggestion_map = {reply_id: full_label}
    chat_session._SESSIONS[session_id] = state

    resolved = asyncio.run(
        chat_session.resolve_whatsapp_suggestion_reply(
            session_id,
            reply_id=reply_id,
            fallback_text="Yes, search for Christ",
        )
    )
    assert resolved == full_label
    assert chat_session._SESSIONS[session_id].whatsapp_suggestion_map == {}


def test_send_reply_with_suggestions_uses_buttons_for_three_or_fewer(monkeypatch) -> None:
    sent: list[tuple[str, dict]] = []

    async def fake_send_button_message(to, body, *, buttons, session_id=None, search_id=None):
        sent.append(("button", {"to": to, "body": body, "buttons": buttons}))

    async def fake_send_list_message(*args, **kwargs):
        sent.append(("list", kwargs))

    async def fake_send_text_message(*args, **kwargs):
        sent.append(("text", kwargs))

    monkeypatch.setattr("app.services.whatsapp.send_button_message", fake_send_button_message)
    monkeypatch.setattr("app.services.whatsapp.send_list_message", fake_send_list_message)
    monkeypatch.setattr("app.services.whatsapp.send_text_message", fake_send_text_message)

    mapping = asyncio.run(
        send_reply_with_suggestions(
            to="919999999999",
            body="What type are you looking for?",
            suggestions=["Tablets", "Injections", "Capsules"],
            session_id="sess-1",
        )
    )

    assert sent[0][0] == "button"
    assert len(sent[0][1]["buttons"]) == 3
    assert len(mapping) == 3


def test_send_reply_with_suggestions_uses_list_for_four_or_more(monkeypatch) -> None:
    sent: list[str] = []

    async def fake_send_button_message(*args, **kwargs):
        sent.append("button")

    async def fake_send_list_message(to, body, *, rows, session_id=None, search_id=None, **kwargs):
        sent.append("list")
        assert len(rows) == 4

    async def fake_send_text_message(*args, **kwargs):
        sent.append("text")

    monkeypatch.setattr("app.services.whatsapp.send_button_message", fake_send_button_message)
    monkeypatch.setattr("app.services.whatsapp.send_list_message", fake_send_list_message)
    monkeypatch.setattr("app.services.whatsapp.send_text_message", fake_send_text_message)

    mapping = asyncio.run(
        send_reply_with_suggestions(
            to="919999999999",
            body="Pick a strength",
            suggestions=["75", "500", "725", "750"],
            session_id="sess-2",
        )
    )

    assert sent == ["list"]
    assert len(mapping) == 4


def test_normalize_vendor_phone_e164() -> None:
    assert whatsapp.normalize_vendor_phone_e164("9876543210") == "919876543210"
    assert whatsapp.normalize_vendor_phone_e164("+91 98765 43210") == "919876543210"
    assert whatsapp.normalize_vendor_phone_e164("09876543210") == "919876543210"
    assert whatsapp.normalize_vendor_phone_e164("invalid") is None


def test_build_vendor_call_url(monkeypatch) -> None:
    monkeypatch.setattr(whatsapp.settings, "voice_webhook_base_url", "https://api.example.com")
    url = whatsapp.build_vendor_call_url("9876543210")
    assert url == "https://api.example.com/api/whatsapp/call-vendor?phone=919876543210"


def test_send_result_card_content_uses_call_cta(monkeypatch) -> None:
    from app.models.schemas import UnifiedResult

    sent: list[tuple[str, str]] = []

    async def fake_send_cta_url_message(*args, **kwargs):
        sent.append(("cta", kwargs.get("button_label") or args[3] if len(args) > 3 else ""))

    async def fake_send_cta(**kwargs):
        sent.append(("cta", kwargs["button_label"]))

    async def fake_send_text_message(*args, **kwargs):
        sent.append(("text", ""))

    async def fake_send_image_message(*args, **kwargs):
        sent.append(("image", ""))

    monkeypatch.setattr(whatsapp.settings, "voice_webhook_base_url", "https://api.example.com")
    monkeypatch.setattr(whatsapp, "_send_result_card_cta", fake_send_cta)
    monkeypatch.setattr(whatsapp, "send_text_message", fake_send_text_message)
    monkeypatch.setattr(whatsapp, "send_image_message", fake_send_image_message)

    result = UnifiedResult(
        source_type="offline",
        name="Vendor | Mug",
        price=299,
        phone="9876543210",
    )
    payload: dict = {}
    asyncio.run(
        whatsapp._send_result_card_content(
            to="919999999999",
            text="Product is available.",
            image_url="https://cdn.example.com/item.jpg",
            result=result,
            session_id="sess-1",
            search_id="search-1",
            payload=payload,
        )
    )

    assert sent == [("cta", "Call vendor")]
    assert payload.get("call_cta_sent") is True


def test_result_product_url_skips_indiamart() -> None:
    from app.models.schemas import UnifiedResult

    result = UnifiedResult(
        source_type="offline",
        name="Listing",
        url="https://www.indiamart.com/proddetail/123.html",
    )
    assert whatsapp._result_product_url(result) is None

    result.url = "https://dealer.example.com/products/mug"
    assert whatsapp._result_product_url(result) == "https://dealer.example.com/products/mug"


def test_result_card_cta_prefers_web_link_over_call(monkeypatch) -> None:
    from app.models.schemas import UnifiedResult

    monkeypatch.setattr(whatsapp.settings, "voice_webhook_base_url", "https://api.example.com")

    result = UnifiedResult(
        source_type="online",
        name="Live dealer",
        result_type="live_rate",
        url="https://dealer.example.com/gold",
        phone="9876543210",
    )
    cta = whatsapp._result_card_cta(result)
    assert cta == ("product", "Open dealer site", "https://dealer.example.com/gold")

    result.url = "https://www.indiamart.com/proddetail/123.html"
    cta = whatsapp._result_card_cta(result)
    assert cta is not None
    assert cta[0] == "call"
    assert cta[1] == "Call vendor"


def test_format_money_includes_unit_from_preview() -> None:
    from app.models.schemas import UnifiedResult

    result = UnifiedResult(source_type="offline", name="Mug", price=299)
    preview = {"unit": "Piece", "price": 299}
    assert whatsapp._format_money(result, preview) == "₹299 / Piece"


def test_format_money_includes_unit_from_specs() -> None:
    from app.models.schemas import UnifiedResult

    result = UnifiedResult(source_type="offline", name="Mug", price=299)
    preview = {
        "price": 299,
        "specs": [{"label": "Packaging Unit", "value": "Box"}],
    }
    assert whatsapp._format_money(result, preview) == "₹299 / Box"


def test_format_money_omits_unit_when_missing() -> None:
    from app.models.schemas import UnifiedResult

    result = UnifiedResult(source_type="offline", name="Mug", price=299)
    assert whatsapp._format_money(result, {}) == "₹299"


def test_quote_card_text_hides_availability_and_supplier_type_specs() -> None:
    from app.models.schemas import UnifiedResult

    result = UnifiedResult(
        source_type="offline",
        name="Acme Traders | Ceramic Mug",
        price=299,
        availability=True,
    )
    preview = {
        "title": "Ceramic Mug",
        "price": 299,
        "available": "In Stock",
        "specs": [
            {"label": "Material", "value": "Ceramic"},
            {"label": "Availability", "value": "In Stock"},
            {"label": "Supplier Type", "value": "Manufacturer"},
            {"label": "IndiaMART MCAT ID", "value": "12345"},
        ],
    }
    result.attributes = {"product_preview": preview}
    text = whatsapp._quote_card_text(result)

    assert "Availability: In Stock." in text
    assert "Product Details:" in text
    assert "Material: Ceramic" in text
    assert "Supplier Type:" not in text
    assert "IndiaMART MCAT ID:" not in text
    details_section = text.split("Product Details:", 1)[1]
    assert "Availability:" not in details_section
