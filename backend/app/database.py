from __future__ import annotations

import asyncio
import logging

import certifi
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket
from pymongo import ASCENDING, DESCENDING, GEOSPHERE

from app.config import settings

logger = logging.getLogger(__name__)

client = AsyncIOMotorClient(
    settings.mongodb_url,
    tls=True,
    tlsCAFile=certifi.where(),
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=5000,
    retryWrites=True,
)
db = client[settings.database_name]
zwig_vendors_db = client[settings.zwig_vendors_database_name]

search_sessions_collection = db["search_sessions"]
chat_sessions_collection = db["chat_sessions"]
chat_messages_collection = db["chat_messages"]
call_attempts_collection = db["call_attempts"]
online_results_collection = db["online_results"]
vendor_profiles_collection = db["vendor_profiles"]
product_catalog_collection = db["product_catalog"]
vendor_product_observations_collection = db["vendor_product_observations"]
price_history_collection = db["price_history"]
raw_webhooks_collection = db["raw_webhooks"]
whatsapp_sessions_collection = db["whatsapp_sessions"]
whatsapp_messages_collection = db["whatsapp_messages"]
scraper_runs_collection = db["scraper_runs"]
scraped_vendor_records_collection = db["scraped_vendor_records"]
voice_lab_sessions_collection = db["voice_lab_sessions"]
voice_webhook_payloads_collection = db["voice_webhook_payloads"]
voice_lab_campaign_presets_collection = db["voice_lab_campaign_presets"]
# Human-in-the-loop fulfilment requests (gold.zwig.in): a user query → a request
# ops picks up on the dashboard and replies to with supplier quotes.
gold_requests_collection = db["gold_requests"]

# Web Push subscriptions for PWA/TWA notifications, keyed by device_id (a user
# may have several browsers/devices; endpoint is the unique key).
push_subscriptions_collection = db["push_subscriptions"]
supplier_pilot_acceptances_collection = db["supplier_pilot_acceptances"]
supplier_documents_collection = db["supplier_documents"]
supplier_activity_collection = db["supplier_activity"]
supplier_loi_agreements_collection = db["supplier_loi_agreements"]

# GridFS bucket for supplier documents. Constructed LAZILY and per-loop:
# instantiating an AsyncIOMotorGridFSBucket calls the Motor client's
# get_io_loop(), which binds it to whatever event loop is current at that
# moment. Doing that at module-import time binds it to the import-time loop and
# then every asyncio.run() job script (which runs under a fresh loop) fails with
# "got Future attached to a different loop". Building it on first use — inside
# whatever loop is actually running — avoids that. We also cache PER LOOP so a
# process that runs multiple asyncio.run() invocations (e.g. pytest-asyncio,
# where each test gets a fresh loop) doesn't reuse a bucket bound to a closed
# loop.
_supplier_documents_bucket: AsyncIOMotorGridFSBucket | None = None
_supplier_documents_bucket_loop: asyncio.AbstractEventLoop | None = None


def get_supplier_documents_bucket() -> AsyncIOMotorGridFSBucket:
    """Lazily construct (and per-loop cache) the supplier-documents GridFS bucket.

    Must be called from within a running event loop (both call sites await it)."""
    global _supplier_documents_bucket, _supplier_documents_bucket_loop
    running_loop = asyncio.get_running_loop()
    if _supplier_documents_bucket is None or _supplier_documents_bucket_loop is not running_loop:
        _supplier_documents_bucket = AsyncIOMotorGridFSBucket(db, bucket_name="supplier_documents_fs")
        _supplier_documents_bucket_loop = running_loop
    return _supplier_documents_bucket

# Generalized product-to-vendor routing collections. These are additive to the
# existing product_catalog/vendor_profiles collections so older flows keep
# working while new categories move to the offering-index architecture.
catalog_categories_collection = db["categories"]
catalog_products_collection = db["products"]
supplier_vendors_collection = db["vendors"]
vendor_channels_collection = db["vendor_channels"]
vendor_offerings_collection = db["vendor_offerings"]
taxonomy_nodes_collection = db["taxonomy_nodes"]
taxonomy_mappings_collection = db["taxonomy_mappings"]
vendor_capabilities_collection = db["vendor_capabilities"]
vendor_identity_links_collection = db["vendor_identity_links"]
price_observations_collection = db["price_observations"]
current_price_snapshots_collection = db["current_price_snapshots"]
catalog_vendor_interactions_collection = db["vendor_interactions"]

