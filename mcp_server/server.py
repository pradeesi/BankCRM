# ==============================================================================
# Purpose: FastMCP Server exposing Aurelia Private Bank & Trust CRM tools,
#   resources, and prompts to external AI agents over SSE, Streamable HTTP,
#   and stdio transports.
# Architecture/Context: Standalone microservice inside `BankCRM/mcp_server/`
#   that connects to the shared `../bank_crm.db` SQLite database via
#   `db_client.py`. Supports optional `MCP_API_KEY` header enforcement and
#   structured tool invocation audit logging.
# Dependencies/Side Effects: Requires `mcp` SDK (`FastMCP`), `starlette`, and
#   `uvicorn`. Mutates SQLite records when governed write tools are invoked.
# Author: Pradeep Singh
# License: Apache-2.0
# ==============================================================================

import argparse
import json
import logging
import time
from typing import Any, Callable, Dict, List

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from config import MCPServerConfig, load_mcp_config
from db_client import (
    ensure_database_ready,
    execute_agent_wire_or_transfer,
    fetch_bank_executive_summary,
    fetch_customer_360_dossier,
    log_agent_interaction_and_optional_task,
    originate_agent_credit_facility,
    query_customers,
    query_flagged_or_recent_transactions,
    query_loan_pipeline,
    query_nba_recommendations,
    resolve_aml_hold,
)

cfg: MCPServerConfig = load_mcp_config()
logger = logging.getLogger("aurelia_mcp.server")

# Ensure SQLite schema and data are initialized prior to serving tool calls
ensure_database_ready(cfg.database_path, auto_seed=cfg.auto_seed_database)

mcp = FastMCP(
    cfg.server_name,
    instructions=(
        "Aurelia Private Bank & Trust Institutional CRM MCP Server. "
        "Provides 10 banking tools (6 read/analytics tools and 4 governed action tools) "
        "for Relationship Management, Customer 360 intelligence, Basel III LTV/DTI credit "
        "underwriting, SWIFT/FEDWIRE settlement execution, and AML/OFAC compliance review."
    ),
)


def _audit_log_tool_call(tool_name: str, args_summary: Dict[str, Any], status: str, elapsed_ms: float) -> None:
    """Emit a structured semantic audit log line for every MCP tool execution.

    Summary:
        Logs tool name, input arguments, execution status, and latency in milliseconds
        to stderr for observability and compliance auditing.

    Parameters:
        tool_name (str): Name of the invoked MCP tool.
        args_summary (Dict[str, Any]): Sanitized dictionary of tool arguments.
        status (str): Execution outcome ('SUCCESS' or 'ERROR').
        elapsed_ms (float): Execution duration in milliseconds.

    Returns:
        None
    """
    logger.info(
        "MCP_TOOL_AUDIT | tool=%s | status=%s | latency_ms=%.2f | args=%s",
        tool_name,
        status,
        elapsed_ms,
        json.dumps(args_summary, default=str),
    )


# ==============================================================================
# 1. READ & ANALYTICS TOOLS (6 Tools)
# ==============================================================================


@mcp.tool()
def search_customers(
    query: str = "",
    tier: str = "",
    kyc_status: str = "",
) -> Dict[str, Any]:
    """Search and filter Aurelia Private Bank clients across the Customer 360 directory.

    Summary:
        Queries institutional and private wealth clients by name, company, CIF code,
        city, service tier, or KYC verification status, returning consolidated AUM
        and credit scores.

    Parameters:
        query (str): Optional substring matching client name, company, CIF (e.g. 'CIF-904821'), email, or city.
        tier (str): Optional service tier filter ('Obsidian Private', 'Platinum Sovereign', 'Sapphire Premier').
        kyc_status (str): Optional KYC filter ('Verified', 'Enhanced Due Diligence', 'Periodic Review Due').

    Returns:
        Dict[str, Any]: Dictionary containing `count` and `customers` list sorted by AUM descending.
    """
    start = time.perf_counter()
    try:
        results = query_customers(
            cfg.database_path, query=query, tier=tier, kyc_status=kyc_status
        )
        _audit_log_tool_call(
            "search_customers",
            {"query": query, "tier": tier, "kyc_status": kyc_status},
            "SUCCESS",
            (time.perf_counter() - start) * 1000,
        )
        return {"status": "success", "count": len(results), "customers": results}
    except Exception as exc:
        _audit_log_tool_call(
            "search_customers",
            {"query": query, "tier": tier, "kyc_status": kyc_status},
            "ERROR",
            (time.perf_counter() - start) * 1000,
        )
        return {"status": "error", "error": str(exc)}


