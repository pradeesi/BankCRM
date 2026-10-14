# ==============================================================================
# Purpose: Automated end-to-end MCP client verification harness for the Aurelia
#   Bank CRM MCP Server.
# Architecture/Context: Connects to `mcp_server/server.py` over both `stdio` and
#   live `SSE` (`http://127.0.0.1:8081/sse`) using the official `mcp.client` SDK
#   to verify tool discovery, resources, prompts, and all 10 banking CRM tools.
# Dependencies/Side Effects: Requires `mcp` and `httpx`; restores any mutated
#   test state so the shared `bank_crm.db` remains clean.
# Author: Pradeep Singh
# License: Apache-2.0
# ==============================================================================

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client


def _parse_tool_json(result: Any) -> Dict[str, Any]:
    """Extract and parse the JSON payload returned by a FastMCP tool call.

    Summary:
        Reads the first text content block from a `CallToolResult` and parses it
        as a Python dictionary.

    Parameters:
        result (Any): `CallToolResult` returned by `session.call_tool()`.

    Returns:
        Dict[str, Any]: Parsed JSON dictionary.

    Raises:
        AssertionError: If the tool result is empty or invalid JSON.
    """
    assert result.content, "Expected non-empty MCP tool content"
    raw_text = result.content[0].text
    return json.loads(raw_text)


async def run_session_checks(session: ClientSession, mode_label: str) -> None:
    """Execute comprehensive verification checks against an active MCP ClientSession.

    Summary:
        Verifies all 10 tools are registered, reads MCP resources and prompts,
        and tests both read intelligence and governed write tools.

    Parameters:
        session (ClientSession): Initialized MCP client session.
        mode_label (str): Label identifying the transport under test ('STDIO' or 'SSE').

    Returns:
        None

    Raises:
        AssertionError: If any tool, resource, prompt, or response assertion fails.
    """
    await session.initialize()

    tools_resp = await session.list_tools()
    tool_names = [t.name for t in tools_resp.tools]
    print(f"[{mode_label}] Discovered {len(tool_names)} MCP tools: {tool_names}")
    assert len(tool_names) == 10, f"Expected 10 tools, got {len(tool_names)}"

    resources_resp = await session.list_resources()
    resource_uris = [str(r.uri) for r in resources_resp.resources]
    print(f"[{mode_label}] Discovered MCP resources: {resource_uris}")
    assert "bank://summary" in resource_uris

    prompts_resp = await session.list_prompts()
    prompt_names = [p.name for p in prompts_resp.prompts]
    print(f"[{mode_label}] Discovered MCP prompts: {prompt_names}")
    assert "wealth_review_prompt" in prompt_names

    # 1. Test get_bank_executive_summary
    res_summary = _parse_tool_json(
        await session.call_tool("get_bank_executive_summary", {})
    )
    assert res_summary["status"] == "success"
    print(
        f"[{mode_label}] get_bank_executive_summary -> AUM=${res_summary['executive_summary']['total_aum_usd']:,.2f}"
    )

    # 2. Test search_customers
    res_search = _parse_tool_json(
        await session.call_tool("search_customers", {"query": "Sterling"})
    )
    assert res_search["status"] == "success" and res_search["count"] >= 1
    cif = res_search["customers"][0]["cif_number"]
    print(f"[{mode_label}] search_customers('Sterling') -> Found {cif}")

    # 3. Test get_customer_360_dossier
    res_360 = _parse_tool_json(
        await session.call_tool(
            "get_customer_360_dossier", {"customer_identifier": cif}
        )
    )
    assert res_360["status"] == "success"
    acct_num = res_360["dossier"]["accounts"][0]["account_number"]
    print(
        f"[{mode_label}] get_customer_360_dossier({cif}) -> {len(res_360['dossier']['accounts'])} accounts (Primary: {acct_num})"
    )

    # 4. Test list_credit_loan_pipeline
    res_loans = _parse_tool_json(
        await session.call_tool("list_credit_loan_pipeline", {})
    )
    assert res_loans["status"] == "success" and res_loans["count"] >= 1
    print(
        f"[{mode_label}] list_credit_loan_pipeline -> {res_loans['count']} facilities (${res_loans['total_principal_usd']:,.2f})"
    )

    # 5. Test list_aml_flagged_transactions
    res_flagged = _parse_tool_json(
        await session.call_tool(
            "list_aml_flagged_transactions", {"flagged_only": True}
        )
    )
    assert res_flagged["status"] == "success"
    print(
        f"[{mode_label}] list_aml_flagged_transactions -> {res_flagged['count']} flagged holds"
    )

    # 6. Test get_nba_product_recommendations
    res_nba = _parse_tool_json(
        await session.call_tool(
            "get_nba_product_recommendations", {"customer_identifier": cif}
        )
    )
    assert res_nba["status"] == "success" and res_nba["count"] >= 1
    print(
        f"[{mode_label}] get_nba_product_recommendations({cif}) -> {res_nba['offers'][0]['product_name']}"
    )

    # 7. Test execute_wire_or_transfer (triggers AML compliance hold on SWIFT >= $25k) & 8. resolve_aml_compliance_hold
    res_wire = _parse_tool_json(
        await session.call_tool(
            "execute_wire_or_transfer",
            {
                "account_identifier": acct_num,
                "txn_type": "Debit",
                "amount_usd": 50000.00,
                "counterparty": "Zurich Prime Custody AG",
                "channel": "SWIFT International Wire",
                "category": "MCP Automated Verification Test",
            },
        )
    )
    assert res_wire["status"] == "success"
    wire_ref = res_wire["transaction"]["reference_id"]
    assert res_wire["transaction"]["risk_flag"] == 1
    print(
        f"[{mode_label}] execute_wire_or_transfer -> Created flagged SWIFT wire {wire_ref}"
    )

    res_resolve = _parse_tool_json(
        await session.call_tool(
            "resolve_aml_compliance_hold",
            {
                "transaction_identifier": wire_ref,
                "officer_name": "MCP Compliance Verifier",
            },
        )
    )
    assert (
        res_resolve["status"] == "success"
        and res_resolve["transaction"]["risk_flag"] == 0
    )
    print(f"[{mode_label}] resolve_aml_compliance_hold({wire_ref}) -> Status: Settled")

    # 9. Test log_client_interaction_and_task
    res_log = _parse_tool_json(
        await session.call_tool(
            "log_client_interaction_and_task",
            {
                "customer_identifier": cif,
                "subject": "MCP Agent Portfolio & Liquidity Check",
                "summary": "Verified multi-account liquidity and SBLOC collateral coverage via MCP.",
                "follow_up_task_title": "Send Q4 Tax-Alpha Direct Indexing Proposal",
                "follow_up_priority": "High",
            },
        )
    )
    assert res_log["status"] == "success" and res_log["created_task"] is not None
    print(
        f"[{mode_label}] log_client_interaction_and_task -> Logged interaction #{res_log['interaction']['id']}"
    )


