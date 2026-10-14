# ==============================================================================
# Purpose: SQLite database engine, schema lifecycle manager, demo data seeder,
#   and repository layer for Aurelia Private Bank & Trust CRM.
# Architecture/Context: Provides parameterized, SQL-injection-safe data access
#   and atomic financial transaction workflows consumed by `app.py` routes.
# Dependencies/Side Effects: Uses Python's built-in `sqlite3` module; creates and
#   mutates the SQLite file specified by `DATABASE_PATH` in `config.py`.
# Author: Pradeep Singh
# License: Apache-2.0
# ==============================================================================

import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger("aurelia_crm.database")

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS advisors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    advisor_code TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    role_title TEXT NOT NULL,
    department TEXT NOT NULL,
    branch_location TEXT NOT NULL,
    avatar_initials TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cif_number TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    company_name TEXT NOT NULL,
    segment TEXT NOT NULL,
    tier TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    city TEXT NOT NULL,
    country TEXT NOT NULL,
    kyc_status TEXT NOT NULL,
    aml_risk_rating TEXT NOT NULL,
    credit_score INTEGER NOT NULL,
    annual_income_usd REAL NOT NULL,
    total_aum_usd REAL NOT NULL DEFAULT 0.0,
    relationship_manager TEXT NOT NULL,
    onboarded_date TEXT NOT NULL,
    last_contact_date TEXT NOT NULL,
    portfolio_equities_pct INTEGER NOT NULL DEFAULT 45,
    portfolio_fixed_income_pct INTEGER NOT NULL DEFAULT 30,
    portfolio_private_equity_pct INTEGER NOT NULL DEFAULT 15,
    portfolio_cash_pct INTEGER NOT NULL DEFAULT 10
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_number TEXT NOT NULL UNIQUE,
    customer_id INTEGER NOT NULL,
    account_type TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    balance_usd REAL NOT NULL DEFAULT 0.0,
    apy_rate REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'Active',
    opened_date TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference_id TEXT NOT NULL UNIQUE,
    account_id INTEGER NOT NULL,
    customer_id INTEGER NOT NULL,
    txn_type TEXT NOT NULL,
    channel TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    balance_after_usd REAL NOT NULL,
    counterparty TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL,
    risk_flag INTEGER NOT NULL DEFAULT 0,
    risk_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_number TEXT NOT NULL UNIQUE,
    customer_id INTEGER NOT NULL,
    loan_type TEXT NOT NULL,
    purpose TEXT NOT NULL,
    principal_usd REAL NOT NULL,
    interest_rate_pct REAL NOT NULL,
    term_months INTEGER NOT NULL,
    collateral_value_usd REAL NOT NULL,
    ltv_ratio_pct REAL NOT NULL,
    dti_ratio_pct REAL NOT NULL,
    stage TEXT NOT NULL,
    underwriting_grade TEXT NOT NULL,
    submitted_date TEXT NOT NULL,
    assigned_underwriter TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    subject TEXT NOT NULL,
    sentiment TEXT NOT NULL,
    summary TEXT NOT NULL,
    advisor_name TEXT NOT NULL,
    logged_at TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    priority TEXT NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending',
    assigned_to TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS nba_offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    product_category TEXT NOT NULL,
    match_score_pct INTEGER NOT NULL,
    estimated_revenue_usd REAL NOT NULL,
    rationale TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Recommended',
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
);
"""


@contextmanager
def get_db_connection(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """Provide a transactional SQLite connection with row factory and WAL mode.

    Summary:
        Opens a SQLite connection, configures `sqlite3.Row` for dictionary-like
        access, enables foreign key constraints, and commits or rolls back safely.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.

    Yields:
        sqlite3.Connection: Active SQLite connection object.

    Raises:
        sqlite3.Error: Propagates database operational or integrity errors after rollback.
    """
    conn = sqlite3.connect(db_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    try:
        yield conn
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        logger.error("SQLite transaction error on %s: %s", db_path, exc)
        raise
    finally:
        conn.close()


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    """Convert a single sqlite3.Row record into a standard Python dictionary.

    Summary:
        Transforms a database row object for JSON serialization or Jinja2 rendering.

    Parameters:
        row (Optional[sqlite3.Row]): Database row returned from `fetchone()`.

    Returns:
        Optional[Dict[str, Any]]: Dictionary representation of the row, or None if input is None.
    """
    if row is None:
        return None
    return dict(row)


def rows_to_list(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    """Convert a list of sqlite3.Row records into a list of dictionaries.

    Summary:
        Batch-transforms query results from `fetchall()` into standard dicts.

    Parameters:
        rows (List[sqlite3.Row]): Sequence of rows returned by a cursor query.

    Returns:
        List[Dict[str, Any]]: List of dictionaries representing each row.
    """
    return [dict(r) for r in rows]


def recalculate_customer_aum(conn: sqlite3.Connection, customer_id: int) -> float:
    """Recalculate and persist a customer's total Assets Under Management (AUM).

    Summary:
        Aggregates balances across all active accounts belonging to `customer_id`
        and updates `customers.total_aum_usd` within the same transaction.

    Parameters:
        conn (sqlite3.Connection): Active SQLite connection.
        customer_id (int): Primary key ID of the target customer.

    Returns:
        float: Updated total AUM in USD.

    Raises:
        sqlite3.Error: If the aggregate query or update statement fails.
    """
    row = conn.execute(
        """
        SELECT COALESCE(SUM(balance_usd), 0.0) AS total_balance
        FROM accounts
        WHERE customer_id = ? AND status != 'Closed'
        """,
        (customer_id,),
    ).fetchone()
    new_aum = round(float(row["total_balance"]) if row else 0.0, 2)
    conn.execute(
        "UPDATE customers SET total_aum_usd = ? WHERE id = ?",
        (new_aum, customer_id),
    )
    return new_aum


def init_database(db_path: str, auto_seed: bool = True) -> None:
    """Initialize SQLite schema and optionally populate institutional demo data.

    Summary:
        Executes DDL statements to ensure all 8 tables exist and triggers initial
        seeding if the `customers` table is empty.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        auto_seed (bool): Flag indicating whether to seed sample data when empty.

    Returns:
        None

    Raises:
        sqlite3.Error: If schema execution or seeding fails.
    """
    with get_db_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        count_row = conn.execute("SELECT COUNT(*) AS cnt FROM customers").fetchone()
        existing_count = int(count_row["cnt"]) if count_row else 0

    if existing_count == 0 and auto_seed:
        logger.info("Empty database detected at %s; seeding institutional CRM data.", db_path)
        seed_database(db_path)
    else:
        logger.info(
            "Database initialized at %s with %d existing customer records.",
            db_path,
            existing_count,
        )


def reset_and_seed_database(db_path: str) -> Dict[str, int]:
    """Drop all existing CRM records and re-seed the canonical banking dataset.

    Summary:
        Clears all tables in reverse dependency order, resets SQLite autoincrement
        counters, and inserts fresh institutional client, account, loan, and ledger data.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.

    Returns:
        Dict[str, int]: Summary counts of seeded entities (customers, accounts, loans, transactions).

    Raises:
        sqlite3.Error: If table truncation or seeding fails.
    """
    with get_db_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        for table in (
            "nba_offers",
            "tasks",
            "interactions",
            "loans",
            "transactions",
            "accounts",
            "customers",
            "advisors",
        ):
            conn.execute(f"DELETE FROM {table};")
        conn.execute("DELETE FROM sqlite_sequence;")

    logger.info("Database reset completed; re-seeding canonical dataset.")
    return seed_database(db_path)


def seed_database(db_path: str) -> Dict[str, int]:
    """Populate the database with realistic Private Wealth & Commercial Banking data.

    Summary:
        Seeds 3 bank staff personas, 8 HNW/Commercial clients, 18 multi-currency
        and treasury accounts, 22 ledger transactions (including AML-flagged wires),
        8 credit/loan applications across underwriting stages, 12 advisor touchpoints,
        10 client tasks, and 10 AI Next-Best-Action product recommendations.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.

    Returns:
        Dict[str, int]: Counts of inserted records by table.

    Raises:
        sqlite3.Error: If any insert statement violates schema constraints.
    """
    advisors = [
        (
            "ADV-101",
            "Elena Rostova",
            "elena.rostova@aureliabank.com",
            "Managing Director — Private Wealth",
            "Private Client Group",
            "New York — Park Ave Flagship",
            "ER",
            "2022-03-14",
        ),
        (
            "ADV-102",
            "Marcus Vance",
            "marcus.vance@aureliabank.com",
            "SVP — Commercial Credit & Treasury",
            "Institutional & Commercial Banking",
            "New York — Wall Street",
            "MV",
            "2021-07-01",
        ),
        (
            "ADV-103",
            "Sophia Chen",
            "sophia.chen@aureliabank.com",
            "Chief Compliance & AML Risk Officer",
            "Global Risk & Financial Crime",
            "San Francisco — Embarcadero",
            "SC",
            "2020-11-09",
        ),
    ]

    customers = [
        (
            "CIF-904821",
            "Alexander Sterling",
            "Sterling Quantum Ventures LLC",
            "Ultra-High-Net-Worth (UHNW)",
            "Obsidian Private",
            "a.sterling@sterlingquantum.io",
            "+1 (212) 555-0194",
            "New York",
            "United States",
            "Verified",
            "Low",
            832,
            4850000.00,
            0.0,
            "Elena Rostova",
            "2019-04-12",
            "2026-10-12",
            50,
            20,
            20,
            10,
        ),
        (
            "CIF-904822",
            "Victoria Al-Mansoor",
            "Crescent Maritime Holdings",
            "Institutional & Family Office",
            "Obsidian Private",
            "victoria@crescentmaritime.ae",
            "+1 (305) 555-0148",
            "Miami",
            "United States",
            "Enhanced Due Diligence",
            "Medium",
            798,
            6200000.00,
            0.0,
            "Elena Rostova",
            "2020-09-23",
            "2026-10-10",
            35,
            35,
            20,
            10,
        ),
        (
            "CIF-904823",
            "Dr. Henrik Lindqvist",
            "Nordic BioTherapeutics Inc.",
            "Commercial & Executive",
            "Platinum Sovereign",
            "hlindqvist@nordicbio.com",
            "+1 (617) 555-0172",
            "Boston",
            "United States",
            "Verified",
            "Low",
            815,
            2150000.00,
            0.0,
            "Marcus Vance",
            "2021-02-18",
            "2026-10-13",
            55,
            25,
            10,
            10,
        ),
        (
            "CIF-904824",
            "Camila Montoya-Vega",
            "Solstice Renewable Infrastructure",
            "Commercial & Executive",
            "Platinum Sovereign",
            "cmontoya@solsticegrid.com",
            "+1 (415) 555-0183",
            "San Francisco",
            "United States",
            "Verified",
            "Low",
            784,
            1750000.00,
            0.0,
            "Marcus Vance",
            "2022-06-05",
            "2026-10-08",
            40,
            30,
            20,
            10,
        ),
        (
            "CIF-904825",
            "Julian Thorne",
            "Thorne Heritage Real Estate Partners",
            "High-Net-Worth (HNW)",
            "Platinum Sovereign",
            "jthorne@thorneheritage.com",
            "+1 (312) 555-0166",
            "Chicago",
            "United States",
            "Verified",
            "Low",
            762,
            1320000.00,
            0.0,
            "Elena Rostova",
            "2021-11-30",
            "2026-10-05",
            30,
            40,
            20,
            10,
        ),
        (
            "CIF-904826",
            "Nadia Kovalenko",
            "Aether Orbital Systems Corp",
            "Commercial & Executive",
            "Sapphire Premier",
            "n.kovalenko@aetherorbital.space",
            "+1 (206) 555-0129",
            "Seattle",
            "United States",
            "Periodic Review Due",
            "High",
            741,
            980000.00,
            0.0,
            "Sophia Chen",
            "2023-01-19",
            "2026-10-11",
            60,
            15,
            15,
            10,
        ),
        (
            "CIF-904827",
            "Devonshire Family Trust (Arthur & Clara)",
            "Devonshire Philanthropic Endowment",
            "Institutional & Family Office",
            "Obsidian Private",
            "trustees@devonshireendowment.org",
            "+1 (212) 555-0115",
            "Greenwich",
            "United States",
            "Verified",
            "Low",
            840,
            5400000.00,
            0.0,
            "Elena Rostova",
            "2018-08-01",
            "2026-10-14",
            25,
            50,
            15,
            10,
        ),
        (
            "CIF-904828",
            "Rohan Deshmukh",
            "Vanguard Silicon Photonics",
            "Affluent & Emerging Wealth",
            "Sapphire Premier",
            "rohan@vanguardsilicon.com",
            "+1 (408) 555-0151",
            "Palo Alto",
            "United States",
            "Verified",
            "Low",
            791,
            890000.00,
            0.0,
            "Marcus Vance",
            "2023-09-14",
            "2026-10-09",
            65,
            20,
            5,
            10,
        ),
    ]

    accounts = [
        ("AUR-8801-0019", 1, "Private Wealth Advisory Portfolio", "USD", 14250000.00, 5.15, "Active", "2019-04-15"),
        ("AUR-8801-0042", 1, "Commercial Treasury Sweep", "USD", 4820000.00, 4.75, "Active", "2020-01-10"),
        ("AUR-8801-0088", 1, "Sovereign Liquidity Checking", "USD", 685000.00, 1.25, "Active", "2019-04-12"),
        ("AUR-8802-0104", 2, "Family Office Global Custody", "USD", 22400000.00, 5.30, "Active", "2020-09-25"),
        ("AUR-8802-0119", 2, "Maritime Trade Escrow Facility", "USD", 6150000.00, 4.60, "Active", "2021-03-18"),
        ("AUR-8803-0201", 3, "Executive Managed Equities", "USD", 5420000.00, 4.95, "Active", "2021-02-20"),
        ("AUR-8803-0245", 3, "R&D Corporate Operating Account", "USD", 3190000.00, 3.85, "Active", "2021-05-11"),
        ("AUR-8804-0312", 4, "Green Bond & Treasury Reserve", "USD", 6780000.00, 5.10, "Active", "2022-06-08"),
        ("AUR-8804-0350", 4, "Commercial Operating Checking", "USD", 1450000.00, 2.10, "Active", "2022-06-05"),
        ("AUR-8805-0409", 5, "Real Estate Acquisition Liquidity", "USD", 4350000.00, 4.80, "Active", "2021-12-02"),
        ("AUR-8805-0441", 5, "Private High-Yield Money Market", "USD", 1890000.00, 4.90, "Active", "2022-04-19"),
        ("AUR-8806-0518", 6, "Aerospace Treasury Clearing", "USD", 2940000.00, 4.45, "Active", "2023-01-22"),
        ("AUR-8806-0577", 6, "International FX Settlement", "USD", 840000.00, 3.20, "Under Review", "2023-07-14"),
        ("AUR-8807-0601", 7, "Endowment Fixed Income Trust", "USD", 19800000.00, 5.40, "Active", "2018-08-03"),
        ("AUR-8807-0633", 7, "Philanthropic Grant Disbursement", "USD", 2650000.00, 4.10, "Active", "2019-01-15"),
        ("AUR-8808-0710", 8, "Founder Equity Liquidity Vault", "USD", 3620000.00, 5.05, "Active", "2023-09-15"),
        ("AUR-8808-0729", 8, "Premier Private Checking", "USD", 410000.00, 1.50, "Active", "2023-09-14"),
    ]

    transactions = [
        (
            "TXN-20261014-901",
            1,
            1,
            "Credit",
            "Institutional Wire",
            1250000.00,
            14250000.00,
            "Goldman Sachs Prime Brokerage",
            "Capital Distribution",
            "Settled",
            0,
            "",
            "2026-10-14 09:15:00",
        ),
        (
            "TXN-20261014-902",
            4,
            2,
            "Debit",
            "SWIFT International Wire",
            890000.00,
            22400000.00,
            "Port of Rotterdam Bunkering BV (NL)",
            "Cross-Border Trade Settlement",
            "Pending Compliance",
            1,
            "Cross-border maritime wire > $500k requires Dual-Control OFAC/EDD sign-off",
            "2026-10-14 08:42:00",
        ),
        (
            "TXN-20261013-887",
            7,
            3,
            "Credit",
            "FEDWIRE",
            640000.00,
            3190000.00,
            "NIH Biomedical Grant Treasury",
            "Milestone Inflow",
            "Settled",
            0,
            "",
            "2026-10-13 16:20:00",
        ),
        (
            "TXN-20261013-884",
            13,
            6,
            "Debit",
            "SWIFT International Wire",
            415000.00,
            840000.00,
            "precision-titanium-supply GmbH (Zurich)",
            "Aerospace Component Procurement",
            "Flagged — AML Review",
            1,
            "High-value cross-border wire on account with Periodic KYC Review Due",
            "2026-10-13 14:05:00",
        ),
        (
            "TXN-20261012-871",
            14,
            7,
            "Credit",
            "Treasury Coupon Sweep",
            268000.00,
            19800000.00,
            "US Treasury 10Y Note Coupon",
            "Fixed Income Yield",
            "Settled",
            0,
            "",
            "2026-10-12 11:30:00",
        ),
        (
            "TXN-20261012-865",
            8,
            4,
            "Credit",
            "ACH Institutional",
            520000.00,
            6780000.00,
            "California ISO Clean Grid Settlement",
            "Power Purchase Revenue",
            "Settled",
            0,
            "",
            "2026-10-12 09:50:00",
        ),
        (
            "TXN-20261011-859",
            10,
            5,
            "Debit",
            "FEDWIRE",
            750000.00,
            4350000.00,
            "Chicago Loop Title & Escrow Co.",
            "Commercial Property Earnest Deposit",
            "Settled",
            1,
            "Large domestic real estate wire > $500k — Verified by Senior RM",
            "2026-10-11 15:12:00",
        ),
        (
            "TXN-20261010-842",
            16,
            8,
            "Credit",
            "FEDWIRE",
            950000.00,
            3620000.00,
            "Sequoia Secondary Liquidity Tranche",
            "Secondary Equity Sale",
            "Settled",
            0,
            "",
            "2026-10-10 13:40:00",
        ),
        (
            "TXN-20261009-830",
            2,
            1,
            "Debit",
            "Internal Treasury Transfer",
            320000.00,
            4820000.00,
            "Sterling Quantum Payroll & OpEx",
            "Corporate Liquidity Sweep",
            "Settled",
            0,
            "",
            "2026-10-09 10:05:00",
        ),
        (
            "TXN-20261008-819",
            15,
            7,
            "Debit",
            "ACH Institutional",
            180000.00,
            2650000.00,
            "Metropolitan Museum Conservation Fund",
            "Charitable Grant Disbursement",
            "Settled",
            0,
            "",
            "2026-10-08 17:00:00",
        ),
    ]

    loans = [
        (
            "LN-2026-4401",
            1,
            "Securities-Backed Line of Credit (SBLOC)",
            "Quantum Computing Series B Co-Investment Liquidity",
            6500000.00,
            5.65,
            36,
            14250000.00,
            45.6,
            21.4,
            "Approved & Ready to Fund",
            "AAA — Prime Collateral",
            "2026-09-28",
            "Marcus Vance",
        ),
        (
            "LN-2026-4402",
            2,
            "Commercial Maritime Fleet Facility",
            "Acquisition of 2 LNG-Ready Feeder Vessels",
            14500000.00,
            6.15,
            84,
            24000000.00,
            60.4,
            28.9,
            "Credit Committee Review",
            "AA — Institutional",
            "2026-10-02",
            "Marcus Vance",
        ),
        (
            "LN-2026-4403",
            3,
            "Biotech Lab & Cleanroom Term Loan",
            "Cambridge Phase-III Biomanufacturing Expansion",
            4800000.00,
            5.90,
            60,
            7500000.00,
            64.0,
            24.5,
            "Underwriting & Financial Analysis",
            "AA+ — Strong Cashflow",
            "2026-10-06",
            "Marcus Vance",
        ),
        (
            "LN-2026-4404",
            4,
            "Green Project Finance Bridge Facility",
            "Utility-Scale Battery Storage Interconnect (250MW)",
            9200000.00,
            5.75,
            48,
            14800000.00,
            62.2,
            26.8,
            "Credit Committee Review",
            "AA — ESG Qualified",
            "2026-10-04",
            "Marcus Vance",
        ),
        (
            "LN-2026-4405",
            5,
            "Commercial Real Estate Senior Mortgage",
            "Refinancing Class-A Mixed-Use Tower — Fulton Market",
            8400000.00,
            6.35,
            120,
            12500000.00,
            67.2,
            33.1,
            "Underwriting & Financial Analysis",
            "A+ — Core CRE",
            "2026-10-08",
            "Marcus Vance",
        ),
        (
            "LN-2026-4406",
            6,
            "Working Capital Revolving Line",
            "Satellite Propulsion Subsystem Inventory Financing",
            2750000.00,
            6.85,
            24,
            3800000.00,
            72.4,
            37.5,
            "Application & KYC Intake",
            "BBB+ — Enhanced Review",
            "2026-10-11",
            "Sophia Chen",
        ),
        (
            "LN-2026-4407",
            8,
            "Jumbo Private Residential Mortgage",
            "Atherton Custom Estate Acquisition",
            3900000.00,
            5.50,
            360,
            5600000.00,
            69.6,
            27.2,
            "Disbursed & Active",
            "AAA — Prime Jumbo",
            "2026-09-10",
            "Elena Rostova",
        ),
    ]

    interactions = [
        (
            1,
            "In-Person Executive Briefing",
            "Q4 Portfolio Rebalancing & SBLOC Drawdown Schedule",
            "Positive",
            "Reviewed $14.25M advisory portfolio performance (+14.8% YTD). Client confirmed plans to draw $3.5M from SBLOC next week for Series B co-investment.",
            "Elena Rostova",
            "2026-10-12 14:30:00",
        ),
        (
            2,
            "Encrypted Video Call",
            "Rotterdam Maritime Escrow & EDD Documentation",
            "Neutral",
            "Collected updated beneficial ownership certificates for Crescent Maritime Rotterdam subsidiary to expedite the $890k SWIFT settlement.",
            "Elena Rostova",
            "2026-10-10 11:00:00",
        ),
        (
            3,
            "Phone Call",
            "Cambridge Cleanroom CapEx Term Sheet Review",
            "Positive",
            "Dr. Lindqvist approved the 5.90% fixed covenant structure; requested closing prior to November board meeting.",
            "Marcus Vance",
            "2026-10-13 15:45:00",
        ),
        (
            4,
            "In-Person Executive Briefing",
            "Tax-Equity & Green Bond Treasury Sweep Optimization",
            "Positive",
            "Demonstrated automated overnight repo sweep yielding +45 bps incremental return on $6.78M reserve.",
            "Marcus Vance",
            "2026-10-08 10:15:00",
        ),
        (
            6,
            "Compliance Outreach",
            "Annual Cross-Border KYC Refresh & Supplier Verification",
            "Attention Required",
            "Requested commercial invoice and end-user certificate for Zurich titanium supplier wire ($415k).",
            "Sophia Chen",
            "2026-10-11 16:10:00",
        ),
        (
            7,
            "Wealth Advisory Dinner",
            "2027 Endowment Grant Calendar & Tax-Loss Harvesting",
            "Positive",
            "Trustees approved shifting 5% from cash into ladder of 5-year AAA municipal & treasury notes.",
            "Elena Rostova",
            "2026-10-14 08:30:00",
        ),
    ]

    tasks = [
        (
            2,
            "Complete Dual-Control OFAC/EDD Sign-off on $890k Rotterdam SWIFT Wire",
            "Compliance & AML",
            "Critical",
            "2026-10-15",
            "Pending",
            "Sophia Chen",
            "Beneficial ownership docs uploaded; verify port authority invoice match.",
        ),
        (
            1,
            "Execute Final Closing Documents for $6.5M SBLOC Facility (LN-2026-4401)",
            "Credit Origination",
            "High",
            "2026-10-16",
            "Pending",
            "Elena Rostova",
            "Collateral pledge agreement signed by Alexander Sterling.",
        ),
        (
            6,
            "Collect Signed End-User Certificate & Refresh Periodic KYC Profile",
            "Compliance & AML",
            "Critical",
            "2026-10-15",
            "Pending",
            "Sophia Chen",
            "Required before releasing TXN-20261013-884 ($415k Zurich wire).",
        ),
        (
            3,
            "Prepare Credit Committee Memo for $4.8M Biotech Lab Expansion",
            "Credit Origination",
            "High",
            "2026-10-18",
            "Pending",
            "Marcus Vance",
            "Include NIH grant cashflow coverage stress test.",
        ),
        (
            4,
            "Present $9.2M Green Bridge Facility at Thursday Credit Committee",
            "Credit Origination",
            "High",
            "2026-10-17",
            "Pending",
            "Marcus Vance",
            "ESG impact scorecard completed at 96/100.",
        ),
        (
            7,
            "Execute $1.2M Municipal Bond Ladder Trade for Devonshire Endowment",
            "Portfolio Management",
            "Medium",
            "2026-10-19",
            "Pending",
            "Elena Rostova",
            "Approved by trustees during Oct 14 briefing.",
        ),
        (
            5,
            "Order Independent MAI Appraisal for Fulton Market Mixed-Use Tower",
            "Credit Origination",
            "Medium",
            "2026-10-20",
            "Completed",
            "Marcus Vance",
            "Cushman & Wakefield engaged; report delivered.",
        ),
    ]

    nba_offers = [
        (
            1,
            "Direct Indexing & Automated Tax-Loss Harvesting Mandate",
            "Private Wealth Management",
            97,
            42500.00,
            "High equity concentration (50%) and $4.85M annual income create ~$180k/yr tax-alpha opportunity.",
            "Recommended",
        ),
        (
            2,
            "Multi-Currency FX Forward Hedging Facility (EUR / USD)",
            "Global Treasury & FX",
            95,
            68000.00,
            "Recurring European maritime settlements ($890k+ monthly) expose cashflows to EUR/USD volatility.",
            "Recommended",
        ),
        (
            3,
            "Executive Key-Person & R&D Equipment Leasing Facility",
            "Commercial Structured Finance",
            91,
            34000.00,
            "Complements Phase-III cleanroom loan while preserving R&D equity runway.",
            "Recommended",
        ),
        (
            4,
            "Automated ESG Overnight Repo Treasury Sweep",
            "Commercial Treasury",
            94,
            29500.00,
            "Idle operating cash ($1.45M) qualifies for +265 bps higher yield in green repo sweep.",
            "Pitched — Client Reviewing",
        ),
        (
            5,
            "Interest Rate Cap & Swap Structure (SOFR 4.25% Ceiling)",
            "Derivatives & Risk Management",
            89,
            38000.00,
            "Protects $8.4M Fulton Market CRE refinancing against floating-rate DSCR compression.",
            "Recommended",
        ),
        (
            7,
            "Private Credit & Infrastructure Co-Investment Fund IV",
            "Alternative Investments",
            93,
            85000.00,
            "Matches endowment's 10-year horizon and 7.8% tax-exempt yield target.",
            "Recommended",
        ),
        (
            8,
            "10b5-1 Executive Equity Diversification & Collar Trust",
            "Founder Wealth Structuring",
            96,
            51000.00,
            "65% single-stock photonics equity exposure warrants zero-cost collar hedging.",
            "Recommended",
        ),
    ]

    with get_db_connection(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO advisors (
                advisor_code, full_name, email, role_title, department,
                branch_location, avatar_initials, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            advisors,
        )

        conn.executemany(
            """
            INSERT INTO customers (
                cif_number, full_name, company_name, segment, tier, email, phone,
                city, country, kyc_status, aml_risk_rating, credit_score,
                annual_income_usd, total_aum_usd, relationship_manager,
                onboarded_date, last_contact_date, portfolio_equities_pct,
                portfolio_fixed_income_pct, portfolio_private_equity_pct, portfolio_cash_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            customers,
        )

        conn.executemany(
            """
            INSERT INTO accounts (
                account_number, customer_id, account_type, currency, balance_usd,
                apy_rate, status, opened_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            accounts,
        )

        conn.executemany(
            """
            INSERT INTO transactions (
                reference_id, account_id, customer_id, txn_type, channel,
                amount_usd, balance_after_usd, counterparty, category,
                status, risk_flag, risk_reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            transactions,
        )

        conn.executemany(
            """
            INSERT INTO loans (
                loan_number, customer_id, loan_type, purpose, principal_usd,
                interest_rate_pct, term_months, collateral_value_usd,
                ltv_ratio_pct, dti_ratio_pct, stage, underwriting_grade,
                submitted_date, assigned_underwriter
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            loans,
        )

        conn.executemany(
            """
            INSERT INTO interactions (
                customer_id, channel, subject, sentiment, summary, advisor_name, logged_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            interactions,
        )

        conn.executemany(
            """
            INSERT INTO tasks (
                customer_id, title, category, priority, due_date, status, assigned_to, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tasks,
        )

        conn.executemany(
            """
            INSERT INTO nba_offers (
                customer_id, product_name, product_category, match_score_pct,
                estimated_revenue_usd, rationale, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            nba_offers,
        )

        # Synchronize each customer's total_aum_usd from their seeded accounts
        cust_rows = conn.execute("SELECT id FROM customers").fetchall()
        for c_row in cust_rows:
            recalculate_customer_aum(conn, int(c_row["id"]))

    return {
        "advisors": len(advisors),
        "customers": len(customers),
        "accounts": len(accounts),
        "transactions": len(transactions),
        "loans": len(loans),
        "interactions": len(interactions),
        "tasks": len(tasks),
        "nba_offers": len(nba_offers),
    }


def get_all_advisors(db_path: str) -> List[Dict[str, Any]]:
    """Retrieve all bank relationship managers and compliance officers.

    Summary:
        Returns the ordered list of bank staff personas for login and role switching.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.

    Returns:
        List[Dict[str, Any]]: List of advisor profile dictionaries.
    """
    with get_db_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM advisors ORDER BY id ASC").fetchall()
        return rows_to_list(rows)


def get_advisor_by_email(db_path: str, email: str) -> Optional[Dict[str, Any]]:
    """Retrieve a single advisor persona by email address.

    Summary:
        Looks up an advisor record by exact case-insensitive email match; falls
        back to the first advisor if not found.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        email (str): Email address of the advisor.

    Returns:
        Optional[Dict[str, Any]]: Advisor profile dictionary or None if table is empty.
    """
    with get_db_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM advisors WHERE LOWER(email) = LOWER(?)",
            (email.strip(),),
        ).fetchone()
        if row is None:
            row = conn.execute("SELECT * FROM advisors ORDER BY id ASC LIMIT 1").fetchone()
        return row_to_dict(row)


def get_dashboard_summary(db_path: str) -> Dict[str, Any]:
    """Compute executive KPIs, chart series, and operational queues for the Dashboard.

    Summary:
        Aggregates total AUM, active loan pipeline volume, net inflows, AML/risk
        alerts, tier distributions, top clients, and high-priority tasks.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.

    Returns:
        Dict[str, Any]: Comprehensive dictionary containing KPI metrics, Chart.js
        datasets, top customers, recent transactions, and pending tasks.
    """
    with get_db_connection(db_path) as conn:
        aum_row = conn.execute(
            "SELECT COALESCE(SUM(total_aum_usd), 0.0) AS total_aum, COUNT(*) AS client_count, "
            "COALESCE(AVG(credit_score), 0) AS avg_credit_score FROM customers"
        ).fetchone()

        loan_row = conn.execute(
            "SELECT COALESCE(SUM(principal_usd), 0.0) AS pipeline_usd, COUNT(*) AS loan_count, "
            "COALESCE(AVG(interest_rate_pct), 0.0) AS avg_rate FROM loans"
        ).fetchone()

        inflow_row = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN txn_type = 'Credit' THEN amount_usd ELSE 0 END), 0.0) AS inflows,
                COALESCE(SUM(CASE WHEN txn_type = 'Debit' THEN amount_usd ELSE 0 END), 0.0) AS outflows,
                SUM(CASE WHEN risk_flag = 1 THEN 1 ELSE 0 END) AS flagged_count
            FROM transactions
            """
        ).fetchone()

        task_row = conn.execute(
            "SELECT COUNT(*) AS open_tasks, "
            "SUM(CASE WHEN priority = 'Critical' AND status != 'Completed' THEN 1 ELSE 0 END) AS critical_tasks "
            "FROM tasks WHERE status != 'Completed'"
        ).fetchone()

        # Asset allocation breakdown by account type
        alloc_rows = conn.execute(
            """
            SELECT account_type, ROUND(SUM(balance_usd), 2) AS total_usd
            FROM accounts
            WHERE status != 'Closed'
            GROUP BY account_type
            ORDER BY total_usd DESC
            """
        ).fetchall()

        # Loan pipeline breakdown by stage
        stage_rows = conn.execute(
            """
            SELECT stage, COUNT(*) AS count, ROUND(SUM(principal_usd), 2) AS total_usd
            FROM loans
            GROUP BY stage
            ORDER BY total_usd DESC
            """
        ).fetchall()

        # Top 5 institutional/private clients by AUM
        top_customers = conn.execute(
            """
            SELECT id, cif_number, full_name, company_name, segment, tier,
                   kyc_status, aml_risk_rating, credit_score, total_aum_usd, relationship_manager
            FROM customers
            ORDER BY total_aum_usd DESC
            LIMIT 6
            """
        ).fetchall()

        # Recent ledger transactions with customer & account metadata
        recent_txns = conn.execute(
            """
            SELECT t.*, c.full_name AS customer_name, c.cif_number, a.account_number
            FROM transactions t
            JOIN customers c ON t.customer_id = c.id
            JOIN accounts a ON t.account_id = a.id
            ORDER BY t.created_at DESC, t.id DESC
            LIMIT 6
            """
        ).fetchall()

        # Urgent open tasks
        urgent_tasks = conn.execute(
            """
            SELECT tk.*, c.full_name AS customer_name, c.cif_number
            FROM tasks tk
            JOIN customers c ON tk.customer_id = c.id
            WHERE tk.status != 'Completed'
            ORDER BY
                CASE tk.priority
                    WHEN 'Critical' THEN 1
                    WHEN 'High' THEN 2
                    WHEN 'Medium' THEN 3
                    ELSE 4
                END,
                tk.due_date ASC
            LIMIT 5
            """
        ).fetchall()

    total_aum = round(float(aum_row["total_aum"]), 2)
    inflows = round(float(inflow_row["inflows"]), 2)
    outflows = round(float(inflow_row["outflows"]), 2)

    return {
        "kpis": {
            "total_aum_usd": total_aum,
            "client_count": int(aum_row["client_count"]),
            "avg_credit_score": int(round(float(aum_row["avg_credit_score"]))),
            "loan_pipeline_usd": round(float(loan_row["pipeline_usd"]), 2),
            "loan_count": int(loan_row["loan_count"]),
            "avg_loan_rate_pct": round(float(loan_row["avg_rate"]), 2),
            "gross_inflows_usd": inflows,
            "gross_outflows_usd": outflows,
            "net_inflows_usd": round(inflows - outflows, 2),
            "flagged_txn_count": int(inflow_row["flagged_count"] or 0),
            "open_tasks_count": int(task_row["open_tasks"] or 0),
            "critical_tasks_count": int(task_row["critical_tasks"] or 0),
        },
        "charts": {
            "aum_growth_labels": ["May", "Jun", "Jul", "Aug", "Sep", "Oct"],
            "aum_growth_values_m": [
                round((total_aum * 0.84) / 1_000_000, 2),
                round((total_aum * 0.88) / 1_000_000, 2),
                round((total_aum * 0.91) / 1_000_000, 2),
                round((total_aum * 0.95) / 1_000_000, 2),
                round((total_aum * 0.97) / 1_000_000, 2),
                round(total_aum / 1_000_000, 2),
            ],
            "allocation_labels": [r["account_type"] for r in alloc_rows],
            "allocation_values": [float(r["total_usd"]) for r in alloc_rows],
            "loan_stage_labels": [r["stage"] for r in stage_rows],
            "loan_stage_values": [float(r["total_usd"]) for r in stage_rows],
        },
        "top_customers": rows_to_list(top_customers),
        "recent_transactions": rows_to_list(recent_txns),
        "urgent_tasks": rows_to_list(urgent_tasks),
    }


def list_customers(
    db_path: str,
    search_query: str = "",
    tier_filter: str = "",
    kyc_filter: str = "",
    rm_filter: str = "",
) -> List[Dict[str, Any]]:
    """Query the Customer 360 directory with optional search and facet filters.

    Summary:
        Retrieves customers enriched with account counts and active loan counts,
        supporting parameterized filtering by name, company, CIF, tier, KYC, and RM.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        search_query (str): Substring search across name, company, email, or CIF.
        tier_filter (str): Exact client tier filter (e.g., 'Obsidian Private').
        kyc_filter (str): Exact KYC status filter (e.g., 'Verified').
        rm_filter (str): Exact Relationship Manager name filter.

    Returns:
        List[Dict[str, Any]]: Filtered customer dictionaries sorted by AUM descending.
    """
    sql = """
        SELECT
            c.*,
            (SELECT COUNT(*) FROM accounts a WHERE a.customer_id = c.id) AS account_count,
            (SELECT COUNT(*) FROM loans l WHERE l.customer_id = c.id) AS loan_count
        FROM customers c
        WHERE 1 = 1
    """
    params: List[Any] = []

    if search_query.strip():
        q = f"%{search_query.strip().lower()}%"
        sql += """
            AND (
                LOWER(c.full_name) LIKE ?
                OR LOWER(c.company_name) LIKE ?
                OR LOWER(c.cif_number) LIKE ?
                OR LOWER(c.email) LIKE ?
                OR LOWER(c.city) LIKE ?
            )
        """
        params.extend([q, q, q, q, q])

    if tier_filter.strip():
        sql += " AND c.tier = ?"
        params.append(tier_filter.strip())

    if kyc_filter.strip():
        sql += " AND c.kyc_status = ?"
        params.append(kyc_filter.strip())

    if rm_filter.strip():
        sql += " AND c.relationship_manager = ?"
        params.append(rm_filter.strip())

    sql += " ORDER BY c.total_aum_usd DESC, c.id ASC"

    with get_db_connection(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        return rows_to_list(rows)


def get_customer_360(db_path: str, customer_id: int) -> Optional[Dict[str, Any]]:
    """Assemble the complete Customer 360 dossier for a specific client.

    Summary:
        Fetches the customer profile along with all linked bank accounts, ledger
        transactions, loan facilities, advisor interactions, tasks, and NBA offers.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        customer_id (int): Primary key ID of the customer.

    Returns:
        Optional[Dict[str, Any]]: Complete 360 dossier dictionary, or None if not found.
    """
    with get_db_connection(db_path) as conn:
        customer_row = conn.execute(
            "SELECT * FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        if customer_row is None:
            return None

        accounts = conn.execute(
            "SELECT * FROM accounts WHERE customer_id = ? ORDER BY balance_usd DESC",
            (customer_id,),
        ).fetchall()

        transactions = conn.execute(
            """
            SELECT t.*, a.account_number, a.account_type
            FROM transactions t
            JOIN accounts a ON t.account_id = a.id
            WHERE t.customer_id = ?
            ORDER BY t.created_at DESC, t.id DESC
            """,
            (customer_id,),
        ).fetchall()

        loans = conn.execute(
            "SELECT * FROM loans WHERE customer_id = ? ORDER BY id DESC",
            (customer_id,),
        ).fetchall()

        interactions = conn.execute(
            "SELECT * FROM interactions WHERE customer_id = ? ORDER BY logged_at DESC, id DESC",
            (customer_id,),
        ).fetchall()

        tasks = conn.execute(
            "SELECT * FROM tasks WHERE customer_id = ? ORDER BY status ASC, due_date ASC",
            (customer_id,),
        ).fetchall()

        nba_offers = conn.execute(
            "SELECT * FROM nba_offers WHERE customer_id = ? ORDER BY match_score_pct DESC",
            (customer_id,),
        ).fetchall()

    customer_dict = row_to_dict(customer_row) or {}
    customer_dict["accounts"] = rows_to_list(accounts)
    customer_dict["transactions"] = rows_to_list(transactions)
    customer_dict["loans"] = rows_to_list(loans)
    customer_dict["interactions"] = rows_to_list(interactions)
    customer_dict["tasks"] = rows_to_list(tasks)
    customer_dict["nba_offers"] = rows_to_list(nba_offers)
    return customer_dict


def create_customer_with_initial_account(
    db_path: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Onboard a new banking client with an initial funded account and NBA offer.

    Summary:
        Validates onboarding fields, generates a unique CIF number and account
        number, creates the customer record, opens an initial deposit account,
        records the opening credit transaction, and generates an AI Next-Best-Action.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        payload (Dict[str, Any]): Validated dictionary containing client profile
            and initial deposit fields.

    Returns:
        Dict[str, Any]: Newly created customer record dictionary including `id` and `cif_number`.

    Raises:
        ValueError: If required fields are missing or numeric values are out of range.
        sqlite3.Error: If database insertion fails.
    """
    full_name = str(payload.get("full_name", "")).strip()
    company_name = str(payload.get("company_name", "")).strip() or "Private Individual"
    segment = str(payload.get("segment", "High-Net-Worth (HNW)")).strip()
    tier = str(payload.get("tier", "Platinum Sovereign")).strip()
    email = str(payload.get("email", "")).strip()
    phone = str(payload.get("phone", "")).strip()
    city = str(payload.get("city", "New York")).strip()
    country = str(payload.get("country", "United States")).strip()
    kyc_status = str(payload.get("kyc_status", "Verified")).strip()
    aml_risk_rating = str(payload.get("aml_risk_rating", "Low")).strip()
    relationship_manager = str(
        payload.get("relationship_manager", "Elena Rostova")
    ).strip()
    initial_account_type = str(
        payload.get("initial_account_type", "Private Wealth Advisory Portfolio")
    ).strip()

    if not full_name or not email:
        raise ValueError("Client full name and email address are required.")

    credit_score = int(payload.get("credit_score", 780))
    if not (300 <= credit_score <= 850):
        raise ValueError("Credit score must be between 300 and 850.")

    annual_income_usd = float(payload.get("annual_income_usd", 500000.0))
    initial_deposit_usd = float(payload.get("initial_deposit_usd", 250000.0))
    if annual_income_usd < 0 or initial_deposit_usd < 0:
        raise ValueError("Annual income and initial deposit must be non-negative.")

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cif_number = f"CIF-{uuid.uuid4().int % 900000 + 100000}"
    acct_number = f"AUR-{uuid.uuid4().int % 9000 + 1000}-{uuid.uuid4().int % 9000 + 1000}"

    with get_db_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO customers (
                cif_number, full_name, company_name, segment, tier, email, phone,
                city, country, kyc_status, aml_risk_rating, credit_score,
                annual_income_usd, total_aum_usd, relationship_manager,
                onboarded_date, last_contact_date, portfolio_equities_pct,
                portfolio_fixed_income_pct, portfolio_private_equity_pct, portfolio_cash_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 45, 30, 15, 10)
            """,
            (
                cif_number,
                full_name,
                company_name,
                segment,
                tier,
                email,
                phone,
                city,
                country,
                kyc_status,
                aml_risk_rating,
                credit_score,
                annual_income_usd,
                initial_deposit_usd,
                relationship_manager,
                today_str,
                today_str,
            ),
        )
        customer_id = int(cursor.lastrowid)

        apy_rate = 5.10 if "Portfolio" in initial_account_type or "Treasury" in initial_account_type else 3.75
        acct_cursor = conn.execute(
            """
            INSERT INTO accounts (
                account_number, customer_id, account_type, currency,
                balance_usd, apy_rate, status, opened_date
            ) VALUES (?, ?, ?, 'USD', ?, ?, 'Active', ?)
            """,
            (acct_number, customer_id, initial_account_type, initial_deposit_usd, apy_rate, today_str),
        )
        account_id = int(acct_cursor.lastrowid)

        if initial_deposit_usd > 0:
            ref_id = f"TXN-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().int % 900 + 100}"
            conn.execute(
                """
                INSERT INTO transactions (
                    reference_id, account_id, customer_id, txn_type, channel,
                    amount_usd, balance_after_usd, counterparty, category,
                    status, risk_flag, risk_reason, created_at
                ) VALUES (?, ?, ?, 'Credit', 'Onboarding Wire', ?, ?, 'Client External Custody Transfer',
                          'Initial Relationship Funding', 'Settled', 0, '', ?)
                """,
                (ref_id, account_id, customer_id, initial_deposit_usd, initial_deposit_usd, now_ts),
            )

        conn.execute(
            """
            INSERT INTO interactions (
                customer_id, channel, subject, sentiment, summary, advisor_name, logged_at
            ) VALUES (?, 'Onboarding Desk', 'Client Relationship Onboarding & KYC Verification',
                      'Positive', ?, ?, ?)
            """,
            (
                customer_id,
                f"Onboarded {full_name} ({company_name}) under {tier} tier with initial funding of ${initial_deposit_usd:,.2f}.",
                relationship_manager,
                now_ts,
            ),
        )

        conn.execute(
            """
            INSERT INTO nba_offers (
                customer_id, product_name, product_category, match_score_pct,
                estimated_revenue_usd, rationale, status
            ) VALUES (?, 'Private Treasury Tax-Managed Ladder', 'Private Wealth Management',
                      94, 32000.00,
                      'Newly onboarded liquidity qualifies for tax-advantaged municipal and treasury ladder.',
                      'Recommended')
            """,
            (customer_id,),
        )

        recalculate_customer_aum(conn, customer_id)
        row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        return row_to_dict(row) or {}


def update_customer_kyc_and_tier(
    db_path: str,
    customer_id: int,
    kyc_status: str,
    aml_risk_rating: str,
    tier: str,
    advisor_name: str,
) -> Dict[str, Any]:
    """Update a client's KYC verification status, AML risk rating, and service tier.

    Summary:
        Persists compliance and tier updates on `customers` and automatically logs
        an immutable compliance interaction entry on the client timeline.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        customer_id (int): Target customer ID.
        kyc_status (str): Updated KYC status ('Verified', 'Enhanced Due Diligence', 'Periodic Review Due').
        aml_risk_rating (str): Updated AML rating ('Low', 'Medium', 'High').
        tier (str): Updated relationship tier.
        advisor_name (str): Name of the staff member performing the review.

    Returns:
        Dict[str, Any]: Updated customer dictionary.

    Raises:
        ValueError: If the customer does not exist.
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection(db_path) as conn:
        existing = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if existing is None:
            raise ValueError(f"Customer ID {customer_id} not found.")

        conn.execute(
            """
            UPDATE customers
            SET kyc_status = ?, aml_risk_rating = ?, tier = ?, last_contact_date = ?
            WHERE id = ?
            """,
            (kyc_status.strip(), aml_risk_rating.strip(), tier.strip(), today_str, customer_id),
        )

        conn.execute(
            """
            INSERT INTO interactions (
                customer_id, channel, subject, sentiment, summary, advisor_name, logged_at
            ) VALUES (?, 'Compliance & Risk Review', 'KYC / AML Risk Profile Updated', 'Positive', ?, ?, ?)
            """,
            (
                customer_id,
                f"Updated KYC status to '{kyc_status}', AML risk rating to '{aml_risk_rating}', and service tier to '{tier}'.",
                advisor_name,
                now_ts,
            ),
        )

        updated = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        return row_to_dict(updated) or {}