@mcp.tool()
def get_customer_360_dossier(customer_identifier: str) -> Dict[str, Any]:
    """Retrieve the complete Customer 360 Dossier for a specific banking client.

    Summary:
        Fetches the client's profile, KYC/AML risk rating, FICO credit score,
        asset allocation percentages, all depository/wealth accounts, recent wire
        transactions, loan facilities, advisor interactions, tasks, and AI
        Next-Best-Action offers.

    Parameters:
        customer_identifier (str): Numeric customer ID (e.g. '1'), CIF number
            (e.g. 'CIF-904821'), or client/company name (e.g. 'Alexander Sterling').

    Returns:
        Dict[str, Any]: Full Customer 360 dossier dictionary or error message if not found.
    """
    start = time.perf_counter()
    dossier = fetch_customer_360_dossier(cfg.database_path, customer_identifier)
    if dossier is None:
        _audit_log_tool_call(
            "get_customer_360_dossier",
            {"customer_identifier": customer_identifier},
            "ERROR",
            (time.perf_counter() - start) * 1000,
        )
        return {
            "status": "not_found",
            "error": f"No client found matching identifier '{customer_identifier}'.",
        }

    _audit_log_tool_call(
        "get_customer_360_dossier",
        {"customer_identifier": customer_identifier, "resolved_cif": dossier.get("cif_number")},
        "SUCCESS",
        (time.perf_counter() - start) * 1000,
    )
    return {"status": "success", "dossier": dossier}


@mcp.tool()
def get_bank_executive_summary() -> Dict[str, Any]:
    """Retrieve consolidated bank-wide executive KPIs, AUM, credit pipeline, and risk telemetry.

    Summary:
        Returns total Assets Under Management (AUM), active client count, average
        FICO score, active loan pipeline volume, weighted average LTV and interest
        rates, gross inflows/outflows, AML flagged wire count, and AUM by tier.

    Parameters:
        None

    Returns:
        Dict[str, Any]: Bank-wide executive telemetry dictionary.
    """
    start = time.perf_counter()
    summary = fetch_bank_executive_summary(cfg.database_path)
    _audit_log_tool_call(
        "get_bank_executive_summary",
        {},
        "SUCCESS",
        (time.perf_counter() - start) * 1000,
    )
    return {"status": "success", "executive_summary": summary}


@mcp.tool()
def list_credit_loan_pipeline(stage: str = "") -> Dict[str, Any]:
    """List commercial and private wealth loan facilities in the credit underwriting pipeline.

    Summary:
        Retrieves all loan applications enriched with borrower CIF, name, FICO
        credit score, collateral valuation, LTV/DTI ratios, underwriting grade,
        and current pipeline stage.

    Parameters:
        stage (str): Optional stage filter substring (e.g. 'Underwriting',
            'Credit Committee Review', 'Approved', 'Disbursed').

    Returns:
        Dict[str, Any]: Dictionary containing `count`, `total_principal_usd`, and `loans`.
    """
    start = time.perf_counter()
    loans = query_loan_pipeline(cfg.database_path, stage=stage)
    total_vol = round(sum(float(x["principal_usd"]) for x in loans), 2)
    _audit_log_tool_call(
        "list_credit_loan_pipeline",
        {"stage": stage, "returned_count": len(loans)},
        "SUCCESS",
        (time.perf_counter() - start) * 1000,
    )
    return {
        "status": "success",
        "count": len(loans),
        "total_principal_usd": total_vol,
        "loans": loans,
    }


