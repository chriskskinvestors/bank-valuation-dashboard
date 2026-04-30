FROM python:3.11-slim

WORKDIR /app

# Install curl for health checks
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create writable directories for runtime data
RUN mkdir -p /app/consensus /app/estimates_cache /app/lists

# Cloud Run sets PORT env var (default 8080)
ENV PORT=8080

# GCS bucket for persistent storage
ENV GCS_BUCKET=ksk-bank-dashboard-data

# Health check
HEALTHCHECK CMD curl --fail http://localhost:${PORT}/_stcore/health || exit 1

# Run Streamlit on the PORT Cloud Run provides
ENTRYPOINT ["sh", "-c", "streamlit run app.py \
    --server.port=${PORT} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false \
    --browser.gatherUsageStats=false"]