zwig_vendor_clusters_collection = zwig_vendors_db["clusters"]
zwig_vendor_categories_collection = zwig_vendors_db["categories"]
zwig_vendors_collection = zwig_vendors_db["vendors"]
zwig_vendor_channels_collection = zwig_vendors_db["vendor_channels"]
zwig_live_rate_snapshots_collection = zwig_vendors_db["live_rate_snapshots"]
zwig_vendor_category_links_collection = zwig_vendors_db["vendor_categories"]
zwig_vendor_interactions_collection = zwig_vendors_db["vendor_interactions"]
zwig_vendor_calls_collection = zwig_vendors_db["vendor_calls"]
zwig_scrape_jobs_collection = zwig_vendors_db["scrape_jobs"]

# Legacy aliases kept so older code paths do not break while persistence is migrated.
queries_collection = search_sessions_collection
results_collection = db["results"]
vendors_collection = vendor_profiles_collection


async def init_database() -> None:
    try:
        await chat_sessions_collection.create_index([("session_id", ASCENDING)], unique=True)
        await chat_sessions_collection.create_index([("updated_at", ASCENDING)])
        await chat_sessions_collection.create_index([("request_metadata.device_id", ASCENDING), ("updated_at", ASCENDING)])

        await chat_messages_collection.create_index([("session_id", ASCENDING), ("created_at", ASCENDING)])
        await chat_messages_collection.create_index([("message_id", ASCENDING)], unique=True)

        await search_sessions_collection.create_index([("search_id", ASCENDING)], unique=True)
        await search_sessions_collection.create_index([("created_at", ASCENDING)])
        await search_sessions_collection.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
        await search_sessions_collection.create_index([("request_metadata.device_id", ASCENDING)])
        await search_sessions_collection.create_index([("request_metadata.ip", ASCENDING)])
        await search_sessions_collection.create_index([("query.category", ASCENDING), ("created_at", ASCENDING)])
        await search_sessions_collection.create_index([("query.product", ASCENDING), ("created_at", ASCENDING)])

        await call_attempts_collection.create_index([("call_id", ASCENDING)], unique=True)
        await call_attempts_collection.create_index([("search_id", ASCENDING), ("started_at", ASCENDING)])
        await call_attempts_collection.create_index([("vendor.vendor_key", ASCENDING), ("started_at", ASCENDING)])
        await call_attempts_collection.create_index([("status", ASCENDING), ("started_at", ASCENDING)])

        await online_results_collection.create_index([("search_id", ASCENDING), ("fetched_at", ASCENDING)])
        await online_results_collection.create_index([("platform.platform_id", ASCENDING), ("fetched_at", ASCENDING)])
        await online_results_collection.create_index([("vendor.vendor_key", ASCENDING), ("fetched_at", ASCENDING)])

        await vendor_profiles_collection.create_index([("vendor_key", ASCENDING)], unique=True)
        await vendor_profiles_collection.create_index(
            [("place_id", ASCENDING)],
            unique=True,
            sparse=True,
        )
        await vendor_profiles_collection.create_index([("phone", ASCENDING)])
        await vendor_profiles_collection.create_index([("location.pincode", ASCENDING), ("last_seen_at", ASCENDING)])

        await product_catalog_collection.create_index([("product_key", ASCENDING)], unique=True)
        await product_catalog_collection.create_index([("category", ASCENDING), ("canonical_name", ASCENDING)])
        await product_catalog_collection.create_index([("aliases", ASCENDING)])

        await vendor_product_observations_collection.create_index(
            [("observation_key", ASCENDING)],
            unique=True,
        )
        await vendor_product_observations_collection.create_index(
            [("product.product_key", ASCENDING), ("location.pincode", ASCENDING), ("last_observed_at", ASCENDING)]
        )
        await vendor_product_observations_collection.create_index(
            [("vendor.vendor_key", ASCENDING), ("last_observed_at", ASCENDING)]
        )
        await vendor_product_observations_collection.create_index([("expires_at", ASCENDING)])

        await price_history_collection.create_index([("search_id", ASCENDING), ("observed_at", ASCENDING)])
        await price_history_collection.create_index([("product.product_key", ASCENDING), ("observed_at", ASCENDING)])
        await price_history_collection.create_index([("vendor.vendor_key", ASCENDING), ("observed_at", ASCENDING)])

        await raw_webhooks_collection.create_index([("execution_id", ASCENDING), ("received_at", ASCENDING)])

        await scraper_runs_collection.create_index([("run_id", ASCENDING)], unique=True)
        await scraper_runs_collection.create_index([("platform.platform_id", ASCENDING), ("started_at", ASCENDING)])

        await scraped_vendor_records_collection.create_index([("record_key", ASCENDING)], unique=True)
        await scraped_vendor_records_collection.create_index([("run_id", ASCENDING), ("scraped_at", ASCENDING)])
        await scraped_vendor_records_collection.create_index([("platform.platform_id", ASCENDING), ("scraped_at", ASCENDING)])
        await scraped_vendor_records_collection.create_index([("vendor.vendor_key", ASCENDING), ("scraped_at", ASCENDING)])

        await voice_lab_sessions_collection.create_index([("search_id", ASCENDING)], unique=True)
        await voice_lab_sessions_collection.create_index([("created_at", ASCENDING)])

        await voice_webhook_payloads_collection.create_index(
            [("provider", ASCENDING), ("call_id", ASCENDING)],
            unique=True,
        )
        await voice_webhook_payloads_collection.create_index([("received_at", ASCENDING)])

        await voice_lab_campaign_presets_collection.create_index(
            [("preset_id", ASCENDING)],
            unique=True,
        )
        await voice_lab_campaign_presets_collection.create_index([("updated_at", ASCENDING)])

        await whatsapp_sessions_collection.create_index([("wa_id", ASCENDING)], unique=True)
        await whatsapp_sessions_collection.create_index([("phone_number", ASCENDING)])
        await whatsapp_sessions_collection.create_index([("session_id", ASCENDING)])
        await whatsapp_sessions_collection.create_index([("updated_at", ASCENDING)])

        await whatsapp_messages_collection.create_index([("message_id", ASCENDING)], unique=True)
        await whatsapp_messages_collection.create_index([("wa_id", ASCENDING), ("received_at", ASCENDING)])
        await whatsapp_messages_collection.create_index([("session_id", ASCENDING), ("received_at", ASCENDING)])

        await gold_requests_collection.create_index([("request_id", ASCENDING)], unique=True)
        await gold_requests_collection.create_index([("session_id", ASCENDING), ("created_at", ASCENDING)])
        await gold_requests_collection.create_index([("status", ASCENDING), ("created_at", DESCENDING)])
        await gold_requests_collection.create_index([("created_at", DESCENDING)])

        await push_subscriptions_collection.create_index([("endpoint", ASCENDING)], unique=True)
        await push_subscriptions_collection.create_index([("device_id", ASCENDING), ("updated_at", DESCENDING)])
        await push_subscriptions_collection.create_index([("session_ids", ASCENDING)])

        await supplier_vendors_collection.create_index([("pilotToken", ASCENDING)], unique=True, sparse=True)
        await supplier_vendors_collection.create_index([("pilotStatus", ASCENDING)])
        await supplier_vendors_collection.create_index([("pilotAcceptedAt", DESCENDING)])
        await supplier_pilot_acceptances_collection.create_index([("acceptance_id", ASCENDING)], unique=True)
        await supplier_pilot_acceptances_collection.create_index([("supplier_id", ASCENDING), ("accepted_at", DESCENDING)])
        await supplier_pilot_acceptances_collection.create_index([("pilot_token", ASCENDING)])
        await supplier_documents_collection.create_index([("document_id", ASCENDING)], unique=True)
        await supplier_documents_collection.create_index([("supplier_id", ASCENDING), ("uploaded_at", DESCENDING)])
        await supplier_documents_collection.create_index([("document_type", ASCENDING), ("extraction_status", ASCENDING)])
        await supplier_activity_collection.create_index([("activity_id", ASCENDING)], unique=True)
        await supplier_activity_collection.create_index([("supplier_id", ASCENDING), ("occurred_at", DESCENDING)])
        await supplier_activity_collection.create_index([("activity_type", ASCENDING), ("occurred_at", DESCENDING)])
        await supplier_loi_agreements_collection.create_index([("agreement_id", ASCENDING)], unique=True)
        await supplier_loi_agreements_collection.create_index([("supplier_id", ASCENDING), ("created_at", DESCENDING)])
        await supplier_loi_agreements_collection.create_index([("loiToken", ASCENDING)], unique=True, sparse=True)

        await taxonomy_nodes_collection.create_index([("node_id", ASCENDING)], unique=True)
        await taxonomy_nodes_collection.create_index([("parent_id", ASCENDING), ("level", ASCENDING)])
        await taxonomy_nodes_collection.create_index([("root_id", ASCENDING), ("level", ASCENDING)])
        await taxonomy_nodes_collection.create_index([("aliases", ASCENDING)])
        await taxonomy_mappings_collection.create_index([("mapping_key", ASCENDING)], unique=True)
        await taxonomy_mappings_collection.create_index([("taxonomy_node_id", ASCENDING)])
        await taxonomy_mappings_collection.create_index([("needs_review", ASCENDING), ("confidence", ASCENDING)])
        await vendor_capabilities_collection.create_index(
            [("vendor_id", ASCENDING), ("taxonomy_node_id", ASCENDING)],
            unique=True,
        )
        await vendor_capabilities_collection.create_index([("taxonomy_path", ASCENDING)])
        await vendor_capabilities_collection.create_index([("canonical_category_id", ASCENDING), ("capability_confidence", DESCENDING)])
        await vendor_capabilities_collection.create_index([("quality_status", ASCENDING)])
        await vendor_capabilities_collection.create_index([("routing_confidence", DESCENDING)])
        await vendor_capabilities_collection.create_index([("service_locations.city", ASCENDING)])
        await vendor_identity_links_collection.create_index(
            [("source_system", ASCENDING), ("source_collection", ASCENDING), ("source_vendor_id", ASCENDING)],
            unique=True,
        )
        await vendor_identity_links_collection.create_index([("canonical_vendor_id", ASCENDING)])
        await vendor_identity_links_collection.create_index([("status", ASCENDING), ("match_type", ASCENDING)])
        await vendor_channels_collection.create_index([("channel_id", ASCENDING)], unique=True)
        await vendor_channels_collection.create_index([("vendor_id", ASCENDING), ("category_id", ASCENDING)])
        await vendor_channels_collection.create_index(
            [("category_id", ASCENDING), ("channel_type", ASCENDING), ("is_active", ASCENDING), ("priority", ASCENDING)]
        )
        await vendor_channels_collection.create_index(
            [("metadata.fetch_adapter", ASCENDING), ("channel_type", ASCENDING), ("is_active", ASCENDING)]
        )
        await vendor_offerings_collection.create_index([("taxonomy_node_id", ASCENDING), ("supply_confidence", DESCENDING)])
        await vendor_offerings_collection.create_index([("taxonomy_path", ASCENDING)])
        await vendor_offerings_collection.create_index([("canonical_category_id", ASCENDING), ("canonical_subcategory_id", ASCENDING)])
        await vendor_offerings_collection.create_index([("needs_taxonomy_review", ASCENDING), ("taxonomy_confidence", ASCENDING)])
        await vendor_offerings_collection.create_index([("offering_id", ASCENDING)], unique=True)
        await vendor_offerings_collection.create_index([("vendor_id", ASCENDING), ("taxonomy_node_id", ASCENDING)])
        await price_observations_collection.create_index([("observation_id", ASCENDING)], unique=True)
        await price_observations_collection.create_index([("vendor_id", ASCENDING), ("offering_id", ASCENDING), ("observed_at", DESCENDING)])
        await current_price_snapshots_collection.create_index([("vendor_id", ASCENDING), ("offering_id", ASCENDING)], unique=True)
        await current_price_snapshots_collection.create_index([("offering_id", ASCENDING)])
        await current_price_snapshots_collection.create_index([("category_id", ASCENDING), ("last_observed_at", DESCENDING)])

        # Vendor reliability + quote cache (electronics + generic)
        from app.services.vendor_reliability import init_indexes as _init_reliability_indexes
        await _init_reliability_indexes()
    except Exception as exc:  # pragma: no cover - depends on external service
        logger.warning("MongoDB index setup failed: %s", exc)