@mcp.tool()
def list_aml_flagged_transactions(
    flagged_only: bool = True, limit: int = 25
) -> Dict[str, Any]:
    """List AML/OFAC compliance-flagged wire transfers or recent ledger settlements.

    Summary:
        Returns ledger transactions joined with client KYC/AML risk ratings and
        account numbers so compliance agents can inspect or clear wire holds.

    Parameters:
        flagged_only (bool): When True (default), returns only transactions with active AML/risk holds (`risk_flag = 1`).
        limit (int): Maximum number of transactions to return (default 25, max 100).

    Returns:
        Dict[str, Any]: Dictionary containing `count` and `transactions` list.
    """
    start = time.perf_counter()
    txns = query_flagged_or_recent_transactions(
        cfg.database_path, flagged_only=flagged_only, limit=limit
    )
    _audit_log_tool_call(
        "list_aml_flagged_transactions",
        {"flagged_only": flagged_only, "limit": limit},
        "SUCCESS",
        (time.perf_counter() - start) * 1000,
    )
    return {"status": "success", "count": len(txns), "transactions": txns}


@mcp.tool()
def get_nba_product_recommendations(
    customer_identifier: str = "", status_filter: str = ""
) -> Dict[str, Any]:
    """Retrieve AI Next-Best-Action (NBA) wealth and treasury product recommendations.

    Summary:
        Returns propensity-scored cross-sell mandates with rationale and estimated
        annual fee revenue, optionally filtered by client or offer status.

    Parameters:
        customer_identifier (str): Optional client ID, CIF code, or name substring.
        status_filter (str): Optional status filter ('Recommended', 'Pitched', 'Accepted').

    Returns:
        Dict[str, Any]: Dictionary containing `count`, `total_estimated_revenue_usd`, and `offers`.
    """
    start = time.perf_counter()
    offers = query_nba_recommendations(
        cfg.database_path,
        customer_identifier=customer_identifier,
        status_filter=status_filter,
    )
    est_rev = round(sum(float(o["estimated_revenue_usd"]) for o in offers), 2)
    _audit_log_tool_call(
        "get_nba_product_recommendations",
        {"customer_identifier": customer_identifier, "status_filter": status_filter},
        "SUCCESS",
        (time.perf_counter() - start) * 1000,
    )
    return {
        "status": "success",
        "count": len(offers),
        "total_estimated_revenue_usd": est_rev,
        "offers": offers,
    }


# ==============================================================================
# 2. GOVERNED WRITE & ACTION TOOLS (4 Tools)
# ==============================================================================


@mcp.tool()
def execute_wire_or_transfer(
    account_identifier: str,
    txn_type: str,
    amount_usd: float,
    counterparty: str,
    channel: str = "FEDWIRE",
    category: str = "Agent-Initiated Treasury Settlement",
    destination_account_identifier: str = "",
) -> Dict[str, Any]:
    """Execute an atomic deposit credit, outgoing wire debit, or internal book transfer.

    Summary:
        Updates account balances and consolidated client AUM in SQLite, automatically
        evaluating FinCEN/OFAC compliance rules (outgoing SWIFT wires >= $25,000 or
        domestic wires >= $500,000 are placed on 'Pending Compliance' hold).

    Parameters:
        account_identifier (str): Source/target account ID (e.g. '1') or number (e.g. 'AUR-8801-0019').
        txn_type (str): Must be 'Debit', 'Credit', or 'Internal Transfer'.
        amount_usd (float): Positive USD amount to transfer.
        counterparty (str): Beneficiary or counterparty institution name.
        channel (str): Settlement rail ('FEDWIRE', 'SWIFT International Wire', 'ACH Institutional', 'Internal Book Transfer').
        category (str): Business purpose category of the settlement.
        destination_account_identifier (str): Required only when `txn_type` is 'Internal Transfer' (e.g. 'AUR-8801-0042').

    Returns:
        Dict[str, Any]: Created ledger transaction record with `reference_id`, `balance_after_usd`, and `risk_flag`.
    """
    start = time.perf_counter()
    try:
        txn = execute_agent_wire_or_transfer(
            cfg.database_path,
            account_identifier=account_identifier,
            txn_type=txn_type,
            amount_usd=amount_usd,
            counterparty=counterparty,
            channel=channel,
            category=category,
            destination_account_identifier=destination_account_identifier,
            wire_threshold_usd=cfg.high_value_wire_threshold_usd,
        )
        _audit_log_tool_call(
            "execute_wire_or_transfer",
            {
                "account": account_identifier,
                "txn_type": txn_type,
                "amount_usd": amount_usd,
                "ref": txn.get("reference_id"),
                "risk_flag": txn.get("risk_flag"),
            },
            "SUCCESS",
            (time.perf_counter() - start) * 1000,
        )
        return {"status": "success", "transaction": txn}
    except Exception as exc:
        _audit_log_tool_call(
            "execute_wire_or_transfer",
            {"account": account_identifier, "txn_type": txn_type, "amount_usd": amount_usd},
            "ERROR",
            (time.perf_counter() - start) * 1000,
        )
        return {"status": "error", "error": str(exc)}


