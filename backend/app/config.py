from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_category_router_model: str = "gpt-4o-mini"
    openai_gold_subcategory_model: str = "gpt-4o-mini"
    openai_gold_subcategory_model: str = "gpt-4o-mini"
    openai_visual_product_model: str = "gpt-4o-mini"
    # Transcript-extraction retry policy: how many times to retry a transient
    # rate-limit / connection / timeout error before falling back to the regex
    # parser, and the base (exponential) backoff between attempts. This keeps a
    # momentary 429 from silently degrading extraction to regex.
    voice_extraction_max_attempts: int = 5
    voice_extraction_backoff_seconds: float = 2.0
    # Small, fast model for WhatsApp intake routing only (responses.parse JSON).
    openai_whatsapp_router_model: str = "gpt-4o-mini"
    whatsapp_intake_router_enabled: bool = True
    serpapi_api_key: str = ""
    serpapi_base_url: str = "https://serpapi.com/search.json"
    google_places_api_key: str = ""
    bolna_api_key: str = ""
    bolna_agent_id: str = ""
    bolna_max_concurrent_calls: int = 5
    voice_provider: str = "bolna"
    voice_webhook_base_url: str = "http://localhost:8000"
    test_call_phone: str = ""
    demo_always_call_enabled: bool = False
    demo_always_call_phone: str = ""
    demo_always_call_city: str = "Mumbai"
    frontend_origins: str = "http://localhost:5173"
    mongodb_url: str = "mongodb://localhost:27017"
    database_name: str = "pricehunter"
    zwig_vendors_database_name: str = "zwig_vendors"
    bolna_webhook_url: str = "http://localhost:8000/api/webhooks/voice"
    mock_voice_calls: bool = False
    elevenlabs_api_key: str = ""
    elevenlabs_agent_id: str = ""
    elevenlabs_agent_phone_number_id: str = ""
    elevenlabs_webhook_secret: str = ""
    elevenlabs_use_conversation_overrides: bool = False
    elevenlabs_call_recording_enabled: bool = False
    elevenlabs_outbound_call_timeout_seconds: int = 30
    elevenlabs_max_concurrent_calls: int = 1
    elevenlabs_call_spacing_seconds: float = 2.0
    plivo_bridge_base_url: str = ""
    plivo_bridge_api_key: str = ""
    plivo_bridge_webhook_secret: str = ""
    plivo_bridge_max_concurrent_calls: int = 10
    plivo_bridge_call_spacing_seconds: float = 1.0
    plivo_bridge_call_timeout_seconds: int = 300
    pipecat_agent_base_url: str = ""
    pipecat_telephony_provider: str = "plivo"
    pipecat_max_concurrent_calls: int = 1
    pipecat_call_spacing_seconds: float = 1.0
    pipecat_call_timeout_seconds: int = 180
    pipecat_webhook_secret: str = ""
    gemini_live_enabled: bool = False
    gemini_live_telephony_provider: str = "plivo"
    gemini_live_max_concurrent_calls: int = 1
    gemini_live_call_spacing_seconds: float = 1.0
    gemini_live_call_timeout_seconds: int = 180
    # Hard server-side ceiling for Voice Lab bulk parallel dialing. The dashboard
    # exposes a per-run concurrency input (default 10); whatever the operator
    # enters is clamped to [1, this] so a typo can never fan out an unbounded
    # number of simultaneous live calls. Raise only after confirming the Plivo
    # account concurrent-call limit and pipecat-agent Cloud Run headroom.
    voice_lab_bulk_max_concurrency: int = 10
    voice_recordings_bucket: str = ""
    voice_recordings_prefix: str = "call-recordings"
    voice_recording_archive_enabled: bool = True
    voice_recording_archive_timeout_seconds: float = 30.0
    voice_recording_archive_max_bytes: int = 50_000_000
    product_images_bucket: str = ""
    product_images_prefix: str = "product-images"
    product_images_cdn_base_url: str = ""
    product_images_max_source_bytes: int = 8_000_000
    product_images_max_dimension: int = 700
    product_images_webp_quality: int = 82
    voice_lab_admin_token: str = ""
    voice_lab_login_password: str = ""
    plivo_auth_id: str = ""
    plivo_auth_token: str = ""
    exotel_api_key: str = ""
    exotel_api_token: str = ""
    flash_compare_enabled: bool = False
    flash_compare_categories: str = "electronics"
    flash_browser_timeout_ms: int = 120000
    flash_serpapi_timeout_seconds: int = 30
    flash_serpapi_retry_attempts: int = 3
    algolia_enabled: bool = False
    # When true, product search is Algolia-first: the user's raw query text is
    # sent to Algolia, Algolia's own ranking is preserved (no Python re-scoring),
    # and the chat asks a facet-driven disambiguation question for broad queries.
    algolia_first_search: bool = False
    algolia_app_id: str = ""
    algolia_admin_api_key: str = ""
    algolia_search_api_key: str = ""
    algolia_offerings_index: str = "vendor_offerings"
    algolia_capabilities_index: str = "vendor_capabilities"
    algolia_request_timeout_seconds: float = 8.0
    browser_use_api_key: str = ""
    browser_use_proxy_country: str = "in"
    browser_use_retry_attempts: int = 4
    browser_use_session_timeout_minutes: int = 5
    # Generic "auto" gold live-rate adapter (discover-once + cached recipe replay).
    gold_generic_adapter_enabled: bool = False
    # When enabled, Gold queries bypass the legacy Gold-specific selector/desk
    # and use the canonical taxonomy + vendor_offerings route like other categories.
    gold_use_unified_taxonomy_flow: bool = False
    # Max inline discoveries (browser+LLM) allowed per query, to bound latency.
    gold_generic_inline_discovery_budget: int = 2
    whatsapp_verify_token: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_api_version: str = "v25.0"
    whatsapp_flows_enabled: bool = False
    whatsapp_flow_message_version: str = "3"
    whatsapp_flows_private_key: str = ""
    whatsapp_flows_private_key_passphrase: str = ""
    whatsapp_gold_live_rates_flow_id: str = ""
    whatsapp_gold_live_rates_flow_cta: str = "View live rates"
    whatsapp_result_poll_interval_seconds: float = 2.0
    whatsapp_partial_result_after_seconds: int = 60
    whatsapp_result_timeout_seconds: int = 600
    whatsapp_typing_heartbeat_seconds: float = 20.0
    whatsapp_use_unified_search: bool = True
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    demo_paywall_amount_paise: int = 900

    # Amplitude
    amplitude_api_key: str = ""
    amplitude_host: str = "https://api2.amplitude.com"
    amplitude_disabled: bool = False

    # Apify
    apify_api_token: str = ""

    # Gold enrichment
    gold_tagging_model: str = "gpt-4o"
    gst_verification_api_url: str = ""
    gst_verification_api_key: str = ""

    # Meta Conversions API
    meta_pixel_id: str = ""
    meta_conversions_api_token: str = ""
    meta_api_version: str = "v21.0"

    # Sentry
    sentry_dsn: str = ""
    sentry_environment: str = "development"
    sentry_release: str = ""
    sentry_traces_sample_rate: float = 0.1
    sentry_enable_logs: bool = True

    # Web Push (PWA notifications via VAPID). Public key is safe to expose to
    # the browser; private key must be set as a secret. Subject is a mailto/URL
    # contact required by the Web Push protocol.
    web_push_enabled: bool = False
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:admin@zwig.in"

    # Dynamic extraction schema
    dynamic_extraction_enabled: bool = False
    dynamic_extraction_model: str = "gpt-4o"
    schema_generation_model: str = "gpt-4o-mini"
    dynamic_extraction_max_fields: int = 12
    dynamic_extraction_max_retries: int = 2
    dynamic_extraction_timeout_seconds: float = 8.0

    model_config = SettingsConfigDict(
        env_file=(
            WORKSPACE_ROOT / ".env",
            PROJECT_ROOT / ".env",
            BACKEND_DIR / ".env",
            BACKEND_DIR / ".env.local",
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
