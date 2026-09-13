from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

UrgencyOption = Literal["immediate", "1-2 days", "10 days", "no rush"]
SearchStrategy = Literal["online", "offline", "both"]
ChatIntent = Literal["cheapest", "fastest", "best_value"]
ChatField = Literal["product", "urgency", "intent", "category", "location"]
ProgressStatus = Literal["pending", "running", "completed", "failed"]


BrandPreference = Literal["specific", "any", "unknown"]


class StructuredQuery(BaseModel):
    """Output of LLM #1 — the structured version of user's raw query."""

    product: str
    category: str
    subcategory_id: Optional[str] = None
    route_handler: Optional[str] = None
    location: str
    intent: Literal["cheapest", "fastest", "best_value", "nearest"]
    urgency: UrgencyOption = "immediate"
    raw_query: str
    brand: Optional[str] = Field(
        default=None,
        description=(
            "Manufacturer / label brand the buyer asked for, e.g. 'Tupperware', "
            "'Probot', 'Amul'. Keep this OUT of 'product'. Null when the buyer "
            "did not name a brand (or said any brand is fine)."
        ),
    )
    brand_preference: Optional[BrandPreference] = Field(
        default=None,
        description=(
            "'specific' when a brand was named or chosen; 'any' when the buyer "
            "said any/good brand is fine; 'unknown' when brand was not discussed. "
            "Null for legacy queries."
        ),
    )
    quantity: Optional[str] = Field(
        default=None,
        description=(
            "Free-form quantity the buyer asked for, including the unit when "
            "given, e.g. '10 pieces', '1 litre', '2 dozen', '100 gram'. "
            "None when the buyer did not specify a quantity."
        ),
    )
    gold_form: Optional[Literal["bullion", "jewellery", "scrap", "other"]] = Field(
        default=None,
        description=(
            "For gold-category queries ONLY: the form of the gold/silver product. "
            "'bullion' = investment-grade bars, coins, biscuits, or a plain gold/"
            "silver spot/rate ask (live rates are published for these). "
            "'jewellery' = finished ornaments (rings, chains, necklaces, bangles, "
            "earrings, mangalsutra, pendants). 'scrap' = old/scrap gold, casting, "
            "ash/bhasma, refining waste, dust, polish sweeps, filings. 'other' = any "
            "other gold-related ask that is not a tradable bullion rate. "
            "Use null for non-gold categories."
        ),
    )
    use_case: Optional[str] = Field(
        default=None,
        description=(
            "Buyer use case as classified during chat intake: "
            "'consumer_single', 'consumer_bulk', 'industrial', or 'b2b_bulk'. "
            "Drives whether the voice agent calls as a retail buyer or a "
            "wholesale/business buyer. None when unknown."
        ),
    )
    gst_required: Optional[bool] = Field(
        default=None,
        description=(
            "True when the buyer needs a GST/tax invoice (asked for B2B/bulk "
            "orders during intake). None when not applicable or unknown."
        ),
    )
    descriptors: list[str] = Field(
        default_factory=list,
        description=(
            "Style/quality adjectives from the query that describe the product "
            "but do not define its type, e.g. 'fancy', 'premium', 'designer', "
            "'stylish', 'latest', 'high quality'. NEVER put type-defining "
            "modifiers here: 'ceiling' in 'ceiling fan', 'dslr' in 'dslr "
            "camera', 'bluetooth' in 'bluetooth speaker' belong in 'product'. "
            "Empty list when there are none."
        ),
    )
    search_specs: list[str] = Field(
        default_factory=list,
        description=(
            "Variant-defining specs collected during intake that should drive "
            "re-ranking: capacity ('10000 mAh'), storage ('256GB'), model "
            "('iPhone 15 Pro', 'Rockerz 450'), voltage, size, color when it "
            "narrows the SKU. Prefer the dedicated 'brand' field for manufacturer "
            "brands; brand may also appear here for ranking boosts."
        ),
    )
    taxonomy_facet_id: Optional[str] = Field(
        default=None,
        description=(
            "Internal field set by the search disambiguation step (taxonomy "
            "node chosen by the user). ALWAYS return null."
        ),
    )


class ProductPrecisionAssessment(BaseModel):
    """Whether a product request is precise enough to search."""

    refined_product: str
    precise_enough: bool
    missing_attributes: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)