@mcp.tool()
def resolve_aml_compliance_hold(
    transaction_identifier: str,
    officer_name: str = "Sophia Chen (Chief Risk Officer via MCP)",
) -> Dict[str, Any]:
    """Approve and release an AML/OFAC compliance hold on a flagged wire transaction.

    Summary:
        Clears `risk_flag = 0`, transitions transaction status to `Settled`, and
        appends a Dual-Control Compliance sign-off entry to the client's 360 timeline.

    Parameters:
        transaction_identifier (str): Numeric transaction ID (e.g. '2') or reference code (e.g. 'TXN-20261014-902').
        officer_name (str): Name of the compliance officer or governed agent approving release.

    Returns:
        Dict[str, Any]: Updated transaction dictionary with `status = 'Settled'`.
    """
    start = time.perf_counter()
    try:
        updated = resolve_aml_hold(
            cfg.database_path,
            transaction_identifier=transaction_identifier,
            officer_name=officer_name,
        )
        _audit_log_tool_call(
            "resolve_aml_compliance_hold",
            {"transaction_identifier": transaction_identifier, "officer_name": officer_name},
            "SUCCESS",
            (time.perf_counter() - start) * 1000,
        )
        return {"status": "success", "transaction": updated}
    except Exception as exc:
        _audit_log_tool_call(
            "resolve_aml_compliance_hold",
            {"transaction_identifier": transaction_identifier},
            "ERROR",
            (time.perf_counter() - start) * 1000,
        )
        return {"status": "error", "error": str(exc)}


