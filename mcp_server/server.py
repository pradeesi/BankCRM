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
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
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
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
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


TOOL_DISPATCH_REGISTRY: Dict[str, Callable[..., Dict[str, Any]]] = {
    "search_customers": search_customers,
    "get_customer_360_dossier": get_customer_360_dossier,
    "get_bank_executive_summary": get_bank_executive_summary,
    "list_credit_loan_pipeline": list_credit_loan_pipeline,
    "list_aml_flagged_transactions": list_aml_flagged_transactions,
    "get_nba_product_recommendations": get_nba_product_recommendations,
    "execute_wire_or_transfer": execute_wire_or_transfer,
    "resolve_aml_compliance_hold": resolve_aml_compliance_hold,
    "originate_credit_facility": originate_credit_facility,
    "log_client_interaction_and_task": log_client_interaction_and_task,
}

READ_TOOL_NAMES = {
    "search_customers",
    "get_customer_360_dossier",
    "get_bank_executive_summary",
    "list_credit_loan_pipeline",
    "list_aml_flagged_transactions",
    "get_nba_product_recommendations",
}

SAMPLE_TOOL_PAYLOADS: Dict[str, Dict[str, Any]] = {
    "search_customers": {"query": "", "tier": "Obsidian Private", "kyc_status": ""},
    "get_customer_360_dossier": {"customer_identifier": "CIF-904821"},
    "get_bank_executive_summary": {},
    "list_credit_loan_pipeline": {"stage": ""},
    "list_aml_flagged_transactions": {"flagged_only": True, "limit": 10},
    "get_nba_product_recommendations": {"customer_identifier": "", "status_filter": ""},
    "execute_wire_or_transfer": {
        "account_identifier": "AUR-8801-0019",
        "txn_type": "Credit",
        "amount_usd": 25000.0,
        "counterparty": "Zurich Prime Custody AG",
        "channel": "FEDWIRE",
        "category": "Agent-Initiated Treasury Settlement",
        "destination_account_identifier": "",
    },
    "resolve_aml_compliance_hold": {
        "transaction_identifier": "TXN-20261011-889",
        "officer_name": "Sophia Chen (Chief Risk Officer via MCP)",
    },
    "originate_credit_facility": {
        "customer_identifier": "CIF-904821",
        "loan_type": "Securities-Backed Line of Credit (SBLOC)",
        "purpose": "Short-term Private Equity Capital Call Bridge",
        "principal_usd": 1200000.0,
        "collateral_value_usd": 2500000.0,
        "interest_rate_pct": 5.45,
        "term_months": 36,
        "underwriter_name": "Marcus Vance (via MCP Credit Agent)",
    },
    "log_client_interaction_and_task": {
        "customer_identifier": "CIF-904821",
        "subject": "Q4 Tax-Loss Harvesting & SBLOC Liquidity Briefing",
        "summary": "Reviewed municipal tax-exempt bond sleeve and SBLOC liquidity line via AI Co-Pilot.",
        "channel": "AI Agent Advisory Briefing",
        "sentiment": "Positive",
        "advisor_name": "Elena Rostova (via AI Co-Pilot)",
        "follow_up_task_title": "Prepare SBLOC Term Sheet for Alexander Sterling",
        "follow_up_priority": "High",
        "follow_up_due_date": "2026-10-25",
    },
}


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
                "explorer_ui": "/",
                "tools_catalog_json": "/api/tools",
                "tool_call_api": "/api/tools/call",
                "sse": "/sse",
                "messages": "/messages/",
                "health": "/health",
            },
            "tools_count": 10,
        }
    )


