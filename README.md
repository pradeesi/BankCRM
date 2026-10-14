# Aurelia Private Bank & Trust — Institutional Bank CRM

An institutional-grade **Private Wealth & Commercial Banking CRM** web application built with **Python Flask**, **SQLite (WAL Mode)**, **HTML5**, **Bootstrap 5.3**, and **Chart.js**. Designed for Relationship Managers, Commercial Credit Underwriters, and Chief Compliance / AML Risk Officers.

---

## Application Architecture

Aurelia Bank CRM follows a clean, layered MVC + Repository architecture:

```
+-------------------------------------------------------------------------+
|                   Presentation Layer (Bootstrap 5.3 + JS)               |
|  - Dark-Navy & Slate Hybrid Shell (`templates/base.html`)               |
|  - Chart.js 4 Analytics & Live LTV/DTI Calculator (`static/js/app.js`)  |
+------------------------------------+------------------------------------+
                                     | HTTP / REST / Form POST
                                     v
+-------------------------------------------------------------------------+
|                     Application Controller (`app.py`)                   |
|  - Flask Application Factory (`create_app`)                             |
|  - Context Processors (Active Persona, Real-time Nav Telemetry Badges)  |
|  - Defensive Input Validation & Flash Notification Routing              |
|  - Cloud Run / GKE Readiness & Liveness Probe (`GET /api/health`)       |
+------------------------------------+------------------------------------+
                                     | Typed Domain Calls
                                     v
+-------------------------------------------------------------------------+
|               Data Access & Domain Engine (`database.py`)               |
|  - Transactional Context Manager (`get_db_connection`, WAL + FK ON)     |
|  - Automated AUM Aggregation (`recalculate_customer_aum`)               |
|  - FinCEN / OFAC Wire Risk Engine (`execute_ledger_transaction`)        |
|  - Basel III LTV / DTI Credit Underwriting (`create_loan_application`)  |
|  - Canonical Institutional Seeder (`seed_database` / `reset_and_seed`)  |
+------------------------------------+------------------------------------+
                                     | Parameterized SQL
                                     v
+-------------------------------------------------------------------------+
|                     SQLite 3 Relational Database                        |
|  Tables: `advisors`, `customers`, `accounts`, `transactions`,           |
|          `loans`, `interactions`, `tasks`, `nba_offers`                 |
+-------------------------------------------------------------------------+
```

### Core Banking CRM Modules
1. **Executive & Relationship Manager Dashboard (`/`)**: Consolidated bank AUM, active credit pipeline volume, net monthly liquidity flows, AML compliance hold counters, 6-month AUM trajectory line chart, depository mix doughnut chart, top UHNW client rankings, and priority action queue.
2. **Customer 360 Directory & Dossier (`/customers`, `/customers/<id>`)**: Multi-facet filtering by service tier (`Obsidian Private`, `Platinum Sovereign`, `Sapphire Premier`), KYC verification state, and Relationship Manager. Individual 360 dossiers feature multi-account holdings, strategic asset allocation charts, KYC/AML compliance review controls, credit facilities, and advisory timelines.
3. **Commercial & Private Credit Origination Pipeline (`/loans`)**: Interactive 5-stage Kanban workflow (`Application & KYC Intake` &rarr; `Underwriting & Financial Analysis` &rarr; `Credit Committee Review` &rarr; `Approved & Ready to Fund` &rarr; `Disbursed & Active`), credit committee register, and a real-time **LTV & Monthly Debt Service Underwriting Calculator**.
4. **Institutional Wire & Account Settlement Ledger (`/ledger`)**: Atomic execution of `FEDWIRE`, `SWIFT International Wire`, `ACH Institutional`, and `Internal Book Transfers` with automatic AUM synchronization, FinCEN/OFAC compliance holds on high-value or cross-border wires, and Dual-Control Officer release sign-off.
5. **Client Tasks, Touchpoints & AI Next-Best-Action Engine (`/tasks`)**: Deliverable tracking, executive briefing logs, and AI propensity-scored cross-sell mandates with estimated annual fee revenue.
6. **External Agent MCP Server (`mcp_server/`)**: Standalone Model Context Protocol (MCP) application exposing 10 read/write banking CRM tools, 2 resources, and 2 prompts over `SSE`, `Streamable HTTP`, and `stdio` transports against the shared SQLite WAL database.

---

## Project Directory Structure

