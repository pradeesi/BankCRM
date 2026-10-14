# ==============================================================================
# Purpose: Production container image definition for the Aurelia Bank CRM
#   Model Context Protocol (MCP) Server.
# Architecture/Context: Standalone Python 3.12 slim container running Uvicorn
#   with Starlette + FastMCP over SSE / Streamable HTTP on port 8081.
# Dependencies/Side Effects: Installs dependencies from `requirements.txt` and
#   runs as a non-root user (`mcpuser`).
# Author: Pradeep Singh
# License: Apache-2.0
# ==============================================================================

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MCP_TRANSPORT=sse \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8081 \
    DATABASE_PATH=/tmp/bank_crm.db \
    AUTO_SEED_DATABASE=true

WORKDIR /app

RUN groupadd -r mcpgroup && useradd -r -g mcpgroup -d /app -s /sbin/nologin mcpuser

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chown -R mcpuser:mcpgroup /app

USER mcpuser

EXPOSE 8081

CMD ["python3", "server.py", "--transport", "sse", "--host", "0.0.0.0", "--port", "8081"]