async def init_zwig_vendors_database() -> None:
    collection_order = [
        "clusters",
        "categories",
        "vendors",
        "vendor_channels",
        "live_rate_snapshots",
        "vendor_categories",
        "vendor_interactions",
        "vendor_calls",
        "scrape_jobs",
    ]

    try:
        existing_collections = set(await zwig_vendors_db.list_collection_names())
        for collection_name in collection_order:
            if collection_name in existing_collections:
                continue
            await zwig_vendors_db.create_collection(collection_name)
            logger.info(
                "Created MongoDB collection %s.%s",
                settings.zwig_vendors_database_name,
                collection_name,
            )

        migration_result = await zwig_vendor_channels_collection.update_many(
            {"channel_type": "live_script"},
            {"$set": {"channel_type": "website"}},
        )
        if migration_result.modified_count:
            logger.info(
                "Migrated %s vendor_channels documents from live_script to website",
                migration_result.modified_count,
            )

        await zwig_vendor_category_links_collection.create_index(
            [
                ("category_id", ASCENDING),
                ("serves_b2b", ASCENDING),
                ("confidence_score", DESCENDING),
            ]
        )
        await zwig_vendor_category_links_collection.create_index(
            [("vendor_id", ASCENDING), ("category_id", ASCENDING)],
            unique=True,
        )
        await zwig_vendors_collection.create_index(
            [("phone_primary", ASCENDING)],
            unique=True,
            sparse=True,
        )
        await zwig_vendors_collection.create_index(
            [("vendor_id", ASCENDING)],
            unique=True,
        )
        await zwig_vendors_collection.create_index([("pilotToken", ASCENDING)], unique=True, sparse=True)
        await zwig_vendors_collection.create_index([("pilotStatus", ASCENDING)])
        await zwig_vendors_collection.create_index([("pilotAcceptedAt", DESCENDING)])
        await zwig_vendors_collection.create_index(
            [("google_place_id", ASCENDING)],
            unique=True,
            sparse=True,
        )
        await zwig_vendor_calls_collection.create_index(
            [("vendor_id", ASCENDING), ("called_at", DESCENDING)]
        )
        await zwig_vendor_channels_collection.create_index(
            [
                ("vendor_id", ASCENDING),
                ("category_id", ASCENDING),
                ("channel_type", ASCENDING),
                ("channel_key", ASCENDING),
            ],
            unique=True,
        )
        await zwig_vendor_channels_collection.create_index(
            [
                ("category_id", ASCENDING),
                ("channel_type", ASCENDING),
                ("is_active", ASCENDING),
                ("priority", ASCENDING),
            ]
        )
        await zwig_vendor_channels_collection.create_index(
            [("metadata.fetch_adapter", ASCENDING), ("channel_type", ASCENDING), ("is_active", ASCENDING)]
        )
        await zwig_vendor_channels_collection.create_index(
            [("city", ASCENDING), ("channel_type", ASCENDING), ("is_active", ASCENDING)]
        )
        await zwig_live_rate_snapshots_collection.create_index(
            [("category_id", ASCENDING), ("city", ASCENDING), ("fetched_at", DESCENDING)]
        )
        await zwig_live_rate_snapshots_collection.create_index(
            [("vendor_id", ASCENDING), ("script_name", ASCENDING), ("fetched_at", DESCENDING)]
        )
        await zwig_live_rate_snapshots_collection.create_index([("rate_timestamp", DESCENDING)])
        # TTL: the snapshots collection is append-only (a new row per poll/tick),
        # so it grows unbounded. Every reader (board, national consensus, live-rate
        # preview, vendor flow) only ever looks back FRESH_SNAPSHOT_HOURS (48h) and
        # no offline job reads it, so expiring rows 7 days after write is safe and
        # keeps the collection bounded. 7d leaves a wide margin over the 48h window.
        # NOTE: Mongo TTL only expires docs whose `fetched_at` is a BSON Date;
        # malformed/missing values are simply left in place (harmless).
        await zwig_live_rate_snapshots_collection.create_index(
            [("fetched_at", ASCENDING)], expireAfterSeconds=7 * 24 * 60 * 60
        )
        await zwig_vendor_interactions_collection.create_index(
            [("vendor_id", ASCENDING), ("interaction_type", ASCENDING), ("occurred_at", DESCENDING)]
        )
        await zwig_vendor_interactions_collection.create_index(
            [("query_id", ASCENDING), ("occurred_at", DESCENDING)]
        )
        await zwig_vendors_collection.create_index([("location", GEOSPHERE)])
        await zwig_scrape_jobs_collection.create_index([("job_id", ASCENDING)], unique=True)
    except Exception as exc:  # pragma: no cover - depends on external service
        logger.warning(
            "zwig_vendors database setup failed for %s: %s",
            settings.zwig_vendors_database_name,
            exc,
        )


async def ping_database() -> bool:
    try:
        await client.admin.command("ping")
        return True
    except Exception as exc:  # pragma: no cover - depends on external service
        logger.warning("MongoDB ping failed: %s", exc)
        return False
