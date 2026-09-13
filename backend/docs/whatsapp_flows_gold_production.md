# WhatsApp Gold Live Rates Flow

## Files

- Flow asset JSON:
  - `app/whatsapp_flows/assets/gold_live_rates_flow.json`
- Encrypted production endpoint:
  - `POST /api/whatsapp/flows/endpoint`
- Local plain JSON endpoint:
  - `POST /api/whatsapp/flows/data-exchange`

## Environment

Set these backend env vars:

- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_VERIFY_TOKEN`
- `WHATSAPP_APP_SECRET`
- `WHATSAPP_FLOWS_ENABLED=true`
- `WHATSAPP_FLOW_MESSAGE_VERSION=3`
- `WHATSAPP_FLOWS_PRIVATE_KEY`
- `WHATSAPP_FLOWS_PRIVATE_KEY_PASSPHRASE`
- `WHATSAPP_GOLD_LIVE_RATES_FLOW_ID`
- `WHATSAPP_GOLD_LIVE_RATES_FLOW_CTA=View live rates`

## Generate Keys

```bash
cd /Users/panktishah/pricing-agent/pricing-agent
PYTHONPATH=pricehunter/backend python pricehunter/backend/scripts/generate_whatsapp_flow_keys.py --passphrase "your-passphrase"
```

Use:

- private key -> `WHATSAPP_FLOWS_PRIVATE_KEY`
- passphrase -> `WHATSAPP_FLOWS_PRIVATE_KEY_PASSPHRASE`
- public key -> upload to the WhatsApp business phone number encryption settings

## Deploy Requirements

- Public HTTPS backend URL
- `/api/whatsapp/webhook` reachable by Meta
- `/api/whatsapp/flows/endpoint` reachable by Meta
- backend must have `cryptography` installed

## Meta Setup

1. Upload the public key for Flow data channel encryption to the business phone number.
2. Create a new Flow in WhatsApp Manager.
3. Upload `app/whatsapp_flows/assets/gold_live_rates_flow.json`.
4. Set the Flow data channel endpoint to:
   - `https://<your-domain>/api/whatsapp/flows/endpoint`
5. Publish the Flow.
6. Copy the published Flow ID into:
   - `WHATSAPP_GOLD_LIVE_RATES_FLOW_ID`

## Runtime Behavior

- Gold bullion WhatsApp searches send a Flow CTA when live rates exist.
- Flow open -> Meta calls encrypted endpoint -> backend returns the live rate board.
- Flow submit -> WhatsApp webhook receives `interactive.nfm_reply.response_json`.
- Backend handles:
  - `call_top_dealers`
  - `whatsapp_top_dealers`
  - `show_more_dealers`

## Fallback

If Flows are disabled or Flow sending fails, the system falls back to plain WhatsApp text live-rate messages.