```text
BankCRM/
├── .env.example                 # Template environment variables (safe defaults, no secrets)
├── .gitignore                   # Excludes .env, credentials, venv, and runtime SQLite files
├── Dockerfile                   # Production Python 3.12 + Gunicorn container image definition
├── README.md                    # Architecture, configuration, local & cloud deployment guide
├── requirements.txt             # Pinned Python dependencies (Flask, python-dotenv, gunicorn)
├── config.py                    # Environment variable loader, validator, and semantic logger
├── database.py                  # SQLite schema DDL, institutional seeder, and repository layer
├── app.py                       # Flask application factory, Jinja2 filters, and HTTP routes
├── k8s/
│   └── deployment.yaml          # GKE PersistentVolumeClaim, Deployment, and LoadBalancer Service
├── mcp_server/                  # Separate Model Context Protocol (MCP) Server Application
│   ├── .env.example             # MCP server environment configuration template
│   ├── .gitignore               # MCP server Git exclusions
│   ├── Dockerfile               # Standalone container image for the MCP server (port 8081)
│   ├── README.md                # Dedicated MCP Server architecture, tool catalog & playbooks
│   ├── requirements.txt         # FastMCP (`mcp[cli]`), Uvicorn, Starlette, httpx, dotenv
│   ├── config.py                # MCP server config & stderr-routed structured logger
│   ├── db_client.py             # Self-contained SQLite WAL repository & standalone seeder
│   ├── server.py                # 10 MCP Tools, 2 Resources, 2 Prompts (SSE / HTTP / stdio)
│   ├── verify_mcp_client.py     # Automated end-to-end MCP client verification suite
│   └── k8s/
│       └── mcp-deployment.yaml  # GKE Deployment & LoadBalancer Service for the MCP server
├── static/
│   ├── css/
│   │   └── styles.css           # Institutional Dark-Navy & Slate Hybrid design system
│   └── js/
│       └── app.js               # Chart.js visualizations & live LTV/DTI underwriting calculator
└── templates/
    ├── base.html                # Master layout with dark sidebar, topbar persona switcher & modals
    ├── login.html               # Frictionless 1-click institutional persona sign-in portal
    ├── dashboard.html           # Executive & RM Command Center view
    ├── customers.html           # Customer 360 Directory with instant search & facet filters
    ├── customer_detail.html    # Individual Client 360 Dossier view
    ├── loans.html               # 5-Stage Kanban & Credit Underwriting Pipeline view
    ├── ledger.html              # SWIFT/FEDWIRE Settlement Ledger & AML Compliance Queue view
    └── tasks.html               # Advisor Tasks, Touchpoints & AI Next-Best-Action workspace
```

---

## Frameworks & Libraries

| Component | Technology / Version | Purpose |
| :--- | :--- | :--- |
| **Web Framework** | `Flask==3.0.3` / `Werkzeug==3.0.4` | Routing, Jinja2 server-side templating, signed sessions |
| **MCP Server SDK** | `mcp[cli]>=1.2.0` + `uvicorn` / `starlette` | External Agent Model Context Protocol server (`mcp_server/`) |
| **Database Engine** | `sqlite3` (Python Standard Library) | Zero-config ACID relational storage in Write-Ahead Logging (WAL) mode |
| **Configuration** | `python-dotenv==1.0.1` | Loads environment variables from `.env` without hardcoding |
| **WSGI Server** | `gunicorn==22.0.0` | Production multi-threaded HTTP server for Docker / Cloud Run / GKE |
| **UI Framework** | `Bootstrap 5.3.3` + `Bootstrap Icons 1.11.3` | Responsive grid, modal dialogs, tables, and institutional iconography |
| **Data Visualization** | `Chart.js 4.4.4` | Interactive AUM growth line chart and asset allocation doughnut charts |
| **Typography** | `Plus Jakarta Sans` & `JetBrains Mono` | Executive UI legibility and tabular-numeral financial alignment |

---

## Environment Variables

All runtime settings are loaded externally via `config.py`. Copy `.env.example` to `.env` for local customization:

