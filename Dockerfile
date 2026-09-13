FROM node:20-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV VITE_API_URL=
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ /app/
COPY --from=frontend /frontend/dist /frontend/dist
ENV FRONTEND_DIST=/frontend/dist
ENV MOCK_VOICE_CALLS=true
ENV FLASH_COMPARE_ENABLED=false
ENV AMPLITUDE_DISABLED=true
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