@mcp.tool()
def originate_credit_facility(
    customer_identifier: str,
    loan_type: str,
    purpose: str,
    principal_usd: float,
    collateral_value_usd: float,
    interest_rate_pct: float = 5.85,
    term_months: int = 60,
    underwriter_name: str = "Marcus Vance (via MCP Credit Agent)",
) -> Dict[str, Any]:
    """Originate a new commercial or private wealth credit facility with automated LTV/DTI underwriting.

    Summary:
        Computes Loan-to-Value (LTV) ratio, monthly debt service, Debt-to-Income
        (DTI) ratio, and Basel III credit underwriting grade ('AAA', 'AA', 'A+',
        'BBB+'), and inserts the new loan into the underwriting pipeline.

    Parameters:
        customer_identifier (str): Borrower ID, CIF number (e.g. 'CIF-904821'), or client name.
        loan_type (str): Facility structure (e.g. 'Securities-Backed Line of Credit (SBLOC)', 'Commercial Real Estate Senior Mortgage').
        purpose (str): Business purpose and covenant summary.
        principal_usd (float): Requested principal amount in USD.
        collateral_value_usd (float): Pledged collateral valuation in USD.
        interest_rate_pct (float): Contracted annual interest rate percentage (default 5.85).
        term_months (int): Amortization term in months (default 60).
        underwriter_name (str): Assigned credit officer or agent name.

    Returns:
        Dict[str, Any]: Newly originated loan facility dictionary including `loan_number`, `ltv_ratio_pct`, `dti_ratio_pct`, and `underwriting_grade`.
    """
    start = time.perf_counter()
    try:
        loan = originate_agent_credit_facility(
            cfg.database_path,
            customer_identifier=customer_identifier,
            loan_type=loan_type,
            purpose=purpose,
            principal_usd=principal_usd,
            collateral_value_usd=collateral_value_usd,
            interest_rate_pct=interest_rate_pct,
            term_months=term_months,
            underwriter_name=underwriter_name,
        )
        _audit_log_tool_call(
            "originate_credit_facility",
            {
                "customer_identifier": customer_identifier,
                "loan_number": loan.get("loan_number"),
                "principal_usd": principal_usd,
                "ltv_ratio_pct": loan.get("ltv_ratio_pct"),
            },
            "SUCCESS",
            (time.perf_counter() - start) * 1000,
        )
        return {"status": "success", "loan_facility": loan}
    except Exception as exc:
        _audit_log_tool_call(
            "originate_credit_facility",
            {"customer_identifier": customer_identifier, "principal_usd": principal_usd},
            "ERROR",
            (time.perf_counter() - start) * 1000,
        )
        return {"status": "error", "error": str(exc)}


@mcp.tool()
def log_client_interaction_and_task(
    customer_identifier: str,
    subject: str,
    summary: str,
    channel: str = "AI Agent Advisory Briefing",
    sentiment: str = "Positive",
    advisor_name: str = "",
    follow_up_task_title: str = "",
    follow_up_priority: str = "High",
    follow_up_due_date: str = "",
) -> Dict[str, Any]:
    """Log an advisory interaction note on a client's 360 timeline and optionally schedule a follow-up task.

    Summary:
        Appends a timestamped touchpoint to `interactions`, updates the client's
        `last_contact_date`, and creates an actionable deliverable in `tasks` when
        `follow_up_task_title` is supplied.

    Parameters:
        customer_identifier (str): Client ID, CIF code (e.g. 'CIF-904821'), or full name.
        subject (str): Headline of the briefing or compliance review.
        summary (str): Detailed executive notes from the interaction.
        channel (str): Engagement channel (e.g. 'AI Agent Advisory Briefing', 'Phone Call').
        sentiment (str): Client sentiment ('Positive', 'Neutral', 'Attention Required').
        advisor_name (str): Advisor or agent name (defaults to `DEFAULT_AGENT_ACTOR`).
        follow_up_task_title (str): Optional title to schedule a linked follow-up task.
        follow_up_priority (str): Task priority ('Critical', 'High', 'Medium').
        follow_up_due_date (str): Optional YYYY-MM-DD due date for the follow-up task.

    Returns:
        Dict[str, Any]: Dictionary containing the recorded `interaction` and optional `created_task`.
    """
    start = time.perf_counter()
    resolved_advisor = advisor_name.strip() or cfg.default_agent_actor
    try:
        result = log_agent_interaction_and_optional_task(
            cfg.database_path,
            customer_identifier=customer_identifier,
            subject=subject,
            summary=summary,
            channel=channel,
            sentiment=sentiment,
            advisor_name=resolved_advisor,
            follow_up_task_title=follow_up_task_title,
            follow_up_priority=follow_up_priority,
            follow_up_due_date=follow_up_due_date,
        )
        _audit_log_tool_call(
            "log_client_interaction_and_task",
            {
                "customer_identifier": customer_identifier,
                "subject": subject,
                "task_created": bool(result.get("created_task")),
            },
            "SUCCESS",
            (time.perf_counter() - start) * 1000,
        )
        return {"status": "success", **result}
    except Exception as exc:
        _audit_log_tool_call(
            "log_client_interaction_and_task",
            {"customer_identifier": customer_identifier, "subject": subject},
            "ERROR",
            (time.perf_counter() - start) * 1000,
        )
        return {"status": "error", "error": str(exc)}


