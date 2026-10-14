# Aurelia Private Bank & Trust — Model Context Protocol (MCP) Server

A standalone **Model Context Protocol (MCP) Server** (`aurelia-bank-crm-mcp`) built with the official Python `mcp` SDK (`FastMCP`), `Starlette`, and `Uvicorn`. It integrates directly with the Aurelia Bank CRM SQLite database (`../bank_crm.db` in Write-Ahead Logging mode) to expose **10 banking CRM tools**, **2 MCP resources**, and **2 MCP prompts** to external AI agents (Google ADK, Vertex AI Agent Engine, Apigee / Agent Gateway, Claude Desktop, Cursor, or LangChain).

---

## Application Architecture

```
+-------------------------------------------------------------------------+
|                        External AI Agents & Clients                     |
|  - Google ADK / Vertex AI Agents / Apigee MCP Proxy (`SSE` / `HTTP`)    |
|  - Local CLI / IDE Agents (`stdio` JSON-RPC)                            |
+------------------------------------+------------------------------------+
                                     | MCP Protocol (SSE / HTTP / stdio)
                                     v
+-------------------------------------------------------------------------+
|                    MCP Server Layer (`mcp_server/server.py`)            |
|  - Optional API Key Auth Middleware (`X-API-Key` / `Bearer`)            |
|  - Readiness & Liveness Probe (`GET /health`)                           |
|  - Structured Tool Audit Logger (`MCP_TOOL_AUDIT` on stderr)            |
|  - 6 Read & Analytics Tools + 4 Governed Action Tools                   |
|  - 2 MCP Resources (`bank://summary`, `bank://customers`)               |
|  - 2 MCP Prompts (`wealth_review_prompt`, `aml_investigation_prompt`)   |
+------------------------------------+------------------------------------+
                                     | Parameterized Domain Calls
                                     v
+-------------------------------------------------------------------------+
|             Self-Contained Repository (`mcp_server/db_client.py`)       |
|  - Concurrent SQLite WAL Connection Manager (`get_connection`)          |
|  - FinCEN/OFAC Wire Hold Rules & Basel III LTV/DTI Underwriting         |
|  - Automatic AUM Recalculation & Standalone Container Seed Fallback     |
+------------------------------------+------------------------------------+
                                     | Shared WAL File (`../bank_crm.db`)
                                     v
+-------------------------------------------------------------------------+
|              Shared BankCRM SQLite 3 Database (`bank_crm.db`)           |
|  Live bi-directional synchronization with the Flask CRM Web Portal      |
+-------------------------------------------------------------------------+
```

### Exposed MCP Tool Catalog (10 Tools)

| # | Tool Name | Type | Description |
| :--- | :--- | :--- | :--- |
| 1 | `search_customers` | Read | Search/filter clients by name, company, CIF, city, tier, or KYC status. |
| 2 | `get_customer_360_dossier` | Read | Retrieve complete Customer 360 dossier (accounts, wires, loans, interactions, tasks, NBA offers). |
| 3 | `get_bank_executive_summary` | Read | Bank-wide AUM, active loan pipeline, net liquidity inflows, and AML flagged wire metrics. |
| 4 | `list_credit_loan_pipeline` | Read | List commercial and private wealth loan facilities with LTV, DTI, and underwriting grades. |
| 5 | `list_aml_flagged_transactions` | Read | List SWIFT/FEDWIRE transfers flagged with FinCEN/OFAC compliance holds (`risk_flag = 1`). |
| 6 | `get_nba_product_recommendations` | Read | Retrieve AI propensity-scored Next-Best-Action wealth/treasury product mandates. |
| 7 | `execute_wire_or_transfer` | Write | Execute an atomic deposit credit, outgoing wire debit, or internal book transfer with AML screening. |
| 8 | `resolve_aml_compliance_hold` | Write | Perform Dual-Control Compliance Officer sign-off to release a flagged wire hold. |
| 9 | `originate_credit_facility` | Write | Originate a new credit facility with automated LTV, DTI, and Basel III credit grade calculation. |
| 10 | `log_client_interaction_and_task` | Write | Log an advisor/agent touchpoint on the client's 360 timeline and optionally schedule a follow-up task. |

---

## Project Directory Structure

```text
BankCRM/mcp_server/
├── .env.example                 # Template environment variables for the MCP Server
├── .gitignore                   # Excludes .env, credentials, venv, and runtime DB files
├── Dockerfile                   # Standalone Python 3.12 + Uvicorn container image
├── README.md                    # MCP architecture, tool catalog, local & cloud deployment guide
├── requirements.txt             # Pinned MCP SDK (`mcp[cli]`), Uvicorn, Starlette, httpx, dotenv
├── config.py                    # Environment validator and stderr-routed structured logger
├── db_client.py                 # Self-contained SQLite WAL repository & standalone seeder
├── server.py                    # FastMCP server with 10 tools, 2 resources, 2 prompts & ASGI app
├── verify_mcp_client.py         # Automated end-to-end test client for both stdio and SSE modes
└── k8s/
    └── mcp-deployment.yaml      # Kubernetes Deployment & LoadBalancer Service for GKE
