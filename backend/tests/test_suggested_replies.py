from app.models.schemas import ConversationState
from app.services.chat_session import (
    _build_suggested_replies,
    _extract_inline_options_from_message,
    _message_asks_quantity_or_location,
)


def test_extract_inline_mg_options() -> None:
    message = (
        'What mg of paracetamol tablets are you looking for — '
        "75, 500, 725, 750, or something else?"
    )
    assert _extract_inline_options_from_message(message) == ["75", "500", "725", "750"]


def test_quantity_question_does_not_show_facet_options() -> None:
    state = ConversationState(session_id="test")
    state.category = "medicine"
    state.intake_catalog_hint = {
        "product": "paracetamol tablets",
        "options": [
            {"node_id": "tablets", "label": "Tablets", "count": 100},
            {"node_id": "injections", "label": "Injections", "count": 50},
            {"node_id": "capsules", "label": "Capsules", "count": 40},
        ],
    }
    message = "How many boxes of 500 mg paracetamol tablets do you need, and what is your location?"
    assert _message_asks_quantity_or_location(message)
    assert _build_suggested_replies(state, message) == []


def test_facet_options_only_for_type_question() -> None:
    state = ConversationState(session_id="test")
    state.intake_catalog_hint = {
        "product": "paracetamol",
        "options": [
            {"node_id": "tablets", "label": "Tablets", "count": 100},
            {"node_id": "injections", "label": "Injections", "count": 50},
        ],
    }
    message = 'I found 120 matches for "paracetamol". What type are you looking for — Tablets, Injections?'
    assert _build_suggested_replies(state, message) == ["Tablets", "Injections"]


def test_medicine_variance_ignores_storage_axis() -> None:
    state = ConversationState(session_id="test")
    state.category = "medicine"
    state.catalog_variance_hint = {
        "high_variance": True,
        "questions_asked": 0,
        "axes": [
            {
                "dimension": "storage",
                "unit": "gb",
                "min_value": 4.7,
                "max_value": 200,
                "examples": ["4.7", "25", "200"],
            }
        ],
    }
    message = "What storage do you need? Our catalog has options from about 4.7 to 200 GB (e.g. 4.7, 25, 200)."
    assert _build_suggested_replies(state, message) == []