# ==============================================================================
# 3. MCP RESOURCES & PROMPTS
# ==============================================================================


@mcp.resource("bank://summary")
def resource_bank_summary() -> str:
    """Expose bank-wide executive KPIs and AUM telemetry as a JSON MCP resource.

    Summary:
        Allows MCP clients to subscribe to or read `bank://summary` directly.

    Parameters:
        None

    Returns:
        str: Formatted JSON string of executive summary metrics.
    """
    return json.dumps(fetch_bank_executive_summary(cfg.database_path), indent=2)


@mcp.resource("bank://customers")
def resource_customers_catalog() -> str:
    """Expose the full Customer 360 directory catalog as a JSON MCP resource.

    Summary:
        Allows MCP clients to read `bank://customers` for context grounding.

    Parameters:
        None

    Returns:
        str: Formatted JSON string of all customer records.
    """
    return json.dumps(query_customers(cfg.database_path), indent=2)


@mcp.prompt()
def wealth_review_prompt(customer_identifier: str) -> str:
    """Generate a structured Private Wealth & Portfolio Review prompt for an AI agent.

    Summary:
        Instructs an external LLM agent on how to synthesize a client's 360 dossier,
        asset allocation, credit capacity, and Next-Best-Action mandates.

    Parameters:
        customer_identifier (str): Client CIF number, ID, or full name.

    Returns:
        str: Structured prompt template string.
    """
    return (
        f"You are a Managing Director at Aurelia Private Bank & Trust preparing an "
        f"executive briefing for client '{customer_identifier}'.\n"
        f"1. Call `get_customer_360_dossier(customer_identifier='{customer_identifier}')`.\n"
        f"2. Call `get_nba_product_recommendations(customer_identifier='{customer_identifier}')`.\n"
        f"3. Summarize their consolidated AUM, depository yield optimization, active loan LTV/DTI posture, "
        f"and top recommended Next-Best-Action mandate with estimated fee impact."
    )


@mcp.prompt()
def aml_investigation_prompt() -> str:
    """Generate a structured FinCEN/OFAC Wire Compliance Investigation prompt.

    Summary:
        Guides a compliance agent through inspecting flagged SWIFT/FEDWIRE holds
        and verifying client KYC status before releasing holds.

    Parameters:
        None

    Returns:
        str: Structured compliance investigation prompt.
    """
    return (
        "You are the Chief Compliance & AML Risk Officer at Aurelia Private Bank & Trust.\n"
        "1. Call `list_aml_flagged_transactions(flagged_only=True)` to inspect all pending wire holds.\n"
        "2. For each flagged transaction, call `get_customer_360_dossier` on the client to verify their "
        "KYC status, AML risk rating, and historical interaction notes.\n"
        "3. Provide a risk disposition recommendation (Release Hold vs. Request EDD Documentation)."
    )


# ==============================================================================
# 4. ASGI HTTP / SSE WRAPPER WITH HEALTH PROBE & OPTIONAL API KEY MIDDLEWARE
# ==============================================================================


class OptionalAPIKeyMiddleware(BaseHTTPMiddleware):
    """Enforce `X-API-Key` or `Authorization: Bearer` when `MCP_API_KEY` is configured.

    Summary:
        Allows unauthenticated requests to `/health` for Cloud Run / GKE probes
        and validates the configured API key on MCP endpoints when `MCP_API_KEY`
        is non-empty.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Validate incoming HTTP request headers against `cfg.mcp_api_key`.

        Summary:
            Checks `X-API-Key` and `Authorization` headers when `cfg.mcp_api_key`
            is enabled; passes through immediately when unconfigured.

        Parameters:
            request (Request): Incoming Starlette HTTP request.
            call_next (Callable): Next ASGI middleware/handler in chain.

        Returns:
            Response: Downstream response or HTTP 401 JSON error.
        """
        if request.url.path == "/health" or not cfg.mcp_api_key:
            return await call_next(request)

        header_key = request.headers.get("x-api-key", "").strip()
        auth_header = request.headers.get("authorization", "").strip()
        bearer_key = (
            auth_header[7:].strip()
            if auth_header.lower().startswith("bearer ")
            else ""
        )

        if header_key != cfg.mcp_api_key and bearer_key != cfg.mcp_api_key:
            logger.warning("Unauthorized MCP request rejected from %s", request.client)
            return JSONResponse(
                {"error": "Unauthorized: valid X-API-Key or Bearer token required."},
                status_code=401,
            )

        return await call_next(request)