```

---

## Frameworks & Libraries

| Component | Package / Version | Purpose |
| :--- | :--- | :--- |
| **MCP SDK** | `mcp[cli]>=1.2.0` (`FastMCP`) | Official Model Context Protocol server and client implementation |
| **ASGI Framework** | `starlette>=0.38.0` | HTTP routing (`/health`, `/sse`, `/messages/`) and API key middleware |
| **ASGI Server** | `uvicorn>=0.30.0` | High-performance async HTTP/SSE server |
| **HTTP Client** | `httpx>=0.27.0` | Used by `verify_mcp_client.py` for SSE and health probe testing |
| **Schema Validation** | `pydantic>=2.8.0` | Strict JSON Schema generation and input validation for MCP tool arguments |
| **Configuration** | `python-dotenv==1.0.1` | Externalized `.env` configuration loader |

---

## Environment Variables

Copy `.env.example` to `.env` inside `mcp_server/` to customize runtime behavior:

| Variable Name | Type | Default / Example | Purpose |
| :--- | :--- | :--- | :--- |
| `MCP_SERVER_NAME` | `string` | `aurelia-bank-crm-mcp` | Registered MCP server name returned during capability negotiation |
| `MCP_TRANSPORT` | `string` | `sse` | Default transport (`sse`, `streamable-http`, or `stdio`) |
| `MCP_HOST` | `string` | `0.0.0.0` | Network interface bind address for HTTP/SSE mode |
| `MCP_PORT` | `integer` | `8081` | TCP port for HTTP/SSE server (`1`–`65535`) |
| `DATABASE_PATH` | `string` | `../bank_crm.db` | Path to the shared BankCRM SQLite file (or standalone container path) |
| `AUTO_SEED_DATABASE` | `boolean` | `true` | Automatically creates schema and seeds baseline data if DB is empty |
| `HIGH_VALUE_WIRE_THRESHOLD_USD` | `float` | `25000.00` | USD threshold triggering automated SWIFT/AML compliance holds |
| `DEFAULT_AGENT_ACTOR` | `string` | `Aurelia AI Advisory Agent` | Default advisor/agent name recorded on logged interactions |
| `MCP_API_KEY` | `string` | *(empty = disabled)* | Optional secret token enforced via `X-API-Key` or `Authorization: Bearer` |
| `LOG_LEVEL` | `string` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## No Hardcoding Rule

- Zero API keys, credentials, or environment-specific paths are hardcoded in source files.
- All configuration is loaded via `config.py` from OS environment variables or `mcp_server/.env` (excluded via `.gitignore`).
- All logs are emitted to `stderr` so `stdio` JSON-RPC framing is never polluted.

---

## Local Execution Instructions

```bash
# 1. Navigate to the MCP Server directory
cd /usr/local/google/home/pradeesi/Documents/jetski/BankCRM/mcp_server

# 2. Create and activate an isolated virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install pinned dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Copy the example environment configuration
cp .env.example .env

# 5A. Start the MCP Server over HTTP/SSE on port 8081 (for remote agents / ADK / Apigee)
python3 server.py --transport sse --host 0.0.0.0 --port 8081

# 5B. OR run in stdio mode (for local CLI / IDE MCP clients)
python3 server.py --transport stdio

# 6. Run the automated end-to-end verification client (tests both stdio and SSE)
python3 verify_mcp_client.py --mode both
```

---

## Cloud Deployment Playbooks

### 1. Google Cloud Run Deployment

```bash
cd /usr/local/google/home/pradeesi/Documents/jetski/BankCRM/mcp_server

export PROJECT_ID="your-gcp-project-id"
export REGION="us-central1"
export MCP_SERVICE_NAME="aurelia-bank-crm-mcp"

gcloud config set project "${PROJECT_ID}"

# Optional: Create a Secret in Secret Manager for MCP API Key protection
openssl rand -hex 24 | gcloud secrets create bank-crm-mcp-api-key --data-file=-

# Deploy standalone MCP Server container to Cloud Run
gcloud run deploy "${MCP_SERVICE_NAME}" \
  --source . \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --port 8081 \
  --memory 512Mi \
  --cpu 1 \
  --max-instances 1 \
  --set-env-vars "MCP_TRANSPORT=sse,DATABASE_PATH=/tmp/bank_crm.db,AUTO_SEED_DATABASE=true" \
  --set-secrets "MCP_API_KEY=bank-crm-mcp-api-key:latest"
```

### 2. Google Kubernetes Engine (GKE) Deployment

```bash
cd /usr/local/google/home/pradeesi/Documents/jetski/BankCRM/mcp_server

export PROJECT_ID="your-gcp-project-id"
export REGION="us-central1"
export REPO_NAME="bank-crm-repo"
export IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/aurelia-bank-crm-mcp:v1.0.0"

# 1. Build and push the MCP Server image to Artifact Registry
gcloud builds submit --tag "${IMAGE_URI}" .

# 2. Create the optional Kubernetes Secret for MCP_API_KEY
kubectl create secret generic bank-crm-mcp-secrets \
  --from-literal=MCP_API_KEY="$(openssl rand -hex 24)"

# 3. Deploy to GKE (shares bank-crm-sqlite-pvc with the web portal when deployed in the same namespace)
sed "s/YOUR_GCP_PROJECT_ID/${PROJECT_ID}/g" k8s/mcp-deployment.yaml | kubectl apply -f -

# 4. Verify rollout and inspect external endpoint
kubectl rollout status deployment/aurelia-bank-crm-mcp
kubectl get service aurelia-bank-crm-mcp-svc
```