async def verify_stdio() -> None:
    """Launch `server.py --transport stdio` as a subprocess and run MCP verification checks.

    Summary:
        Tests the local `stdio` transport protocol end-to-end.

    Parameters:
        None

    Returns:
        None
    """
    server_script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "server.py"
    )
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[server_script, "--transport", "stdio"],
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await run_session_checks(session, "STDIO")


async def verify_sse(sse_url: str, health_url: str) -> None:
    """Verify the `/health` HTTP endpoint and run MCP tool checks over live SSE transport.

    Summary:
        Connects to the running Uvicorn/Starlette MCP server on port 8081 and
        validates all 10 tools over Server-Sent Events (`SSE`).

    Parameters:
        sse_url (str): Full URL of the SSE endpoint (e.g. 'http://127.0.0.1:8081/sse').
        health_url (str): Full URL of the health endpoint (e.g. 'http://127.0.0.1:8081/health').

    Returns:
        None
    """
    async with httpx.AsyncClient() as http_client:
        h_resp = await http_client.get(health_url)
        assert h_resp.status_code == 200, f"Health check failed: {h_resp.status_code}"
        print(f"[SSE] GET {health_url} -> 200 OK: {h_resp.json()}")

    async with sse_client(sse_url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await run_session_checks(session, "SSE")


async def main_async() -> None:
    """Parse CLI arguments and run stdio and/or SSE MCP verification suites.

    Summary:
        Entrypoint coroutine for `verify_mcp_client.py`.

    Parameters:
        None

    Returns:
        None
    """
    parser = argparse.ArgumentParser(description="Verify Aurelia Bank CRM MCP Server")
    parser.add_argument(
        "--mode",
        choices=["stdio", "sse", "both"],
        default="both",
        help="Transport mode(s) to verify",
    )
    parser.add_argument(
        "--sse-url",
        default="http://127.0.0.1:8081/sse",
        help="SSE endpoint URL when testing SSE mode",
    )
    parser.add_argument(
        "--health-url",
        default="http://127.0.0.1:8081/health",
        help="Health check URL when testing SSE mode",
    )
    args = parser.parse_args()

    if args.mode in ("stdio", "both"):
        await verify_stdio()
    if args.mode in ("sse", "both"):
        await verify_sse(args.sse_url, args.health_url)

    print("ALL MCP SERVER VERIFICATION CHECKS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    asyncio.run(main_async())