async def api_tools_catalog_endpoint(request: Request) -> JSONResponse:
    """Return the complete catalog of registered MCP tools, resources, and prompts as JSON.

    Summary:
        Introspects the active `FastMCP` instance to return all 10 tools (with JSON
        Schema parameters, descriptions, categories, and sample payloads), 2 resources,
        and 2 prompts.

    Parameters:
        request (Request): Incoming HTTP GET request.

    Returns:
        JSONResponse: Structured catalog of MCP primitives.
    """
    tools_list = await mcp.list_tools()
    resources_list = await mcp.list_resources()
    prompts_list = await mcp.list_prompts()

    serialized_tools: List[Dict[str, Any]] = []
    for t in tools_list:
        category = "Read & Analytics" if t.name in READ_TOOL_NAMES else "Governed Action (Write)"
        serialized_tools.append(
            {
                "name": t.name,
                "category": category,
                "description": (t.description or "").strip(),
                "inputSchema": t.inputSchema,
                "sample_arguments": SAMPLE_TOOL_PAYLOADS.get(t.name, {}),
            }
        )

    serialized_resources: List[Dict[str, Any]] = [
        {
            "name": r.name,
            "uri": str(r.uri),
            "mimeType": r.mimeType,
            "description": (r.description or "").strip(),
        }
        for r in resources_list
    ]

    serialized_prompts: List[Dict[str, Any]] = [
        {
            "name": p.name,
            "description": (p.description or "").strip(),
            "arguments": [
                {"name": arg.name, "required": arg.required, "description": arg.description}
                for arg in (p.arguments or [])
            ],
        }
        for p in prompts_list
    ]

    return JSONResponse(
        {
            "service": cfg.server_name,
            "protocol": "Model Context Protocol (MCP)",
            "sse_endpoint": "/sse",
            "messages_endpoint": "/messages/",
            "tools_count": len(serialized_tools),
            "resources_count": len(serialized_resources),
            "prompts_count": len(serialized_prompts),
            "tools": serialized_tools,
            "resources": serialized_resources,
            "prompts": serialized_prompts,
        }
    )


async def api_call_tool_endpoint(request: Request) -> JSONResponse:
    """Execute a registered MCP tool via HTTP POST for the interactive browser playground.

    Summary:
        Accepts `{"name": "<tool_name>", "arguments": {...}}`, invokes the underlying
        MCP tool function, records audit telemetry, and returns the JSON result.

    Parameters:
        request (Request): Incoming HTTP POST request with JSON payload.

    Returns:
        JSONResponse: Tool output payload or HTTP 400/404 error response.
    """
    try:
        body = await request.json()
    except Exception as exc:
        return JSONResponse({"error": f"Invalid JSON request body: {exc}"}, status_code=400)

    tool_name = str(body.get("name") or "").strip()
    arguments = body.get("arguments") or {}

    if tool_name not in TOOL_DISPATCH_REGISTRY:
        return JSONResponse(
            {
                "error": f"Unknown MCP tool '{tool_name}'.",
                "available_tools": list(TOOL_DISPATCH_REGISTRY.keys()),
            },
            status_code=404,
        )

    if not isinstance(arguments, dict):
        return JSONResponse({"error": "'arguments' must be a JSON object."}, status_code=400)

    try:
        result = TOOL_DISPATCH_REGISTRY[tool_name](**arguments)
        return JSONResponse({"status": "success", "tool": tool_name, "result": result})
    except Exception as exc:
        logger.error("Interactive tool execution failed for %s: %s", tool_name, exc)
        return JSONResponse(
            {"status": "error", "tool": tool_name, "error": str(exc)},
            status_code=400,
        )