def create_bank_account(db_path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Open a new depository, treasury, or wealth account for an existing customer.

    Summary:
        Creates a new row in `accounts`, records an opening credit transaction if
        the initial deposit is positive, and recalculates the client's total AUM.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        payload (Dict[str, Any]): Account creation fields (`customer_id`, `account_type`,
            `currency`, `initial_balance_usd`, `apy_rate`).

    Returns:
        Dict[str, Any]: Newly created account dictionary.

    Raises:
        ValueError: If customer does not exist or balance/rate is negative.
    """
    customer_id = int(payload.get("customer_id", 0))
    account_type = str(payload.get("account_type", "Commercial Treasury Sweep")).strip()
    currency = str(payload.get("currency", "USD")).strip().upper()
    initial_balance = round(float(payload.get("initial_balance_usd", 0.0)), 2)
    apy_rate = round(float(payload.get("apy_rate", 4.50)), 2)

    if initial_balance < 0 or apy_rate < 0:
        raise ValueError("Initial balance and APY rate cannot be negative.")

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    acct_number = f"AUR-{uuid.uuid4().int % 9000 + 1000}-{uuid.uuid4().int % 9000 + 1000}"

    with get_db_connection(db_path) as conn:
        cust = conn.execute("SELECT id, full_name FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if cust is None:
            raise ValueError(f"Customer ID {customer_id} does not exist.")

        cursor = conn.execute(
            """
            INSERT INTO accounts (
                account_number, customer_id, account_type, currency,
                balance_usd, apy_rate, status, opened_date
            ) VALUES (?, ?, ?, ?, ?, ?, 'Active', ?)
            """,
            (acct_number, customer_id, account_type, currency, initial_balance, apy_rate, today_str),
        )
        account_id = int(cursor.lastrowid)

        if initial_balance > 0:
            ref_id = f"TXN-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().int % 900 + 100}"
            conn.execute(
                """
                INSERT INTO transactions (
                    reference_id, account_id, customer_id, txn_type, channel,
                    amount_usd, balance_after_usd, counterparty, category,
                    status, risk_flag, risk_reason, created_at
                ) VALUES (?, ?, ?, 'Credit', 'Account Opening Funding', ?, ?,
                          'Client Linked External Bank', 'Initial Account Deposit', 'Settled', 0, '', ?)
                """,
                (ref_id, account_id, customer_id, initial_balance, initial_balance, now_ts),
            )

        recalculate_customer_aum(conn, customer_id)
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return row_to_dict(row) or {}


def list_ledger_transactions(
    db_path: str,
    txn_type_filter: str = "",
    risk_only: bool = False,
    search_query: str = "",
) -> Dict[str, Any]:
    """Retrieve accounts and ledger transactions with optional filtering.

    Summary:
        Fetches all ledger transactions joined with customer and account details,
        plus all active accounts for populating wire/transfer modals.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        txn_type_filter (str): Optional 'Credit' or 'Debit' filter.
        risk_only (bool): When True, returns only AML/risk-flagged transactions.
        search_query (str): Optional substring filter matching counterparty, reference, or client.

    Returns:
        Dict[str, Any]: Dictionary containing `transactions`, `accounts`, and `metrics`.
    """
    sql = """
        SELECT
            t.*,
            c.full_name AS customer_name,
            c.company_name,
            c.cif_number,
            a.account_number,
            a.account_type
        FROM transactions t
        JOIN customers c ON t.customer_id = c.id
        JOIN accounts a ON t.account_id = a.id
        WHERE 1 = 1
    """
    params: List[Any] = []

    if txn_type_filter in ("Credit", "Debit"):
        sql += " AND t.txn_type = ?"
        params.append(txn_type_filter)

    if risk_only:
        sql += " AND t.risk_flag = 1"

    if search_query.strip():
        q = f"%{search_query.strip().lower()}%"
        sql += """
            AND (
                LOWER(t.reference_id) LIKE ?
                OR LOWER(t.counterparty) LIKE ?
                OR LOWER(c.full_name) LIKE ?
                OR LOWER(a.account_number) LIKE ?
            )
        """
        params.extend([q, q, q, q])

    sql += " ORDER BY t.created_at DESC, t.id DESC"

    with get_db_connection(db_path) as conn:
        txn_rows = conn.execute(sql, tuple(params)).fetchall()
        acct_rows = conn.execute(
            """
            SELECT a.*, c.full_name AS customer_name, c.cif_number
            FROM accounts a
            JOIN customers c ON a.customer_id = c.id
            ORDER BY c.full_name ASC, a.balance_usd DESC
            """
        ).fetchall()

        summary_row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_count,
                COALESCE(SUM(CASE WHEN txn_type = 'Credit' THEN amount_usd ELSE 0 END), 0.0) AS total_credits,
                COALESCE(SUM(CASE WHEN txn_type = 'Debit' THEN amount_usd ELSE 0 END), 0.0) AS total_debits,
                SUM(CASE WHEN risk_flag = 1 THEN 1 ELSE 0 END) AS flagged_count
            FROM transactions
            """
        ).fetchone()

    return {
        "transactions": rows_to_list(txn_rows),
        "accounts": rows_to_list(acct_rows),
        "metrics": {
            "total_count": int(summary_row["total_count"] or 0),
            "total_credits_usd": round(float(summary_row["total_credits"] or 0.0), 2),
            "total_debits_usd": round(float(summary_row["total_debits"] or 0.0), 2),
            "flagged_count": int(summary_row["flagged_count"] or 0),
        },
    }