class VisualProductIntent(BaseModel):
    """Vision-derived product brief used to turn an image into a searchable query."""

    product_name: str
    category: str = "electronics"
    subcategory: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    visible_attributes: dict[str, str] = Field(default_factory=dict)
    confidence: float = 0.0
    follow_up_questions: list[str] = Field(default_factory=list)
    search_query: str
    description: str


class WhatsAppIntakeRouterResult(BaseModel):
    """LLM router: merge prior session with the latest WhatsApp bubble for query structuring."""

    is_conversational_chaff: bool = Field(
        description="True if this bubble is only acknowledgment/small-talk and must not change captured shopping text"
    )
    effective_search_text: str = Field(
        default="",
        description="Single line to parse as shopping intent this turn; empty when is_conversational_chaff is true",
    )


class ExtractionField(BaseModel):
    """A single field in a per-query, LLM-generated extraction schema."""

    name: str
    type: Literal["string", "number", "boolean", "enum"]
    description: Optional[str] = None
    enum_values: Optional[list[str]] = None
    is_core: bool = False


class ExtractionSchema(BaseModel):
    """The per-query extraction schema describing fields to capture from a call."""

    schema_version: str
    fields: list[ExtractionField]


class UnifiedResult(BaseModel):
    """THE core schema. Both pipelines MUST return a list of these."""

    # `model_version` (Req 7.1) starts with the `model_` prefix Pydantic reserves
    # as a protected namespace; the field name is mandated by the spec, so opt out
    # of the namespace guard rather than rename it.
    model_config = {"protected_namespaces": ()}

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_type: Literal["online", "offline"]
    result_type: Optional[str] = None
    name: str
    price: Optional[float] = None
    currency: str = "INR"
    display_price: Optional[float] = None
    display_currency: Optional[str] = None
    fx_rate: Optional[float] = None
    fx_as_of: Optional[datetime] = None
    delivery_time: Optional[str] = None
    availability: bool = True
    negotiated: bool = False
    confidence: float = 0.5
    vendor_id: Optional[str] = None
    city: Optional[str] = None
    channel_type: Optional[str] = None
    url: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    gold_terms: Optional[dict] = None
    attributes: Optional[dict] = None
    # Drift traceability (Req 7.1, 7.2): set only on the schema-driven path.
    # `schema_version` identifies the ExtractionSchema (model-independent),
    # `model_version` the LLM used to extract. Default None so non-schema /
    # flag-off results and existing persisted records are unaffected (Req 6.4).
    schema_version: Optional[str] = None
    model_version: Optional[str] = None
    is_mock: bool = False
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class VendorInfo(BaseModel):
    """A discovered local vendor."""

    vendor_id: Optional[str] = None
    name: str
    phone: str
    address: str
    city: Optional[str] = None
    location: Optional[dict] = None
    place_id: Optional[str] = None
    rating: Optional[float] = None
    user_rating_count: Optional[int] = None
    confidence_score: Optional[float] = None
    channel_types: list[str] = Field(default_factory=list)
    live_script_available: bool = False
    live_script_count: int = 0
    whatsapp_available: bool = False
    call_available: bool = False
    preferred_contact_channel: Optional[str] = None
    website: Optional[str] = None
    bucket: Optional[str] = None
    offering_id: Optional[str] = None
    product_id: Optional[str] = None
    category_id: Optional[str] = None
    is_mock: bool = False


class VoiceCallResult(BaseModel):
    """Raw result from a voice call before extraction."""

    vendor: VendorInfo
    call_id: str
    provider: str = "bolna"
    status: Literal["completed", "failed", "no_answer", "busy"]
    transcript: Optional[str] = None
    duration_seconds: Optional[int] = None
    extracted_data: Optional[dict] = None
    provider_metadata: Optional[dict] = None
    recording_url: Optional[str] = None
    is_mock: bool = False


class SearchRequest(BaseModel):
    """API request body."""

    query: str
    location: Optional[str] = None


class SearchResponse(BaseModel):
    """API response body."""

    query: StructuredQuery
    results: list[UnifiedResult]
    online_count: int
    offline_count: int
    total_time_seconds: float
    search_strategy: SearchStrategy = "both"


class SearchProgressStep(BaseModel):
    id: str
    label: str
    status: ProgressStatus = "pending"
    detail: Optional[str] = None


