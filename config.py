# ==============================================================================
# Purpose: Centralized environment configuration and validation module.
# Architecture/Context: Loaded by `app.py` and `database.py` to provide typed,
#   validated runtime settings without hardcoding secrets or file paths.
# Dependencies/Side Effects: Reads OS environment variables and `.env` file via
#   `python-dotenv`; configures application-wide structured logging.
# Author: Pradeep Singh
# License: Apache-2.0
# ==============================================================================

import logging
import os
import secrets
from dataclasses import dataclass
from dotenv import load_dotenv

# Load environment variables from .env if present in local development
load_dotenv()


def configure_logging(log_level_name: str = "INFO") -> logging.Logger:
    """Configure structured semantic logging for the Bank CRM application.

    Summary:
        Initializes the root and application logger with ISO-8601 timestamps,
        severity levels, and module context.

    Parameters:
        log_level_name (str): Desired logging level name (DEBUG, INFO, WARNING, ERROR).

    Returns:
        logging.Logger: Configured application logger instance (`aurelia_crm`).

    Raises:
        ValueError: Never raised directly; invalid levels safely fall back to INFO.
    """
    resolved_level = getattr(logging, str(log_level_name).upper(), logging.INFO)

    logging.basicConfig(
        level=resolved_level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    logger = logging.getLogger("aurelia_crm")
    logger.setLevel(resolved_level)
    return logger


@dataclass(frozen=True)
class AppConfig:
    """Immutable configuration container populated from environment variables.

    Summary:
        Holds validated runtime parameters for Flask, SQLite storage, and banking
        compliance rules.

    Parameters:
        flask_env (str): Runtime environment identifier ('development' or 'production').
        flask_debug (bool): Flag enabling/disabling Flask interactive debugger.
        secret_key (str): Cryptographic key signing Flask session cookies.
        host (str): Network interface IP address to bind the HTTP server.
        port (int): TCP port number for the HTTP server.
        database_path (str): Filesystem path to the SQLite database file.
        auto_seed_database (bool): Whether to populate demo records when DB is empty.
        bank_name (str): Display name of the financial institution.
        high_value_wire_threshold_usd (float): USD amount triggering AML/risk review flags.
        default_rm_email (str): Email of the default Relationship Manager persona.
        log_level (str): Configured semantic logging verbosity.
    """

    flask_env: str
    flask_debug: bool
    secret_key: str
    host: str
    port: int
    database_path: str
    auto_seed_database: bool
    bank_name: str
    high_value_wire_threshold_usd: float
    default_rm_email: str
    log_level: str


def load_config() -> AppConfig:
    """Load, sanitize, and validate configuration values from environment variables.

    Summary:
        Reads environment keys with safe defaults for local development while enforcing
        defensive type conversion and boundary checks.

    Parameters:
        None

    Returns:
        AppConfig: Frozen configuration instance ready for application startup.

    Raises:
        ValueError: If numeric environment variables (`PORT`, `HIGH_VALUE_WIRE_THRESHOLD_USD`)
            are outside valid operational bounds.
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    logger = configure_logging(log_level)

    flask_env = os.getenv("FLASK_ENV", "development").strip().lower()
    flask_debug = os.getenv("FLASK_DEBUG", "false").strip().lower() in ("1", "true", "yes")

    # Generate an ephemeral cryptographic key if SECRET_KEY is not set in environment
    raw_secret = os.getenv("SECRET_KEY", "").strip()
    if not raw_secret or raw_secret.startswith("replace-with-"):
        raw_secret = secrets.token_hex(32)
        logger.info("Using dynamically generated ephemeral SECRET_KEY for session signing.")

    host = os.getenv("HOST", "0.0.0.0").strip()

    try:
        port = int(os.getenv("PORT", "8080").strip())
    except ValueError as exc:
        logger.error("Invalid PORT environment variable; defaulting to 8080: %s", exc)
        port = 8080

    if not (1 <= port <= 65535):
        raise ValueError(f"PORT must be between 1 and 65535, got {port}")

    base_dir = os.path.abspath(os.path.dirname(__file__))
    raw_db_path = os.getenv("DATABASE_PATH", "bank_crm.db").strip()
    database_path = (
        raw_db_path if os.path.isabs(raw_db_path) else os.path.join(base_dir, raw_db_path)
    )

    auto_seed_database = os.getenv("AUTO_SEED_DATABASE", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    bank_name = os.getenv("BANK_NAME", "Aurelia Private Bank & Trust").strip()

    try:
        wire_threshold = float(os.getenv("HIGH_VALUE_WIRE_THRESHOLD_USD", "25000.00").strip())
    except ValueError as exc:
        logger.warning("Invalid HIGH_VALUE_WIRE_THRESHOLD_USD; fallback to 25000.00: %s", exc)
        wire_threshold = 25000.00

    if wire_threshold <= 0:
        raise ValueError("HIGH_VALUE_WIRE_THRESHOLD_USD must be strictly positive.")

    default_rm_email = os.getenv(
        "DEFAULT_RM_EMAIL", "elena.rostova@aureliabank.com"
    ).strip()

    return AppConfig(
        flask_env=flask_env,
        flask_debug=flask_debug,
        secret_key=raw_secret,
        host=host,
        port=port,
        database_path=database_path,
        auto_seed_database=auto_seed_database,
        bank_name=bank_name,
        high_value_wire_threshold_usd=wire_threshold,
        default_rm_email=default_rm_email,
        log_level=log_level,
    )