async def health_endpoint(request: Request) -> JSONResponse:
    """Return JSON health and database readiness telemetry for Cloud Run and GKE.

    Summary:
        Executes a live SQLite summary query and reports available MCP tools and
        transport configuration.

    Parameters:
        request (Request): Incoming HTTP GET request.

    Returns:
        JSONResponse: HTTP 200 readiness payload.
    """
    summary = fetch_bank_executive_summary(cfg.database_path)
    return JSONResponse(
        {
            "status": "healthy",
            "service": cfg.server_name,
            "database_path": cfg.database_path,
            "active_clients_count": summary["active_clients_count"],
            "total_aum_usd": summary["total_aum_usd"],
            "aml_flagged_wires_count": summary["aml_flagged_wires_count"],
            "mcp_endpoints": {
                "sse": "/sse",
                "messages": "/messages/",
                "health": "/health",
            },
            "tools_count": 10,
        }
    )


def create_asgi_app(transport_mode: str = "sse") -> Starlette:
    """Build the composite Starlette ASGI application hosting FastMCP and `/health`.

    Summary:
        Mounts FastMCP's SSE or Streamable HTTP ASGI app alongside `/health` and
        attaches `OptionalAPIKeyMiddleware`.

    Parameters:
        transport_mode (str): Either 'sse' or 'streamable-http'.

    Returns:
        Starlette: Configured ASGI application for Uvicorn.
    """
    mcp_asgi = (
        mcp.streamable_http_app()
        if transport_mode == "streamable-http"
        else mcp.sse_app()
    )

    app = Starlette(
        routes=[
            Route("/health", endpoint=health_endpoint, methods=["GET"]),
            Mount("/", app=mcp_asgi),
        ]
    )
    app.add_middleware(OptionalAPIKeyMiddleware)
    return app


asgi_app = create_asgi_app(cfg.transport if cfg.transport != "stdio" else "sse")


def main() -> None:
    """CLI entrypoint supporting `--transport sse`, `--transport streamable-http`, and `--transport stdio`.

    Summary:
        Parses command-line flags and starts either the `stdio` JSON-RPC loop or
        the Uvicorn ASGI server on the configured host and port.

    Parameters:
        None

    Returns:
        None
    """
    parser = argparse.ArgumentParser(
        description="Aurelia Private Bank & Trust CRM — Model Context Protocol (MCP) Server"
    )
    parser.add_argument(
        "--transport",
        choices=["sse", "streamable-http", "stdio"],
        default=cfg.transport,
        help="MCP transport protocol (default: from MCP_TRANSPORT or 'sse')",
    )
    parser.add_argument(
        "--host",
        default=cfg.host,
        help="Bind host for HTTP/SSE transport (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=cfg.port,
        help="Bind port for HTTP/SSE transport (default: 8081)",
    )
    args = parser.parse_args()

    if args.transport == "stdio":
        logger.info(
            "Starting %s in stdio transport mode (DB=%s)",
            cfg.server_name,
            cfg.database_path,
        )
        mcp.run(transport="stdio")
    else:
        logger.info(
            "Starting %s (%s) on http://%s:%d (DB=%s)",
            cfg.server_name,
            args.transport,
            args.host,
            args.port,
            cfg.database_path,
        )
        app_instance = create_asgi_app(args.transport)
        uvicorn.run(app_instance, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