class OtherCityPrompt(BaseModel):
    status: Literal["none", "awaiting", "accepted", "declined"] = "none"
    message: Optional[str] = None
    cities: list[str] = Field(default_factory=list)
    other_city_count: int = 0


class BrandFallbackPrompt(BaseModel):
    """Ask before showing non-brand DB suppliers after a brand miss."""

    status: Literal["none", "awaiting", "accepted", "declined"] = "none"
    message: Optional[str] = None
    brand: Optional[str] = None
    same_city_count: int = 0
    other_city_count: int = 0


class SearchProgressSnapshot(BaseModel):
    search_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    query: StructuredQuery
    status: ProgressStatus = "pending"
    discovered_vendors: list[VendorInfo] = Field(default_factory=list)
    online_platforms: list[str] = Field(default_factory=list)
    steps: list[SearchProgressStep] = Field(default_factory=list)
    partial_results: list[UnifiedResult] = Field(default_factory=list)
    final_results: Optional[SearchResponse] = None
    visible_result_count: int = 5
    next_result_offset: int = 0
    total_ranked_results: int = 0
    has_more_results: bool = False
    # Same-city DB shown first; other-city DB held until user consents.
    pending_other_city_results: list[UnifiedResult] = Field(default_factory=list)
    other_city_prompt: Optional[OtherCityPrompt] = None
    # Brand miss: hold generic DB until user opts into other brands.
    pending_brand_fallback_same_city: list[UnifiedResult] = Field(default_factory=list)
    pending_brand_fallback_other_city: list[UnifiedResult] = Field(default_factory=list)
    brand_fallback_prompt: Optional[BrandFallbackPrompt] = None
    error: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SearchMoreResultsResponse(BaseModel):
    search_id: str
    results: list[UnifiedResult] = Field(default_factory=list)
    next_offset: int
    has_more: bool = False
    total_results: int = 0


class ProductPreviewRequest(BaseModel):
    url: str


class ProductPreviewSpec(BaseModel):
    label: str
    value: str


class ProductPreviewResponse(BaseModel):
    url: str
    title: Optional[str] = None
    price: Optional[str] = None
    unit: Optional[str] = None
    image_url: Optional[str] = None
    images: list[str] = Field(default_factory=list)
    specs: list[ProductPreviewSpec] = Field(default_factory=list)
    description: Optional[str] = None
    supplier_name: Optional[str] = None
    supplier_city: Optional[str] = None
    supplier_address: Optional[str] = None
    supplier_phone: Optional[str] = None
    supplier_rating: Optional[str] = None
    supplier_rating_count: Optional[str] = None
    response_rate: Optional[str] = None
    gst: Optional[str] = None
    gst_verified: Optional[bool] = None
    trust_badges: list[str] = Field(default_factory=list)
    company_details: list[ProductPreviewSpec] = Field(default_factory=list)
    available: Optional[str] = None
    source_timestamp: datetime = Field(default_factory=datetime.utcnow)


class GoldLiveRatePreviewRequest(BaseModel):
    vendor_id: Optional[str] = None
    source_url: Optional[str] = None
    city: Optional[str] = None


class GoldLiveRateRow(BaseModel):
    script_name: str
    buy_rate: Optional[float] = None
    sell_rate: Optional[float] = None
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    purity: Optional[str] = None
    product_type: Optional[str] = None
    quantity_grams: Optional[float] = None
    unit: Optional[str] = None
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    updated_at: Optional[str] = None


class GoldLiveRatePreviewResponse(BaseModel):
    vendor_id: Optional[str] = None
    vendor_name: Optional[str] = None
    city: Optional[str] = None
    source_url: Optional[str] = None
    updated_at: Optional[str] = None
    rates: list[GoldLiveRateRow] = Field(default_factory=list)


