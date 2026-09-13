# PriceHunter

A multi-app AI agent that takes a shopping query, searches online stores and nearby local vendors, then ranks both into one result list.

**Live search:** https://ron-passengers-changing-ages.trycloudflare.com/#/app

## Project overview

PriceHunter turns a natural-language request like “1 ton AC in Andheri” into a structured search, then:

1. Uses OpenAI to extract product, category, location, and intent.
2. Pulls live online listings through SerpApi (Google Shopping).
3. Finds nearby vendors through Google Places.
4. Optionally calls those vendors by phone (Bolna / Plivo / ElevenLabs). When those credentials are missing, it uses mock call transcripts so the rest of the agent still runs.

The web app is a chat UI at `/#/app`. Results come back as a single ranked list of online and offline prices.

## External apps used

The agent takes action across **at least three** external apps:

| App | What the agent does |
|---|---|
| **OpenAI** | Structures the query and ranks / explains results |
| **SerpApi (Google Shopping)** | Fetches live online prices |
| **Google Places** | Discovers nearby local vendors |
| **Bolna / Plivo / ElevenLabs** | Places outbound voice calls to vendors for live quotes |

Search still works if a provider is down: missing SerpApi falls back to demo online listings, missing Places falls back to mock Indian vendors, and missing voice credentials (or `MOCK_VOICE_CALLS=true`) returns instant mock transcripts.

## Setup instructions

**Requirements:** Python 3.11+, Node 20+, and (optional) MongoDB.

```bash
cp .env.example .env
# Add OPENAI_API_KEY, SERPAPI_API_KEY, and GOOGLE_PLACES_API_KEY for live data.
# Leave them blank to run the demo fallbacks.

python3.11 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

cd frontend
npm install
VITE_API_URL= npm run build
cd ..

MOCK_VOICE_CALLS=true FLASH_COMPARE_ENABLED=false \
  uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
```

Open [http://localhost:8000/#/app](http://localhost:8000/#/app).

Local frontend-only development:

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173/#/app
cd backend && uvicorn app.main:app --reload  # http://localhost:8000
```

Docker:

```bash
cp .env.example .env
docker compose up --build
```

## Reliability testing

How we verified the agent works:

- **Provider fallbacks.** Each external app is optional. The API logs which credentials are present on startup and degrades instead of failing the search.
- **Health check.** `GET /health` returns `{"status":"ok"}`.
- **Voice isolation.** `MOCK_VOICE_CALLS=true` keeps search usable without placing real phone calls.
- **Mongo optional.** If MongoDB is unreachable, search still returns results; persistence logs a warning.
- **Automated tests.** Backend tests live in `backend/tests` and `backend/test_*.py`. From `backend/`:

```bash
python -m pytest tests test_deploy_wiring.py test_comparator_attributes.py -q
```

## Demo video

**Demo (≤2 minutes):** https://drive.google.com/file/d/1triJIroP4wk5hHDQiI4FasVIL1hijzYM/view?usp=sharing

**Live search:** https://ron-passengers-changing-ages.trycloudflare.com/#/app
