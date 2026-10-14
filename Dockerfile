# ==============================================================================
# Purpose: Production container image definition for Aurelia Bank CRM.
# Architecture/Context: Multi-layer slim Python 3.12 container running Gunicorn
#   WSGI workers; compatible with Google Cloud Run and Google Kubernetes Engine.
# Dependencies/Side Effects: Installs packages from `requirements.txt` and runs
#   as a non-root system user (`crmuser`) on port 8080.
# Author: Pradeep Singh
# License: Apache-2.0
# ==============================================================================

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HOST=0.0.0.0 \
    DATABASE_PATH=/tmp/bank_crm.db \
    AUTO_SEED_DATABASE=true

WORKDIR /app

# Create non-privileged application user for container security
RUN groupadd -r crmgroup && useradd -r -g crmgroup -d /app -s /sbin/nologin crmuser

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chown -R crmuser:crmgroup /app

USER crmuser

EXPOSE 8080

# Use 1 worker with 8 threads when backed by local/ephemeral SQLite so all threads share the WAL state cleanly
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "60", "app:app"]