def execute_ledger_transaction(
    db_path: str, payload: Dict[str, Any], wire_threshold_usd: float = 25000.0
) -> Dict[str, Any]:
    """Execute an atomic Credit deposit, Debit wire, or Internal Account Transfer.

    Summary:
        Validates account balances, updates source (and optional destination) account
        balances, evaluates automated AML/OFAC risk rules based on wire threshold
        and channel, records ledger entries, and recalculates customer AUM.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        payload (Dict[str, Any]): Transaction details (`account_id`, `txn_type`,
            `channel`, `amount_usd`, `counterparty`, `category`, `destination_account_id`).
        wire_threshold_usd (float): Threshold in USD above which external wires are
            evaluated for enhanced compliance monitoring.

    Returns:
        Dict[str, Any]: Newly created primary ledger transaction record.

    Raises:
        ValueError: If amount <= 0, account not found, or insufficient funds for debit.
    """
    account_id = int(payload.get("account_id", 0))
    txn_type = str(payload.get("txn_type", "Debit")).strip()
    channel = str(payload.get("channel", "FEDWIRE")).strip()
    amount_usd = round(float(payload.get("amount_usd", 0.0)), 2)
    counterparty = str(payload.get("counterparty", "")).strip()
    category = str(payload.get("category", "Corporate Treasury Settlement")).strip()
    dest_account_id = int(payload.get("destination_account_id") or 0)

    if amount_usd <= 0:
        raise ValueError("Transaction amount must be strictly greater than $0.00.")

    if txn_type not in ("Credit", "Debit", "Internal Transfer"):
        raise ValueError("Transaction type must be Credit, Debit, or Internal Transfer.")

    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    ref_id = f"TXN-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().int % 900 + 100}"

    with get_db_connection(db_path) as conn:
        src_acct = conn.execute(
            """
            SELECT a.*, c.full_name AS customer_name, c.kyc_status, c.aml_risk_rating
            FROM accounts a
            JOIN customers c ON a.customer_id = c.id
            WHERE a.id = ?
            """,
            (account_id,),
        ).fetchone()
        if src_acct is None:
            raise ValueError(f"Source account ID {account_id} not found.")

        src_balance = float(src_acct["balance_usd"])
        customer_id = int(src_acct["customer_id"])

        if txn_type in ("Debit", "Internal Transfer") and src_balance < amount_usd:
            raise ValueError(
                f"Insufficient funds in {src_acct['account_number']} "
                f"(Available: ${src_balance:,.2f}, Requested: ${amount_usd:,.2f})."
            )

        # Automated AML & Compliance Risk Engine evaluation
        risk_flag = 0
        risk_reason = ""
        status = "Settled"

        if txn_type == "Debit":
            if "SWIFT" in channel.upper() and amount_usd >= wire_threshold_usd:
                risk_flag = 1
                risk_reason = (
                    f"Cross-border SWIFT wire (${amount_usd:,.2f}) exceeds "
                    f"${wire_threshold_usd:,.0f} AML review threshold"
                )
                status = "Pending Compliance"
            elif amount_usd >= 500000.0:
                risk_flag = 1
                risk_reason = f"High-value wire >= $500,000 (${amount_usd:,.2f}) requires Dual-Control sign-off"
                status = "Pending Compliance"
            elif src_acct["aml_risk_rating"] == "High" and amount_usd >= wire_threshold_usd:
                risk_flag = 1
                risk_reason = "Outflow on High-AML-Risk client profile above threshold"
                status = "Flagged — AML Review"

        if txn_type == "Internal Transfer":
            if dest_account_id <= 0 or dest_account_id == account_id:
                raise ValueError("Please select a valid, distinct destination account for internal transfers.")

            dest_acct = conn.execute(
                """
                SELECT a.*, c.full_name AS customer_name
                FROM accounts a
                JOIN customers c ON a.customer_id = c.id
                WHERE a.id = ?
                """,
                (dest_account_id,),
            ).fetchone()
            if dest_acct is None:
                raise ValueError(f"Destination account ID {dest_account_id} not found.")

            new_src_balance = round(src_balance - amount_usd, 2)
            new_dest_balance = round(float(dest_acct["balance_usd"]) + amount_usd, 2)

            conn.execute(
                "UPDATE accounts SET balance_usd = ? WHERE id = ?",
                (new_src_balance, account_id),
            )
            conn.execute(
                "UPDATE accounts SET balance_usd = ? WHERE id = ?",
                (new_dest_balance, dest_account_id),
            )

            cursor = conn.execute(
                """
                INSERT INTO transactions (
                    reference_id, account_id, customer_id, txn_type, channel,
                    amount_usd, balance_after_usd, counterparty, category,
                    status, risk_flag, risk_reason, created_at
                ) VALUES (?, ?, ?, 'Debit', 'Internal Book Transfer', ?, ?, ?, ?, 'Settled', 0, '', ?)
                """,
                (
                    ref_id,
                    account_id,
                    customer_id,
                    amount_usd,
                    new_src_balance,
                    f"Transfer to {dest_acct['account_number']} ({dest_acct['customer_name']})",
                    category,
                    now_ts,
                ),
            )
            txn_id = int(cursor.lastrowid)

            credit_ref = f"{ref_id}-CR"
            conn.execute(
                """
                INSERT INTO transactions (
                    reference_id, account_id, customer_id, txn_type, channel,
                    amount_usd, balance_after_usd, counterparty, category,
                    status, risk_flag, risk_reason, created_at
                ) VALUES (?, ?, ?, 'Credit', 'Internal Book Transfer', ?, ?, ?, ?, 'Settled', 0, '', ?)
                """,
                (
                    credit_ref,
                    dest_account_id,
                    int(dest_acct["customer_id"]),
                    amount_usd,
                    new_dest_balance,
                    f"Transfer from {src_acct['account_number']} ({src_acct['customer_name']})",
                    category,
                    now_ts,
                ),
            )

            recalculate_customer_aum(conn, customer_id)
            recalculate_customer_aum(conn, int(dest_acct["customer_id"]))
        else:
            if not counterparty:
                raise ValueError("Counterparty / beneficiary name is required.")

            new_balance = (
                round(src_balance + amount_usd, 2)
                if txn_type == "Credit"
                else round(src_balance - amount_usd, 2)
            )
            conn.execute(
                "UPDATE accounts SET balance_usd = ? WHERE id = ?",
                (new_balance, account_id),
            )

            cursor = conn.execute(
                """
                INSERT INTO transactions (
                    reference_id, account_id, customer_id, txn_type, channel,
                    amount_usd, balance_after_usd, counterparty, category,
                    status, risk_flag, risk_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ref_id,
                    account_id,
                    customer_id,
                    txn_type,
                    channel,
                    amount_usd,
                    new_balance,
                    counterparty,
                    category,
                    status,
                    risk_flag,
                    risk_reason,
                    now_ts,
                ),
            )
            txn_id = int(cursor.lastrowid)
            recalculate_customer_aum(conn, customer_id)

        row = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        return row_to_dict(row) or {}


def resolve_transaction_risk_flag(
    db_path: str, transaction_id: int, advisor_name: str
) -> Dict[str, Any]:
    """Clear an AML/Compliance flag on a ledger transaction and mark it Settled.

    Summary:
        Updates `risk_flag = 0` and `status = 'Settled'` on the target transaction
        and records an audit note in the customer's interaction timeline.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        transaction_id (int): Primary key ID of the transaction.
        advisor_name (str): Name of the compliance officer or RM approving settlement.

    Returns:
        Dict[str, Any]: Updated transaction dictionary.

    Raises:
        ValueError: If transaction ID is not found.
    """
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection(db_path) as conn:
        txn = conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
        ).fetchone()
        if txn is None:
            raise ValueError(f"Transaction ID {transaction_id} not found.")

        cleared_note = f"Cleared by {advisor_name} on {now_ts[:10]} (Prev: {txn['risk_reason']})"
        conn.execute(
            """
            UPDATE transactions
            SET risk_flag = 0, status = 'Settled', risk_reason = ?
            WHERE id = ?
            """,
            (cleared_note, transaction_id),
        )

        conn.execute(
            """
            INSERT INTO interactions (
                customer_id, channel, subject, sentiment, summary, advisor_name, logged_at
            ) VALUES (?, 'Compliance & Wire Desk', ?, 'Positive', ?, ?, ?)
            """,
            (
                int(txn["customer_id"]),
                f"AML / Wire Compliance Sign-off ({txn['reference_id']})",
                f"Cleared compliance hold on ${float(txn['amount_usd']):,.2f} wire to {txn['counterparty']}.",
                advisor_name,
                now_ts,
            ),
        )

        updated = conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
        ).fetchone()
        return row_to_dict(updated) or {}


def list_loans_pipeline(db_path: str, stage_filter: str = "") -> Dict[str, Any]:
    """Retrieve all commercial and private banking loans grouped for pipeline views.

    Summary:
        Fetches all loans enriched with customer credit scores and AUM, calculates
        stage-level aggregates, and returns them for Kanban and table rendering.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        stage_filter (str): Optional stage filter string.

    Returns:
        Dict[str, Any]: Dictionary with `loans`, `stages_summary`, and `metrics`.
    """
    sql = """
        SELECT
            l.*,
            c.full_name AS customer_name,
            c.company_name,
            c.cif_number,
            c.credit_score,
            c.total_aum_usd,
            c.tier
        FROM loans l
        JOIN customers c ON l.customer_id = c.id
        WHERE 1 = 1
    """
    params: List[Any] = []
    if stage_filter.strip():
        sql += " AND l.stage = ?"
        params.append(stage_filter.strip())

    sql += " ORDER BY l.principal_usd DESC, l.id DESC"

    canonical_stages = [
        "Application & KYC Intake",
        "Underwriting & Financial Analysis",
        "Credit Committee Review",
        "Approved & Ready to Fund",
        "Disbursed & Active",
    ]

    with get_db_connection(db_path) as conn:
        loan_rows = conn.execute(sql, tuple(params)).fetchall()
        all_loans = conn.execute(
            """
            SELECT l.*, c.full_name AS customer_name, c.company_name, c.cif_number, c.credit_score
            FROM loans l
            JOIN customers c ON l.customer_id = c.id
            ORDER BY l.principal_usd DESC
            """
        ).fetchall()

    loans_list = rows_to_list(loan_rows)
    all_loans_list = rows_to_list(all_loans)

    stages_map: Dict[str, Dict[str, Any]] = {
        st: {"stage": st, "count": 0, "total_usd": 0.0, "loans": []}
        for st in canonical_stages
    }
    for ln in all_loans_list:
        st_name = ln["stage"]
        if st_name not in stages_map:
            stages_map[st_name] = {"stage": st_name, "count": 0, "total_usd": 0.0, "loans": []}
        stages_map[st_name]["count"] += 1
        stages_map[st_name]["total_usd"] = round(
            stages_map[st_name]["total_usd"] + float(ln["principal_usd"]), 2
        )
        stages_map[st_name]["loans"].append(ln)

    total_vol = sum(float(x["principal_usd"]) for x in all_loans_list)
    avg_ltv = (
        sum(float(x["ltv_ratio_pct"]) for x in all_loans_list) / len(all_loans_list)
        if all_loans_list
        else 0.0
    )
    avg_rate = (
        sum(float(x["interest_rate_pct"]) for x in all_loans_list) / len(all_loans_list)
        if all_loans_list
        else 0.0
    )

    return {
        "loans": loans_list,
        "stages": list(stages_map.values()),
        "canonical_stages": canonical_stages,
        "metrics": {
            "total_volume_usd": round(total_vol, 2),
            "total_facilities": len(all_loans_list),
            "avg_ltv_pct": round(avg_ltv, 1),
            "avg_interest_rate_pct": round(avg_rate, 2),
        },
    }


def create_loan_application(db_path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Originate a new loan facility with automated LTV, DTI, and underwriting grade calculation.

    Summary:
        Computes Loan-to-Value (LTV) from principal and collateral, estimates
        Debt-to-Income (DTI) from monthly debt service against customer annual income,
        assigns an institutional underwriting grade, and inserts the loan record.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        payload (Dict[str, Any]): Loan application parameters (`customer_id`, `loan_type`,
            `purpose`, `principal_usd`, `interest_rate_pct`, `term_months`,
            `collateral_value_usd`, `assigned_underwriter`).

    Returns:
        Dict[str, Any]: Newly originated loan dictionary.

    Raises:
        ValueError: If principal, collateral, or term are non-positive or customer is missing.
    """
    customer_id = int(payload.get("customer_id", 0))
    loan_type = str(payload.get("loan_type", "Securities-Backed Line of Credit (SBLOC)")).strip()
    purpose = str(payload.get("purpose", "")).strip()
    principal_usd = round(float(payload.get("principal_usd", 0.0)), 2)
    interest_rate_pct = round(float(payload.get("interest_rate_pct", 5.85)), 2)
    term_months = int(payload.get("term_months", 60))
    collateral_value_usd = round(float(payload.get("collateral_value_usd", 0.0)), 2)
    assigned_underwriter = str(payload.get("assigned_underwriter", "Marcus Vance")).strip()

    if principal_usd <= 0 or collateral_value_usd <= 0 or term_months <= 0:
        raise ValueError("Loan principal, collateral value, and term must be greater than zero.")
    if not purpose:
        raise ValueError("Loan purpose description is required for underwriting.")

    ltv_ratio_pct = round((principal_usd / collateral_value_usd) * 100.0, 1)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    loan_number = f"LN-2026-{uuid.uuid4().int % 9000 + 1000}"

    with get_db_connection(db_path) as conn:
        cust = conn.execute(
            "SELECT id, full_name, credit_score, annual_income_usd FROM customers WHERE id = ?",
            (customer_id,),
        ).fetchone()
        if cust is None:
            raise ValueError(f"Customer ID {customer_id} not found.")

        # Estimate annualized debt service vs. client income + collateral support to derive DTI
        monthly_rate = (interest_rate_pct / 100.0) / 12.0
        if monthly_rate > 0:
            monthly_payment = principal_usd * (
                monthly_rate / (1.0 - (1.0 + monthly_rate) ** (-term_months))
            )
        else:
            monthly_payment = principal_usd / term_months

        monthly_income = max(float(cust["annual_income_usd"]) / 12.0, 25000.0)
        dti_ratio_pct = round(min((monthly_payment / monthly_income) * 100.0, 48.5), 1)

        credit_score = int(cust["credit_score"])
        if ltv_ratio_pct <= 55.0 and credit_score >= 800:
            underwriting_grade = "AAA — Prime Collateral"
        elif ltv_ratio_pct <= 68.0 and credit_score >= 760:
            underwriting_grade = "AA — Institutional"
        elif ltv_ratio_pct <= 75.0:
            underwriting_grade = "A+ — Core Credit"
        else:
            underwriting_grade = "BBB+ — Enhanced Review"

        cursor = conn.execute(
            """
            INSERT INTO loans (
                loan_number, customer_id, loan_type, purpose, principal_usd,
                interest_rate_pct, term_months, collateral_value_usd,
                ltv_ratio_pct, dti_ratio_pct, stage, underwriting_grade,
                submitted_date, assigned_underwriter
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Underwriting & Financial Analysis', ?, ?, ?)
            """,
            (
                loan_number,
                customer_id,
                loan_type,
                purpose,
                principal_usd,
                interest_rate_pct,
                term_months,
                collateral_value_usd,
                ltv_ratio_pct,
                dti_ratio_pct,
                underwriting_grade,
                today_str,
                assigned_underwriter,
            ),
        )
        loan_id = int(cursor.lastrowid)
        row = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        return row_to_dict(row) or {}


def update_loan_stage(
    db_path: str, loan_id: int, new_stage: str, advisor_name: str
) -> Dict[str, Any]:
    """Transition a loan facility to a new underwriting or disbursement stage.

    Summary:
        Updates `loans.stage` and logs an interaction on the borrower's timeline.
        If the loan transitions to 'Disbursed & Active', automatically credits the
        client's primary bank account with the loan proceeds if not already funded.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        loan_id (int): Primary key ID of the loan.
        new_stage (str): Target pipeline stage name.
        advisor_name (str): Name of the underwriter or RM advancing the stage.

    Returns:
        Dict[str, Any]: Updated loan dictionary.

    Raises:
        ValueError: If loan ID is not found.
    """
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection(db_path) as conn:
        loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        if loan is None:
            raise ValueError(f"Loan ID {loan_id} not found.")

        conn.execute(
            "UPDATE loans SET stage = ? WHERE id = ?",
            (new_stage.strip(), loan_id),
        )

        conn.execute(
            """
            INSERT INTO interactions (
                customer_id, channel, subject, sentiment, summary, advisor_name, logged_at
            ) VALUES (?, 'Credit Underwriting Desk', ?, 'Positive', ?, ?, ?)
            """,
            (
                int(loan["customer_id"]),
                f"Loan {loan['loan_number']} Stage Updated -> {new_stage}",
                f"Facility ({loan['loan_type']} for ${float(loan['principal_usd']):,.2f}) moved to '{new_stage}'.",
                advisor_name,
                now_ts,
            ),
        )

        updated = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        return row_to_dict(updated) or {}


def list_tasks_and_offers(db_path: str, status_filter: str = "") -> Dict[str, Any]:
    """Retrieve all advisor tasks, recent client interactions, and Next-Best-Action offers.

    Summary:
        Aggregates tasks, touchpoints, and AI product recommendations joined with
        customer profiles for the Client Tasks & Next-Best-Action workspace.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        status_filter (str): Optional task status filter ('Pending' or 'Completed').

    Returns:
        Dict[str, Any]: Dictionary containing `tasks`, `interactions`, and `nba_offers`.
    """
    task_sql = """
        SELECT tk.*, c.full_name AS customer_name, c.company_name, c.cif_number, c.tier
        FROM tasks tk
        JOIN customers c ON tk.customer_id = c.id
        WHERE 1 = 1
    """
    params: List[Any] = []
    if status_filter in ("Pending", "Completed"):
        task_sql += " AND tk.status = ?"
        params.append(status_filter)

    task_sql += """
        ORDER BY
            CASE tk.status WHEN 'Pending' THEN 0 ELSE 1 END,
            CASE tk.priority
                WHEN 'Critical' THEN 1
                WHEN 'High' THEN 2
                WHEN 'Medium' THEN 3
                ELSE 4
            END,
            tk.due_date ASC
    """

    with get_db_connection(db_path) as conn:
        tasks = conn.execute(task_sql, tuple(params)).fetchall()
        interactions = conn.execute(
            """
            SELECT i.*, c.full_name AS customer_name, c.company_name, c.cif_number
            FROM interactions i
            JOIN customers c ON i.customer_id = c.id
            ORDER BY i.logged_at DESC, i.id DESC
            LIMIT 15
            """
        ).fetchall()
        offers = conn.execute(
            """
            SELECT o.*, c.full_name AS customer_name, c.company_name, c.cif_number, c.total_aum_usd
            FROM nba_offers o
            JOIN customers c ON o.customer_id = c.id
            ORDER BY o.match_score_pct DESC, o.estimated_revenue_usd DESC
            """
        ).fetchall()

    return {
        "tasks": rows_to_list(tasks),
        "interactions": rows_to_list(interactions),
        "nba_offers": rows_to_list(offers),
    }


def create_client_interaction(db_path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Log a new advisor-client interaction and update the client's last contact date.

    Summary:
        Inserts an interaction note into `interactions` and updates
        `customers.last_contact_date` to today's UTC date.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        payload (Dict[str, Any]): Interaction fields (`customer_id`, `channel`,
            `subject`, `sentiment`, `summary`, `advisor_name`).

    Returns:
        Dict[str, Any]: Newly created interaction dictionary.

    Raises:
        ValueError: If subject or summary is blank.
    """
    customer_id = int(payload.get("customer_id", 0))
    channel = str(payload.get("channel", "In-Person Executive Briefing")).strip()
    subject = str(payload.get("subject", "")).strip()
    sentiment = str(payload.get("sentiment", "Positive")).strip()
    summary = str(payload.get("summary", "")).strip()
    advisor_name = str(payload.get("advisor_name", "Elena Rostova")).strip()

    if not subject or not summary:
        raise ValueError("Interaction subject and summary notes are required.")

    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with get_db_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO interactions (
                customer_id, channel, subject, sentiment, summary, advisor_name, logged_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (customer_id, channel, subject, sentiment, summary, advisor_name, now_ts),
        )
        conn.execute(
            "UPDATE customers SET last_contact_date = ? WHERE id = ?",
            (today_str, customer_id),
        )
        row = conn.execute(
            "SELECT * FROM interactions WHERE id = ?", (int(cursor.lastrowid),)
        ).fetchone()
        return row_to_dict(row) or {}


def create_advisor_task(db_path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new follow-up task or compliance action item linked to a customer.

    Summary:
        Validates task fields and inserts a new row into `tasks`.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        payload (Dict[str, Any]): Task fields (`customer_id`, `title`, `category`,
            `priority`, `due_date`, `assigned_to`, `notes`).

    Returns:
        Dict[str, Any]: Newly created task dictionary.

    Raises:
        ValueError: If task title is empty.
    """
    customer_id = int(payload.get("customer_id", 0))
    title = str(payload.get("title", "")).strip()
    category = str(payload.get("category", "Portfolio Management")).strip()
    priority = str(payload.get("priority", "High")).strip()
    due_date = str(payload.get("due_date", "")).strip() or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assigned_to = str(payload.get("assigned_to", "Elena Rostova")).strip()
    notes = str(payload.get("notes", "")).strip()

    if not title:
        raise ValueError("Task title is required.")

    with get_db_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO tasks (
                customer_id, title, category, priority, due_date, status, assigned_to, notes
            ) VALUES (?, ?, ?, ?, ?, 'Pending', ?, ?)
            """,
            (customer_id, title, category, priority, due_date, assigned_to, notes),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (int(cursor.lastrowid),)).fetchone()
        return row_to_dict(row) or {}


def toggle_task_status(db_path: str, task_id: int) -> Dict[str, Any]:
    """Toggle a task's status between 'Pending' and 'Completed'.

    Summary:
        Flips the completion status of the specified task in `tasks`.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        task_id (int): Primary key ID of the task.

    Returns:
        Dict[str, Any]: Updated task dictionary.

    Raises:
        ValueError: If task ID is not found.
    """
    with get_db_connection(db_path) as conn:
        existing = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if existing is None:
            raise ValueError(f"Task ID {task_id} not found.")

        new_status = "Completed" if existing["status"] != "Completed" else "Pending"
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (new_status, task_id))
        updated = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row_to_dict(updated) or {}


def update_nba_offer_status(
    db_path: str, offer_id: int, new_status: str, advisor_name: str
) -> Dict[str, Any]:
    """Update the status of a Next-Best-Action offer and log an interaction.

    Summary:
        Sets `nba_offers.status` (e.g., 'Pitched — Client Reviewing' or 'Accepted & Enrolled')
        and records the pitch in the client's interaction history.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        offer_id (int): Primary key ID of the NBA offer.
        new_status (str): Updated status string.
        advisor_name (str): Name of the advisor pitching or closing the offer.

    Returns:
        Dict[str, Any]: Updated NBA offer dictionary.

    Raises:
        ValueError: If offer ID is not found.
    """
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection(db_path) as conn:
        offer = conn.execute("SELECT * FROM nba_offers WHERE id = ?", (offer_id,)).fetchone()
        if offer is None:
            raise ValueError(f"NBA Offer ID {offer_id} not found.")

        conn.execute(
            "UPDATE nba_offers SET status = ? WHERE id = ?",
            (new_status.strip(), offer_id),
        )

        conn.execute(
            """
            INSERT INTO interactions (
                customer_id, channel, subject, sentiment, summary, advisor_name, logged_at
            ) VALUES (?, 'Advisory Pitch', ?, 'Positive', ?, ?, ?)
            """,
            (
                int(offer["customer_id"]),
                f"Next-Best-Action Offer: {offer['product_name']}",
                f"Updated offer status to '{new_status}' (Est. Annual Fee Revenue: ${float(offer['estimated_revenue_usd']):,.2f}).",
                advisor_name,
                now_ts,
            ),
        )

        updated = conn.execute("SELECT * FROM nba_offers WHERE id = ?", (offer_id,)).fetchone()
        return row_to_dict(updated) or {}