class ConversationState(BaseModel):
    """Current chatbot slot-filling state for a search session."""

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    product: Optional[str] = None
    category: Optional[str] = None
    location: str = "unknown"
    intent: Optional[ChatIntent] = None
    urgency: Optional[UrgencyOption] = None
    missing_fields: list[ChatField] = Field(default_factory=list)
    awaiting_field: Optional[ChatField] = None
    search_strategy: Optional[SearchStrategy] = None
    raw_query: str = ""
    urgency_prompt_count: int = 0
    product_prompt_count: int = 0
    product_precise: bool = False
    product_missing_attributes: list[str] = Field(default_factory=list)
    product_follow_up_questions: list[str] = Field(default_factory=list)
    declined_attributes: list[str] = Field(default_factory=list)
    precision_check_count: int = 0
    suggested_brands: list[str] = Field(default_factory=list)
    product_family_id: Optional[str] = None
    product_attributes: dict[str, str] = Field(default_factory=dict)
    product_intake_awaiting_attribute: Optional[str] = None

    # --- Priya LLM-driven conversation fields ---
    priya_active: bool = False
    priya_use_case: Optional[str] = None  # "consumer_single", "consumer_bulk", "industrial", "b2b_bulk"
    priya_checklist: list[str] = Field(default_factory=list)
    priya_collected: dict[str, str] = Field(default_factory=dict)
    priya_conversation_history: list[dict[str, str]] = Field(default_factory=list)
    priya_ready: bool = False
    last_search_id: Optional[str] = None
    # Pending Algolia facet disambiguation: set when the chat asked "what type
    # of X?" and we are waiting for the user to pick an option.
    # Shape: {"query": StructuredQuery dump, "options": [{"node_id", "label", "count"}]}
    pending_disambiguation: Optional[dict] = None
    # Catalog product-type options probed from Algolia facets during intake, so
    # Priya's first "what type?" question offers real options instead of a
    # generic template. Shape: {"product": str, "options": [{"node_id", "label", "count"}]}
    intake_catalog_hint: Optional[dict] = None
    # Catalog variant spread for dominant queries (capacity/storage variance).
    # Shape: see catalog_variance.variance_to_hint_payload
    catalog_variance_hint: Optional[dict] = None
    # Pending visual search: image analyzed, waiting for location or buyer confirmation.
    pending_visual_search: Optional[dict] = None
    image_nudge_sent: bool = False
    gold_followup_active: bool = False
    gold_last_product: Optional[str] = None
    gold_last_city: Optional[str] = None
    gold_shown_vendor_ids: list[str] = Field(default_factory=list)
    gold_candidate_vendor_names: list[str] = Field(default_factory=list)
    # Maps WhatsApp interactive reply IDs to full suggested-reply text (button titles truncate).
    whatsapp_suggestion_map: dict[str, str] = Field(default_factory=dict)


class ChatMessageRequest(BaseModel):
    """User message for the chat workflow."""

    message: str
    session_id: Optional[str] = None
    location: Optional[str] = None
    # When set (e.g. the gold.zwig.in host), the workflow is pinned to this
    # category and skips the generic product-intake — every message is treated
    # as a query within this category.
    category: Optional[str] = None


class ChatHistoryMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: Literal["assistant", "user"]
    content: str
    kind: Literal["text", "status", "results"] = "text"
    payload: Optional[dict] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChatSessionSummary(BaseModel):
    session_id: str
    title: str
    last_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ChatSessionDetail(BaseModel):
    session_id: str
    title: str
    state: Optional[ConversationState] = None
    messages: list[ChatHistoryMessage] = Field(default_factory=list)
    latest_search: Optional[dict] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ChatHistoryMessageCreate(BaseModel):
    message_id: Optional[str] = None
    role: Literal["assistant", "user"]
    content: str
    kind: Literal["text", "status", "results"] = "text"
    payload: Optional[dict] = None


class ChatMessageResponse(BaseModel):
    """Assistant reply plus optional search results."""

    session_id: str
    assistant_message: str
    state: ConversationState
    ready_to_search: bool = False
    suggested_replies: list[str] = Field(default_factory=list)
    results: Optional[SearchResponse] = None
    search_progress: Optional[SearchProgressSnapshot] = None
    # Legacy compatibility field. Gold searches now stay on the unified
    # taxonomy flow unless a dedicated host explicitly locks the category.
    redirect_to_gold: bool = False


class ResolveLocationRequest(BaseModel):
    latitude: float
    longitude: float


class ResolveLocationResponse(BaseModel):
    location: str
    formatted_address: Optional[str] = None


class LocationSuggestion(BaseModel):
    place_id: str
    description: str


class LocationSuggestionsResponse(BaseModel):
    suggestions: list[LocationSuggestion] = Field(default_factory=list)