async def mcp_explorer_ui_endpoint(request: Request) -> HTMLResponse:
    """Render an interactive Bootstrap 5 MCP Tool Explorer & Live Playground UI.

    Summary:
        Provides an institutional-grade web dashboard at `/` and `/tools` allowing
        developers and architects to browse all 10 MCP tools, inspect their JSON
        schemas, view resources/prompts, and execute live tool calls against SQLite.

    Parameters:
        request (Request): Incoming HTTP GET request.

    Returns:
        HTMLResponse: Rendered HTML5 page.
    """
    base_url = str(request.base_url).rstrip("/")
    html_content = f"""<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Aurelia Private Bank — MCP Server &amp; Tool Explorer</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
    <style>
        :root {{
            --aurelia-bg: #080d1a;
            --aurelia-card: #0f172a;
            --aurelia-border: rgba(212, 175, 55, 0.22);
            --aurelia-gold: #d4af37;
            --aurelia-emerald: #10b981;
        }}
        body {{
            background-color: var(--aurelia-bg);
            color: #f8fafc;
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
        }}
        .navbar-aurelia {{
            background: rgba(15, 23, 42, 0.92);
            border-bottom: 1px solid var(--aurelia-border);
            backdrop-filter: blur(12px);
        }}
        .card-aurelia {{
            background: var(--aurelia-card);
            border: 1px solid rgba(148, 163, 184, 0.16);
            border-radius: 12px;
        }}
        .card-aurelia:hover {{
            border-color: var(--aurelia-border);
        }}
        .badge-gold {{
            background: rgba(212, 175, 55, 0.15);
            color: #facc15;
            border: 1px solid rgba(212, 175, 55, 0.35);
        }}
        .badge-read {{
            background: rgba(16, 185, 129, 0.15);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.35);
        }}
        .badge-write {{
            background: rgba(245, 158, 11, 0.15);
            color: #fbbf24;
            border: 1px solid rgba(245, 158, 11, 0.35);
        }}
        .code-panel {{
            background: #050811;
            border: 1px solid rgba(148, 163, 184, 0.2);
            border-radius: 8px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.84rem;
            color: #e2e8f0;
        }}
        .btn-gold {{
            background: linear-gradient(135deg, #d4af37, #b48a1e);
            color: #080d1a;
            font-weight: 600;
            border: none;
        }}
        .btn-gold:hover {{
            background: linear-gradient(135deg, #e5c349, #c89b24);
            color: #080d1a;
        }}
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-aurelia sticky-top py-3">
        <div class="container">
            <div class="d-flex align-items-center gap-3">
                <span class="fs-4 text-warning"><i class="bi bi-cpu-fill"></i></span>
                <div>
                    <div class="fw-bold tracking-wide text-uppercase" style="letter-spacing: 0.06em; color: var(--aurelia-gold);">
                        Aurelia Private Bank &amp; Trust
                    </div>
                    <div class="small text-secondary">Model Context Protocol (MCP) Server &amp; Interactive Tool Explorer</div>
                </div>
            </div>
            <div class="d-flex gap-2">
                <a href="/api/tools" target="_blank" class="btn btn-sm btn-outline-warning">
                    <i class="bi bi-filetype-json me-1"></i>Raw JSON Catalog (<code>/api/tools</code>)
                </a>
                <a href="/health" target="_blank" class="btn btn-sm btn-outline-light">
                    <i class="bi bi-heart-pulse me-1"></i>Health (<code>/health</code>)
                </a>
            </div>
        </div>
    </nav>

    <main class="container py-4">
        <div class="row g-3 mb-4">
            <div class="col-md-3">
                <div class="card card-aurelia p-3 h-100">
                    <div class="text-secondary small text-uppercase">MCP SSE Endpoint</div>
                    <div class="fw-bold mt-1 text-warning text-truncate" title="{base_url}/sse"><code>{base_url}/sse</code></div>
                    <div class="small text-secondary mt-1">JSON-RPC Messages: <code>/messages/</code></div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card card-aurelia p-3 h-100">
                    <div class="text-secondary small text-uppercase">Registered MCP Tools</div>
                    <div class="fs-3 fw-bold mt-1" id="kpi-tools-count">10</div>
                    <div class="small text-success">6 Read/Analytics &bull; 4 Governed Write</div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card card-aurelia p-3 h-100">
                    <div class="text-secondary small text-uppercase">MCP Resources &amp; Prompts</div>
                    <div class="fs-3 fw-bold mt-1">2 / 2</div>
                    <div class="small text-secondary"><code>bank://summary</code> &bull; <code>bank://customers</code></div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card card-aurelia p-3 h-100">
                    <div class="text-secondary small text-uppercase">Live Bank AUM (SQLite)</div>
                    <div class="fs-4 fw-bold mt-1 text-success" id="kpi-aum">Loading...</div>
                    <div class="small text-secondary" id="kpi-clients">Active Clients: --</div>
                </div>
            </div>
        </div>

        <div class="row g-4">
            <!-- Left Column: Tool Catalog -->
            <div class="col-lg-7">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h5 class="mb-0 fw-semibold"><i class="bi bi-tools text-warning me-2"></i>Registered MCP Tools (10)</h5>
                    <span class="badge badge-gold">Click "Test Tool" to load in Playground</span>
                </div>
                <div id="tools-container" class="d-flex flex-column gap-3">
                    <div class="text-center py-5 text-secondary">Loading MCP tool schemas...</div>
                </div>

                <h5 class="mt-5 mb-3 fw-semibold"><i class="bi bi-database-check text-warning me-2"></i>MCP Resources &amp; Prompts</h5>
                <div class="row g-3" id="resources-prompts-container"></div>
            </div>

            <!-- Right Column: Live Tool Execution Playground -->
            <div class="col-lg-5">
                <div class="card card-aurelia p-4 sticky-top" style="top: 90px;">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h5 class="mb-0 fw-semibold"><i class="bi bi-play-circle-fill text-warning me-2"></i>Live MCP Tool Tester</h5>
                        <span class="badge bg-secondary" id="selected-tool-badge">get_customer_360_dossier</span>
                    </div>
                    <p class="small text-secondary mb-3">
                        Select any MCP tool to populate its sample JSON arguments and execute it directly against the live SQLite database.
                    </p>
                    <div class="mb-3">
                        <label class="form-label small text-uppercase text-secondary fw-semibold">Select MCP Tool</label>
                        <select id="playground-tool-select" class="form-select bg-dark text-light border-secondary"></select>
                    </div>
                    <div class="mb-3">
                        <label class="form-label small text-uppercase text-secondary fw-semibold">JSON Arguments Payload</label>
                        <textarea id="playground-args" rows="7" class="form-control code-panel p-3"></textarea>
                    </div>
                    <button id="run-tool-btn" class="btn btn-gold w-100 py-2 mb-3">
                        <i class="bi bi-lightning-charge-fill me-1"></i>Execute MCP Tool Call
                    </button>
                    <div>
                        <div class="d-flex justify-content-between align-items-center mb-1">
                            <span class="small text-uppercase text-secondary fw-semibold">Tool Execution Output</span>
                            <span class="small text-secondary" id="exec-latency"></span>
                        </div>
                        <pre id="playground-output" class="code-panel p-3 mb-0" style="max-height: 360px; overflow-y: auto;">Click "Execute MCP Tool Call" to inspect live banking data...</pre>
                    </div>
                </div>
            </div>
        </div>
    </main>

    <script>
        let catalogData = null;

        async function loadCatalog() {{
            const [healthRes, catalogRes] = await Promise.all([
                fetch('/health'),
                fetch('/api/tools')
            ]);
            const health = await healthRes.json();
            catalogData = await catalogRes.json();

            document.getElementById('kpi-aum').textContent = '$' + Number(health.total_aum_usd).toLocaleString();
            document.getElementById('kpi-clients').textContent = `Active Clients: ${{health.active_clients_count}} | Flagged Wires: ${{health.aml_flagged_wires_count}}`;

            const selectEl = document.getElementById('playground-tool-select');
            const toolsContainer = document.getElementById('tools-container');
            selectEl.innerHTML = '';
            toolsContainer.innerHTML = '';

            catalogData.tools.forEach((tool, idx) => {{
                const opt = document.createElement('option');
                opt.value = tool.name;
                opt.textContent = `${{tool.name}} (${{tool.category}})`;
                selectEl.appendChild(opt);

                const isRead = tool.category.includes('Read');
                const badgeClass = isRead ? 'badge-read' : 'badge-write';
                const props = (tool.inputSchema && tool.inputSchema.properties) ? Object.keys(tool.inputSchema.properties) : [];
                const summaryLine = tool.description.split('\\n')[0];

                const card = document.createElement('div');
                card.className = 'card card-aurelia p-3';
                card.innerHTML = `
                    <div class="d-flex justify-content-between align-items-start gap-2">
                        <div>
                            <div class="d-flex align-items-center gap-2 flex-wrap">
                                <code class="fs-6 fw-bold text-warning">${{tool.name}}</code>
                                <span class="badge ${{badgeClass}}">${{tool.category}}</span>
                            </div>
                            <p class="small text-light mt-2 mb-2">${{summaryLine}}</p>
                            <div class="small text-secondary">
                                <strong>Parameters:</strong> ${{props.length ? props.map(p => `<code>${{p}}</code>`).join(', ') : '<em>None</em>'}}
                            </div>
                        </div>
                        <button class="btn btn-sm btn-outline-warning flex-shrink-0" onclick="selectToolForTest('${{tool.name}}')">
                            <i class="bi bi-terminal me-1"></i>Test Tool
                        </button>
                    </div>
                    <details class="mt-2">
                        <summary class="small text-secondary" style="cursor:pointer;">View JSON Input Schema &amp; Full Docstring</summary>
                        <pre class="code-panel p-2 mt-2 mb-0">${{JSON.stringify(tool.inputSchema, null, 2)}}</pre>
                    </details>
                `;
                toolsContainer.appendChild(card);
            }});

            const rpContainer = document.getElementById('resources-prompts-container');
            rpContainer.innerHTML = '';
            catalogData.resources.forEach(r => {{
                rpContainer.innerHTML += `
                    <div class="col-md-6">
                        <div class="card card-aurelia p-3 h-100">
                            <div class="d-flex justify-content-between">
                                <code class="text-info fw-bold">${{r.uri}}</code>
                                <span class="badge bg-info bg-opacity-10 text-info border border-info border-opacity-25">MCP Resource</span>
                            </div>
                            <div class="small text-secondary mt-2">${{r.description.split('\\n')[0]}}</div>
                        </div>
                    </div>
                `;
            }});
            catalogData.prompts.forEach(p => {{
                rpContainer.innerHTML += `
                    <div class="col-md-6">
                        <div class="card card-aurelia p-3 h-100">
                            <div class="d-flex justify-content-between">
                                <code class="text-warning fw-bold">${{p.name}}</code>
                                <span class="badge badge-gold">MCP Prompt</span>
                            </div>
                            <div class="small text-secondary mt-2">${{p.description.split('\\n')[0]}}</div>
                        </div>
                    </div>
                `;
            }});

            selectToolForTest('get_customer_360_dossier');
        }}

        function selectToolForTest(toolName) {{
            const tool = catalogData.tools.find(t => t.name === toolName) || catalogData.tools[0];
            document.getElementById('playground-tool-select').value = tool.name;
            document.getElementById('selected-tool-badge').textContent = tool.name;
            document.getElementById('playground-args').value = JSON.stringify(tool.sample_arguments || {{}}, null, 2);
        }}

        document.getElementById('playground-tool-select').addEventListener('change', (e) => {{
            selectToolForTest(e.target.value);
        }});

        document.getElementById('run-tool-btn').addEventListener('click', async () => {{
            const toolName = document.getElementById('playground-tool-select').value;
            const outputEl = document.getElementById('playground-output');
            const latencyEl = document.getElementById('exec-latency');
            let parsedArgs = {{}};
            try {{
                parsedArgs = JSON.parse(document.getElementById('playground-args').value || '{{}}');
            }} catch (err) {{
                outputEl.textContent = 'Invalid JSON in arguments box: ' + err.message;
                return;
            }}

            outputEl.textContent = 'Executing MCP tool ' + toolName + '...';
            const t0 = performance.now();
            try {{
                const res = await fetch('/api/tools/call', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ name: toolName, arguments: parsedArgs }})
                }});
                const data = await res.json();
                const elapsed = (performance.now() - t0).toFixed(1);
                latencyEl.textContent = `${{elapsed}} ms`;
                outputEl.textContent = JSON.stringify(data, null, 2);
            }} catch (err) {{
                outputEl.textContent = 'Request failed: ' + err.message;
            }}
        }});

        loadCatalog();
    </script>
</body>
</html>"""
    return HTMLResponse(html_content)


def create_asgi_app(transport_mode: str = "sse") -> Starlette:
    """Build the composite Starlette ASGI application hosting FastMCP, Tool Explorer UI, and `/health`.

    Summary:
        Mounts the interactive MCP Tool Explorer UI (`/`, `/tools`), JSON catalog
        (`/api/tools`), live tool invocation endpoint (`/api/tools/call`), `/health`,
        and FastMCP's SSE or Streamable HTTP ASGI app.

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
            Route("/", endpoint=mcp_explorer_ui_endpoint, methods=["GET"]),
            Route("/tools", endpoint=mcp_explorer_ui_endpoint, methods=["GET"]),
            Route("/api/tools", endpoint=api_tools_catalog_endpoint, methods=["GET"]),
            Route("/api/tools/call", endpoint=api_call_tool_endpoint, methods=["POST"]),
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