| Variable Name | Type | Default / Example | Purpose |
| :--- | :--- | :--- | :--- |
| `FLASK_ENV` | `string` | `development` | Execution mode (`development` or `production`) |
| `FLASK_DEBUG` | `boolean` | `false` | Enables/disables Flask interactive debug reloader |
| `SECRET_KEY` | `string` | *(auto-generated hex)* | Cryptographic secret signing Flask session cookies |
| `HOST` | `string` | `0.0.0.0` | Network interface bind address |
| `PORT` | `integer` | `8080` | HTTP listener TCP port (`1`–`65535`) |
| `DATABASE_PATH` | `string` | `bank_crm.db` | Path to the SQLite database file |
| `AUTO_SEED_DATABASE` | `boolean` | `true` | Automatically seeds canonical banking records if DB is empty |
| `BANK_NAME` | `string` | `Aurelia Private Bank & Trust` | Institution title displayed across UI and API health probes |
| `HIGH_VALUE_WIRE_THRESHOLD_USD` | `float` | `25000.00` | USD wire threshold triggering automated AML/OFAC review holds |
| `DEFAULT_RM_EMAIL` | `string` | `elena.rostova@aureliabank.com` | Default Relationship Manager persona when session starts |
| `LOG_LEVEL` | `string` | `INFO` | Structured logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## No Hardcoding Rule

This repository enforces a strict **Zero Hardcoding Policy**:
- No API keys, session secrets, database paths, or environment-specific URLs are hardcoded in source files.
- All configuration parameters are injected via OS environment variables or `.env` (excluded from Git via `.gitignore`).
- A clean `.env.example` template is provided in the repository root with safe placeholder values.

---

## Local Execution Instructions

```bash
# 1. Navigate to the project directory
cd /usr/local/google/home/pradeesi/Documents/jetski/BankCRM

# 2. Create and activate an isolated Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install pinned dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Initialize your local environment configuration
cp .env.example .env

# 5. Start the application (automatically creates and seeds bank_crm.db on first boot)
python3 app.py
```

Open **`http://localhost:8080`** in your browser.
- Use `/login` or the **top-right Role Switcher** to switch between **Elena Rostova** (Managing Director — Private Wealth), **Marcus Vance** (SVP — Commercial Credit), and **Sophia Chen** (Chief Compliance & AML Risk Officer).
- Click **"Reset & Re-seed Demo Data"** at the bottom of the left sidebar at any time to restore the canonical dataset.

---

## Cloud Deployment Playbooks

### 1. Google Cloud Run Deployment

```bash
# Set your Google Cloud Project and Region
export PROJECT_ID="your-gcp-project-id"
export REGION="us-central1"
export SERVICE_NAME="aurelia-bank-crm"

gcloud config set project "${PROJECT_ID}"

# Enable required Google Cloud APIs
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com

# Create a Secret in Secret Manager for Flask session signing
openssl rand -hex 32 | gcloud secrets create bank-crm-secret-key --data-file=-

# Build and deploy directly from source to Cloud Run
gcloud run deploy "${SERVICE_NAME}" \
  --source . \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --max-instances 1 \
  --set-env-vars "FLASK_ENV=production,DATABASE_PATH=/tmp/bank_crm.db,AUTO_SEED_DATABASE=true,HIGH_VALUE_WIRE_THRESHOLD_USD=25000.00" \
  --set-secrets "SECRET_KEY=bank-crm-secret-key:latest"
```

### 2. Google Kubernetes Engine (GKE) Deployment

```bash
# 1. Set environment variables
export PROJECT_ID="your-gcp-project-id"
export REGION="us-central1"
export CLUSTER_NAME="aurelia-crm-cluster"
export REPO_NAME="bank-crm-repo"
export IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/aurelia-bank-crm:v1.0.0"

# 2. Create Artifact Registry repository and build/push container image
gcloud artifacts repositories create "${REPO_NAME}" \
  --repository-format=docker \
  --location="${REGION}"

gcloud builds submit --tag "${IMAGE_URI}" .

# 3. Create or connect to your GKE Autopilot cluster
gcloud container clusters create-auto "${CLUSTER_NAME}" --region "${REGION}"
gcloud container clusters get-credentials "${CLUSTER_NAME}" --region "${REGION}"

# 4. Create the Kubernetes Secret for SECRET_KEY
kubectl create secret generic bank-crm-secrets \
  --from-literal=SECRET_KEY="$(openssl rand -hex 32)"

# 5. Update the image project ID in k8s/deployment.yaml and apply manifests
sed "s/YOUR_GCP_PROJECT_ID/${PROJECT_ID}/g" k8s/deployment.yaml | kubectl apply -f -

# 6. Verify pod readiness and retrieve external LoadBalancer IP
kubectl rollout status deployment/aurelia-bank-crm
kubectl get service aurelia-bank-crm-svc
```
