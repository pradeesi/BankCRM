# ==============================================================================
# Purpose: Centralized configuration and structured logging module for the
#   Aurelia Bank CRM MCP Server.
# Architecture/Context: Loaded by `mcp_server/server.py` and `db_client.py` to
#   validate environment settings, database file resolution, and transport flags.
# Dependencies/Side Effects: Reads `mcp_server/.env` and OS environment variables;
#   configures stderr logging so `stdio` MCP transport remains clean on stdout.
# Author: Pradeep Singh
# License: Apache-2.0
# ==============================================================================

import logging
import os
import sys
from dataclasses import dataclass
from dotenv import load_dotenv

# Load environment variables from mcp_server/.env if present
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def configure_mcp_logging(log_level_name: str = "INFO") -> logging.Logger:
    """Configure structured semantic logging routed to stderr for MCP compatibility.

    Summary:
        Directs all log records to `sys.stderr` so JSON-RPC messages over `stdio`
        are never corrupted by application log output.

    Parameters:
        log_level_name (str): Logging severity level ('DEBUG', 'INFO', 'WARNING', 'ERROR').

    Returns:
        logging.Logger: Configured logger instance (`aurelia_mcp`).
    """
    resolved_level = getattr(logging, str(log_level_name).upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
    )

    logger = logging.getLogger("aurelia_mcp")
    logger.setLevel(resolved_level)
    if not logger.handlers:
        logger.addHandler(handler)
    logger.propagate = False
    return logger


@dataclass(frozen=True)
class MCPServerConfig:
    """Immutable configuration container for the Aurelia Bank CRM MCP Server.

    Summary:
        Holds validated network, transport, database, compliance, and security
        settings for the MCP server process.

    Parameters:
        server_name (str): Registered MCP server identifier.
        transport (str): Default MCP transport mode ('sse', 'streamable-http', or 'stdio').
        host (str): Bind IP address when running HTTP/SSE transport.
        port (int): TCP port when running HTTP/SSE transport.
        database_path (str): Resolved absolute path to the SQLite database file.
        auto_seed_database (bool): Whether to initialize and seed the DB if empty.
        high_value_wire_threshold_usd (float): USD amount triggering AML review holds.
        default_agent_actor (str): Default actor attribution for agent-logged actions.
        mcp_api_key (str): Optional API key required on HTTP requests when non-empty.
        log_level (str): Configured logging verbosity.
    """

    server_name: str
    transport: str
    host: str
    port: int
    database_path: str
    auto_seed_database: bool
    high_value_wire_threshold_usd: float
    default_agent_actor: str
    mcp_api_key: str
    log_level: str


def load_mcp_config() -> MCPServerConfig:
    """Load and validate MCP server configuration from environment variables.

    Summary:
        Resolves the SQLite database path relative to `mcp_server/` (defaulting to
        `../bank_crm.db` so it shares live state with the Flask CRM web app) and
        validates port and compliance thresholds.

    Parameters:
        None

    Returns:
        MCPServerConfig: Frozen, validated configuration object.

    Raises:
        ValueError: If `MCP_PORT` or `HIGH_VALUE_WIRE_THRESHOLD_USD` are invalid.
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    logger = configure_mcp_logging(log_level)

    server_name = os.getenv("MCP_SERVER_NAME", "aurelia-bank-crm-mcp").strip()
    transport = os.getenv("MCP_TRANSPORT", "sse").strip().lower()
    if transport not in ("sse", "streamable-http", "stdio"):
        logger.warning("Unrecognized MCP_TRANSPORT '%s'; defaulting to 'sse'", transport)
        transport = "sse"

    host = os.getenv("MCP_HOST", "0.0.0.0").strip()

    try:
        port = int(os.getenv("MCP_PORT", os.getenv("PORT", "8081")).strip())
    except ValueError as exc:
        logger.error("Invalid MCP_PORT value; falling back to 8081: %s", exc)
        port = 8081

    if not (1 <= port <= 65535):
        raise ValueError(f"MCP_PORT must be between 1 and 65535, got {port}")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    raw_db_path = os.getenv("DATABASE_PATH", "../bank_crm.db").strip()
    database_path = (
        raw_db_path
        if os.path.isabs(raw_db_path)
        else os.path.abspath(os.path.join(base_dir, raw_db_path))
    )

    auto_seed = os.getenv("AUTO_SEED_DATABASE", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    try:
        wire_threshold = float(
            os.getenv("HIGH_VALUE_WIRE_THRESHOLD_USD", "25000.00").strip()
        )
    except ValueError:
        wire_threshold = 25000.00

    if wire_threshold <= 0:
        raise ValueError("HIGH_VALUE_WIRE_THRESHOLD_USD must be greater than zero.")

    default_agent_actor = os.getenv(
        "DEFAULT_AGENT_ACTOR", "Aurelia AI Advisory Agent"
    ).strip()
    mcp_api_key = os.getenv("MCP_API_KEY", "").strip()

    return MCPServerConfig(
        server_name=server_name,
        transport=transport,
        host=host,
        port=port,
        database_path=database_path,
        auto_seed_database=auto_seed,
        high_value_wire_threshold_usd=wire_threshold,
        default_agent_actor=default_agent_actor,
        mcp_api_key=mcp_api_key,
        log_level=log_level,
    )
