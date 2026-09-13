FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Local browser for the generic gold adapter's render tier ────────────────
# Enables render-based discovery (inline + nightly seed Job). Adds ~400MB and
# needs more memory at runtime (deploy with BACKEND_MEMORY=2Gi / JOB_MEMORY=2Gi).
RUN python -m playwright install --with-deps chromium

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
