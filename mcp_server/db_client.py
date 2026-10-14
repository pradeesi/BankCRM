# ==============================================================================
# Purpose: Self-contained SQLite database engine and banking repository for the
#   Aurelia Bank CRM MCP Server.
# Architecture/Context: Connects to the shared `../bank_crm.db` SQLite database
#   in WAL mode so external AI agent actions immediately reflect in the Flask
#   CRM UI, while including full DDL and seed fallback for standalone container
#   deployments on Cloud Run or Google Kubernetes Engine (GKE).
# Dependencies/Side Effects: Uses Python's standard `sqlite3` library and mutates
#   the SQLite file specified by `DATABASE_PATH`.
# Author: Pradeep Singh
# License: Apache-2.0
# ==============================================================================

import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger("aurelia_mcp.db_client")

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
def get_connection(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """Open a transactional SQLite connection configured with WAL mode and FK constraints.

    Summary:
        Provides concurrent read/write safety between the Flask CRM web server
        and the MCP Server via SQLite Write-Ahead Logging (`WAL`).

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.

    Yields:
        sqlite3.Connection: Active SQLite connection with `sqlite3.Row` factory.

    Raises:
        sqlite3.Error: Rolls back and re-raises on any SQL execution failure.
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
        logger.error("SQLite error on %s: %s", db_path, exc)
        raise
    finally:
        conn.close()


def ensure_database_ready(db_path: str, auto_seed: bool = True) -> None:
    """Ensure all CRM tables exist and seed fallback records if deployed standalone.

    Summary:
        Creates tables if missing and populates baseline institutional records
        when `customers` is empty and `auto_seed` is enabled.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        auto_seed (bool): Whether to seed sample records if database is empty.

    Returns:
        None
    """
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        row = conn.execute("SELECT COUNT(*) AS cnt FROM customers").fetchone()
        count = int(row["cnt"]) if row else 0

    if count == 0 and auto_seed:
        logger.info("Standalone empty SQLite DB detected at %s; seeding baseline records.", db_path)
        _seed_standalone_fallback(db_path)
    else:
        logger.info("Connected to BankCRM SQLite database at %s (%d clients).", db_path, count)


def _seed_standalone_fallback(db_path: str) -> None:
    """Seed the canonical 8-client institutional dataset when running in an isolated container.

    Summary:
        Populates all 3 advisors, 8 UHNW/Commercial clients, 17 accounts, 10 ledger
        transactions (including AML-flagged wires), 7 credit facilities, 6 interactions,
        7 tasks, and 7 Next-Best-Action offers when parent `bank_crm.db` is not mounted.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.

    Returns:
        None
    """
    advisors = [
        ("ADV-101", "Elena Rostova", "elena.rostova@aureliabank.com", "Managing Director — Private Wealth", "Private Client Group", "New York — Park Ave Flagship", "ER", "2022-03-14"),
        ("ADV-102", "Marcus Vance", "marcus.vance@aureliabank.com", "SVP — Commercial Credit & Treasury", "Institutional & Commercial Banking", "New York — Wall Street", "MV", "2021-07-01"),
        ("ADV-103", "Sophia Chen", "sophia.chen@aureliabank.com", "Chief Compliance & AML Risk Officer", "Global Risk & Financial Crime", "San Francisco — Embarcadero", "SC", "2020-11-09"),
    ]
    customers = [
        ("CIF-904821", "Alexander Sterling", "Sterling Quantum Ventures LLC", "Ultra-High-Net-Worth (UHNW)", "Obsidian Private", "a.sterling@sterlingquantum.io", "+1 (212) 555-0194", "New York", "United States", "Verified", "Low", 832, 4850000.00, 0.0, "Elena Rostova", "2019-04-12", "2026-10-12", 50, 20, 20, 10),
        ("CIF-904822", "Victoria Al-Mansoor", "Crescent Maritime Holdings", "Institutional & Family Office", "Obsidian Private", "victoria@crescentmaritime.ae", "+1 (305) 555-0148", "Miami", "United States", "Enhanced Due Diligence", "Medium", 798, 6200000.00, 0.0, "Elena Rostova", "2020-09-23", "2026-10-10", 35, 35, 20, 10),
        ("CIF-904823", "Dr. Henrik Lindqvist", "Nordic BioTherapeutics Inc.", "Commercial & Executive", "Platinum Sovereign", "hlindqvist@nordicbio.com", "+1 (617) 555-0172", "Boston", "United States", "Verified", "Low", 815, 2150000.00, 0.0, "Marcus Vance", "2021-02-18", "2026-10-13", 55, 25, 10, 10),
        ("CIF-904824", "Camila Montoya-Vega", "Solstice Renewable Infrastructure", "Commercial & Executive", "Platinum Sovereign", "cmontoya@solsticegrid.com", "+1 (415) 555-0183", "San Francisco", "United States", "Verified", "Low", 784, 1750000.00, 0.0, "Marcus Vance", "2022-06-05", "2026-10-08", 40, 30, 20, 10),
        ("CIF-904825", "Julian Thorne", "Thorne Heritage Real Estate Partners", "High-Net-Worth (HNW)", "Platinum Sovereign", "jthorne@thorneheritage.com", "+1 (312) 555-0166", "Chicago", "United States", "Verified", "Low", 762, 1320000.00, 0.0, "Elena Rostova", "2021-11-30", "2026-10-05", 30, 40, 20, 10),
        ("CIF-904826", "Nadia Kovalenko", "Aether Orbital Systems Corp", "Commercial & Executive", "Sapphire Premier", "n.kovalenko@aetherorbital.space", "+1 (206) 555-0129", "Seattle", "United States", "Periodic Review Due", "High", 741, 980000.00, 0.0, "Sophia Chen", "2023-01-19", "2026-10-11", 60, 15, 15, 10),
        ("CIF-904827", "Devonshire Family Trust (Arthur & Clara)", "Devonshire Philanthropic Endowment", "Institutional & Family Office", "Obsidian Private", "trustees@devonshireendowment.org", "+1 (212) 555-0115", "Greenwich", "United States", "Verified", "Low", 840, 5400000.00, 0.0, "Elena Rostova", "2018-08-01", "2026-10-14", 25, 50, 15, 10),
        ("CIF-904828", "Rohan Deshmukh", "Vanguard Silicon Photonics", "Affluent & Emerging Wealth", "Sapphire Premier", "rohan@vanguardsilicon.com", "+1 (408) 555-0151", "Palo Alto", "United States", "Verified", "Low", 791, 890000.00, 0.0, "Marcus Vance", "2023-09-14", "2026-10-09", 65, 20, 5, 10),
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
        ("TXN-20261014-901", 1, 1, "Credit", "Institutional Wire", 1250000.00, 14250000.00, "Goldman Sachs Prime Brokerage", "Capital Distribution", "Settled", 0, "", "2026-10-14 09:15:00"),
        ("TXN-20261014-902", 4, 2, "Debit", "SWIFT International Wire", 890000.00, 22400000.00, "Port of Rotterdam Bunkering BV (NL)", "Cross-Border Trade Settlement", "Pending Compliance", 1, "Cross-border maritime wire > $500k requires Dual-Control OFAC/EDD sign-off", "2026-10-14 08:42:00"),
        ("TXN-20261013-887", 7, 3, "Credit", "FEDWIRE", 640000.00, 3190000.00, "NIH Biomedical Grant Treasury", "Milestone Inflow", "Settled", 0, "", "2026-10-13 16:20:00"),
        ("TXN-20261013-884", 13, 6, "Debit", "SWIFT International Wire", 415000.00, 840000.00, "precision-titanium-supply GmbH (Zurich)", "Aerospace Component Procurement", "Flagged — AML Review", 1, "High-value cross-border wire on account with Periodic KYC Review Due", "2026-10-13 14:05:00"),
        ("TXN-20261011-859", 10, 5, "Debit", "FEDWIRE", 750000.00, 4350000.00, "Chicago Loop Title & Escrow Co.", "Commercial Property Earnest Deposit", "Settled", 1, "Large domestic real estate wire > $500k — Verified by Senior RM", "2026-10-11 15:12:00"),
    ]
    loans = [
        ("LN-2026-4401", 1, "Securities-Backed Line of Credit (SBLOC)", "Quantum Computing Series B Co-Investment Liquidity", 6500000.00, 5.65, 36, 14250000.00, 45.6, 21.4, "Approved & Ready to Fund", "AAA — Prime Collateral", "2026-09-28", "Marcus Vance"),
        ("LN-2026-4402", 2, "Commercial Maritime Fleet Facility", "Acquisition of 2 LNG-Ready Feeder Vessels", 14500000.00, 6.15, 84, 24000000.00, 60.4, 28.9, "Credit Committee Review", "AA — Institutional", "2026-10-02", "Marcus Vance"),
        ("LN-2026-4403", 3, "Biotech Lab & Cleanroom Term Loan", "Cambridge Phase-III Biomanufacturing Expansion", 4800000.00, 5.90, 60, 7500000.00, 64.0, 24.5, "Underwriting & Financial Analysis", "AA+ — Strong Cashflow", "2026-10-06", "Marcus Vance"),
        ("LN-2026-4404", 4, "Green Project Finance Bridge Facility", "Utility-Scale Battery Storage Interconnect (250MW)", 9200000.00, 5.75, 48, 14800000.00, 62.2, 26.8, "Credit Committee Review", "AA — ESG Qualified", "2026-10-04", "Marcus Vance"),
        ("LN-2026-4405", 5, "Commercial Real Estate Senior Mortgage", "Refinancing Class-A Mixed-Use Tower — Fulton Market", 8400000.00, 6.35, 120, 12500000.00, 67.2, 33.1, "Underwriting & Financial Analysis", "A+ — Core CRE", "2026-10-08", "Marcus Vance"),
        ("LN-2026-4406", 6, "Working Capital Revolving Line", "Satellite Propulsion Subsystem Inventory Financing", 2750000.00, 6.85, 24, 3800000.00, 72.4, 37.5, "Application & KYC Intake", "BBB+ — Enhanced Review", "2026-10-11", "Sophia Chen"),
        ("LN-2026-4407", 8, "Jumbo Private Residential Mortgage", "Atherton Custom Estate Acquisition", 3900000.00, 5.50, 360, 5600000.00, 69.6, 27.2, "Disbursed & Active", "AAA — Prime Jumbo", "2026-09-10", "Elena Rostova"),
    ]
    nba_offers = [
        (1, "Direct Indexing & Automated Tax-Loss Harvesting Mandate", "Private Wealth Management", 97, 42500.00, "High equity concentration (50%) and $4.85M annual income create ~$180k/yr tax-alpha opportunity.", "Recommended"),
        (2, "Multi-Currency FX Forward Hedging Facility (EUR / USD)", "Global Treasury & FX", 95, 68000.00, "Recurring European maritime settlements ($890k+ monthly) expose cashflows to EUR/USD volatility.", "Recommended"),
        (3, "Executive Key-Person & R&D Equipment Leasing Facility", "Commercial Structured Finance", 91, 34000.00, "Complements Phase-III cleanroom loan while preserving R&D equity runway.", "Recommended"),
        (4, "Automated ESG Overnight Repo Treasury Sweep", "Commercial Treasury", 94, 29500.00, "Idle operating cash ($1.45M) qualifies for +265 bps higher yield in green repo sweep.", "Pitched — Client Reviewing"),
        (5, "Interest Rate Cap & Swap Structure (SOFR 4.25% Ceiling)", "Derivatives & Risk Management", 89, 38000.00, "Protects $8.4M Fulton Market CRE refinancing against floating-rate DSCR compression.", "Recommended"),
        (7, "Private Credit & Infrastructure Co-Investment Fund IV", "Alternative Investments", 93, 85000.00, "Matches endowment's 10-year horizon and 7.8% tax-exempt yield target.", "Recommended"),
        (8, "10b5-1 Executive Equity Diversification & Collar Trust", "Founder Wealth Structuring", 96, 51000.00, "65% single-stock photonics equity exposure warrants zero-cost collar hedging.", "Recommended"),
    ]

    with get_connection(db_path) as conn:
        conn.executemany(
            "INSERT INTO advisors (advisor_code, full_name, email, role_title, department, branch_location, avatar_initials, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            advisors,
        )
        conn.executemany(
            "INSERT INTO customers (cif_number, full_name, company_name, segment, tier, email, phone, city, country, kyc_status, aml_risk_rating, credit_score, annual_income_usd, total_aum_usd, relationship_manager, onboarded_date, last_contact_date, portfolio_equities_pct, portfolio_fixed_income_pct, portfolio_private_equity_pct, portfolio_cash_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            customers,
        )
        conn.executemany(
            "INSERT INTO accounts (account_number, customer_id, account_type, currency, balance_usd, apy_rate, status, opened_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            accounts,
        )
        conn.executemany(
            "INSERT INTO transactions (reference_id, account_id, customer_id, txn_type, channel, amount_usd, balance_after_usd, counterparty, category, status, risk_flag, risk_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            transactions,
        )
        conn.executemany(
            "INSERT INTO loans (loan_number, customer_id, loan_type, purpose, principal_usd, interest_rate_pct, term_months, collateral_value_usd, ltv_ratio_pct, dti_ratio_pct, stage, underwriting_grade, submitted_date, assigned_underwriter) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            loans,
        )
        conn.executemany(
            "INSERT INTO nba_offers (customer_id, product_name, product_category, match_score_pct, estimated_revenue_usd, rationale, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            nba_offers,
        )
        for c_row in conn.execute("SELECT id FROM customers").fetchall():
            _sync_customer_aum(conn, int(c_row["id"]))


def _sync_customer_aum(conn: sqlite3.Connection, customer_id: int) -> float:
    """Recalculate and persist a customer's consolidated AUM across active accounts.

    Summary:
        Sums `balance_usd` for all non-closed accounts owned by `customer_id`
        and updates `customers.total_aum_usd`.

    Parameters:
        conn (sqlite3.Connection): Active transactional SQLite connection.
        customer_id (int): Primary key ID of the customer.

    Returns:
        float: Updated total AUM in USD.
    """
    row = conn.execute(
        """
        SELECT COALESCE(SUM(balance_usd), 0.0) AS total_balance
        FROM accounts
        WHERE customer_id = ? AND status != 'Closed'
        """,
        (customer_id,),
    ).fetchone()
    total_aum = round(float(row["total_balance"]) if row else 0.0, 2)
    conn.execute(
        "UPDATE customers SET total_aum_usd = ? WHERE id = ?",
        (total_aum, customer_id),
    )
    return total_aum


def query_customers(
    db_path: str,
    query: str = "",
    tier: str = "",
    kyc_status: str = "",
) -> List[Dict[str, Any]]:
    """Search and filter banking customers by name, company, CIF, tier, or KYC status.

    Summary:
        Executes a parameterized query against `customers` enriched with account
        and loan counts, ordered by total AUM descending.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        query (str): Optional substring matching name, company, CIF, email, or city.
        tier (str): Optional exact tier filter ('Obsidian Private', 'Platinum Sovereign', 'Sapphire Premier').
        kyc_status (str): Optional exact KYC filter ('Verified', 'Enhanced Due Diligence', 'Periodic Review Due').

    Returns:
        List[Dict[str, Any]]: Matching customer dictionaries.
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

    if query.strip():
        q = f"%{query.strip().lower()}%"
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

    if tier.strip():
        sql += " AND LOWER(c.tier) = LOWER(?)"
        params.append(tier.strip())

    if kyc_status.strip():
        sql += " AND LOWER(c.kyc_status) = LOWER(?)"
        params.append(kyc_status.strip())

    sql += " ORDER BY c.total_aum_usd DESC, c.id ASC"

    with get_connection(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]


def fetch_customer_360_dossier(
    db_path: str, identifier: str
) -> Optional[Dict[str, Any]]:
    """Fetch a complete Customer 360 Dossier by numeric ID, CIF number, or client name.

    Summary:
        Resolves the target customer using flexible identifier matching (ID,
        `CIF-xxxxxx`, or full name) and aggregates all accounts, transactions,
        loans, interactions, tasks, and Next-Best-Action offers.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        identifier (str): Numeric customer ID (e.g. '1'), CIF code (e.g. 'CIF-904821'),
            or client name substring (e.g. 'Alexander Sterling').

    Returns:
        Optional[Dict[str, Any]]: Complete Customer 360 dictionary, or None if not found.
    """
    clean_id = str(identifier).strip()
    if not clean_id:
        return None

    with get_connection(db_path) as conn:
        cust_row = None
        if clean_id.isdigit():
            cust_row = conn.execute(
                "SELECT * FROM customers WHERE id = ?", (int(clean_id),)
            ).fetchone()

        if cust_row is None:
            cust_row = conn.execute(
                """
                SELECT * FROM customers
                WHERE LOWER(cif_number) = LOWER(?)
                   OR LOWER(full_name) LIKE LOWER(?)
                   OR LOWER(company_name) LIKE LOWER(?)
                ORDER BY total_aum_usd DESC
                LIMIT 1
                """,
                (clean_id, f"%{clean_id}%", f"%{clean_id}%"),
            ).fetchone()

        if cust_row is None:
            return None

        cid = int(cust_row["id"])
        accounts = conn.execute(
            "SELECT * FROM accounts WHERE customer_id = ? ORDER BY balance_usd DESC",
            (cid,),
        ).fetchall()
        txns = conn.execute(
            """
            SELECT t.*, a.account_number
            FROM transactions t
            JOIN accounts a ON t.account_id = a.id
            WHERE t.customer_id = ?
            ORDER BY t.created_at DESC, t.id DESC
            LIMIT 20
            """,
            (cid,),
        ).fetchall()
        loans = conn.execute(
            "SELECT * FROM loans WHERE customer_id = ? ORDER BY id DESC", (cid,)
        ).fetchall()
        interactions = conn.execute(
            "SELECT * FROM interactions WHERE customer_id = ? ORDER BY logged_at DESC LIMIT 15",
            (cid,),
        ).fetchall()
        tasks = conn.execute(
            "SELECT * FROM tasks WHERE customer_id = ? ORDER BY status ASC, due_date ASC",
            (cid,),
        ).fetchall()
        nba_offers = conn.execute(
            "SELECT * FROM nba_offers WHERE customer_id = ? ORDER BY match_score_pct DESC",
            (cid,),
        ).fetchall()

    dossier = dict(cust_row)
    dossier["accounts"] = [dict(r) for r in accounts]
    dossier["recent_transactions"] = [dict(r) for r in txns]
    dossier["loans"] = [dict(r) for r in loans]
    dossier["interactions"] = [dict(r) for r in interactions]
    dossier["tasks"] = [dict(r) for r in tasks]
    dossier["nba_offers"] = [dict(r) for r in nba_offers]
    return dossier


def fetch_bank_executive_summary(db_path: str) -> Dict[str, Any]:
    """Compute bank-wide executive KPIs, liquidity metrics, and compliance telemetry.

    Summary:
        Aggregates total institutional AUM, client counts, credit pipeline volume,
        net inflows/outflows, AML flagged wire counts, and asset allocation by product.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.

    Returns:
        Dict[str, Any]: Consolidated bank executive telemetry payload.
    """
    with get_connection(db_path) as conn:
        cust_row = conn.execute(
            "SELECT COUNT(*) AS client_count, COALESCE(SUM(total_aum_usd), 0.0) AS total_aum, "
            "COALESCE(AVG(credit_score), 0.0) AS avg_credit_score FROM customers"
        ).fetchone()
        loan_row = conn.execute(
            "SELECT COUNT(*) AS loan_count, COALESCE(SUM(principal_usd), 0.0) AS total_pipeline, "
            "COALESCE(AVG(interest_rate_pct), 0.0) AS avg_rate, COALESCE(AVG(ltv_ratio_pct), 0.0) AS avg_ltv FROM loans"
        ).fetchone()
        txn_row = conn.execute(
            """
            SELECT
                COUNT(*) AS txn_count,
                COALESCE(SUM(CASE WHEN txn_type = 'Credit' THEN amount_usd ELSE 0 END), 0.0) AS gross_inflows,
                COALESCE(SUM(CASE WHEN txn_type = 'Debit' THEN amount_usd ELSE 0 END), 0.0) AS gross_outflows,
                SUM(CASE WHEN risk_flag = 1 THEN 1 ELSE 0 END) AS flagged_holds
            FROM transactions
            """
        ).fetchone()
        task_row = conn.execute(
            "SELECT COUNT(*) AS pending_tasks, "
            "SUM(CASE WHEN priority = 'Critical' THEN 1 ELSE 0 END) AS critical_tasks "
            "FROM tasks WHERE status != 'Completed'"
        ).fetchone()
        by_tier = conn.execute(
            """
            SELECT tier, COUNT(*) AS clients, ROUND(SUM(total_aum_usd), 2) AS tier_aum_usd
            FROM customers
            GROUP BY tier
            ORDER BY tier_aum_usd DESC
            """
        ).fetchall()

    inflows = round(float(txn_row["gross_inflows"] or 0.0), 2)
    outflows = round(float(txn_row["gross_outflows"] or 0.0), 2)

    return {
        "total_aum_usd": round(float(cust_row["total_aum"] or 0.0), 2),
        "active_clients_count": int(cust_row["client_count"] or 0),
        "average_client_fico_score": int(round(float(cust_row["avg_credit_score"] or 0.0))),
        "credit_pipeline_volume_usd": round(float(loan_row["total_pipeline"] or 0.0), 2),
        "active_loan_facilities_count": int(loan_row["loan_count"] or 0),
        "weighted_avg_loan_rate_pct": round(float(loan_row["avg_rate"] or 0.0), 2),
        "weighted_avg_ltv_pct": round(float(loan_row["avg_ltv"] or 0.0), 1),
        "gross_inflows_usd": inflows,
        "gross_outflows_usd": outflows,
        "net_liquidity_flow_usd": round(inflows - outflows, 2),
        "aml_flagged_wires_count": int(txn_row["flagged_holds"] or 0),
        "pending_advisor_tasks_count": int(task_row["pending_tasks"] or 0),
        "critical_tasks_count": int(task_row["critical_tasks"] or 0),
        "aum_breakdown_by_tier": [dict(r) for r in by_tier],
    }


def query_loan_pipeline(db_path: str, stage: str = "") -> List[Dict[str, Any]]:
    """Retrieve commercial and private wealth loan facilities in the underwriting pipeline.

    Summary:
        Fetches loan records joined with borrower CIF, name, company, and FICO score.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        stage (str): Optional stage filter substring (e.g., 'Credit Committee Review').

    Returns:
        List[Dict[str, Any]]: List of loan facility dictionaries.
    """
    sql = """
        SELECT
            l.*,
            c.full_name AS customer_name,
            c.company_name,
            c.cif_number,
            c.credit_score
        FROM loans l
        JOIN customers c ON l.customer_id = c.id
        WHERE 1 = 1
    """
    params: List[Any] = []
    if stage.strip():
        sql += " AND LOWER(l.stage) LIKE LOWER(?)"
        params.append(f"%{stage.strip()}%")

    sql += " ORDER BY l.principal_usd DESC, l.id DESC"
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]


def query_flagged_or_recent_transactions(
    db_path: str, flagged_only: bool = True, limit: int = 25
) -> List[Dict[str, Any]]:
    """List AML/OFAC risk-flagged wires or recent settlement ledger entries.

    Summary:
        Queries `transactions` joined with customer and account metadata so
        external compliance or treasury agents can audit wire holds.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        flagged_only (bool): When True, returns only transactions with `risk_flag = 1`.
        limit (int): Maximum number of rows to return (1–100).

    Returns:
        List[Dict[str, Any]]: Matching transaction dictionaries.
    """
    safe_limit = max(1, min(int(limit), 100))
    sql = """
        SELECT
            t.*,
            c.full_name AS customer_name,
            c.company_name,
            c.cif_number,
            c.kyc_status,
            c.aml_risk_rating,
            a.account_number,
            a.account_type
        FROM transactions t
        JOIN customers c ON t.customer_id = c.id
        JOIN accounts a ON t.account_id = a.id
    """
    if flagged_only:
        sql += " WHERE t.risk_flag = 1"
    sql += " ORDER BY t.created_at DESC, t.id DESC LIMIT ?"

    with get_connection(db_path) as conn:
        rows = conn.execute(sql, (safe_limit,)).fetchall()
        return [dict(r) for r in rows]


def query_nba_recommendations(
    db_path: str, customer_identifier: str = "", status_filter: str = ""
) -> List[Dict[str, Any]]:
    """Fetch AI Next-Best-Action wealth and treasury product recommendations.

    Summary:
        Returns propensity-scored cross-sell mandates enriched with client AUM
        and estimated annual fee revenue.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        customer_identifier (str): Optional client ID, CIF, or name filter.
        status_filter (str): Optional offer status filter ('Recommended', 'Pitched', 'Accepted').

    Returns:
        List[Dict[str, Any]]: Matching Next-Best-Action offer dictionaries.
    """
    sql = """
        SELECT
            o.*,
            c.full_name AS customer_name,
            c.company_name,
            c.cif_number,
            c.tier,
            c.total_aum_usd
        FROM nba_offers o
        JOIN customers c ON o.customer_id = c.id
        WHERE 1 = 1
    """
    params: List[Any] = []

    if customer_identifier.strip():
        clean = customer_identifier.strip()
        if clean.isdigit():
            sql += " AND c.id = ?"
            params.append(int(clean))
        else:
            q = f"%{clean.lower()}%"
            sql += " AND (LOWER(c.cif_number) LIKE ? OR LOWER(c.full_name) LIKE ? OR LOWER(c.company_name) LIKE ?)"
            params.extend([q, q, q])

    if status_filter.strip():
        sql += " AND LOWER(o.status) LIKE LOWER(?)"
        params.append(f"%{status_filter.strip()}%")

    sql += " ORDER BY o.match_score_pct DESC, o.estimated_revenue_usd DESC"

    with get_connection(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]


def execute_agent_wire_or_transfer(
    db_path: str,
    account_identifier: str,
    txn_type: str,
    amount_usd: float,
    counterparty: str,
    channel: str = "FEDWIRE",
    category: str = "Agent-Initiated Treasury Settlement",
    destination_account_identifier: str = "",
    wire_threshold_usd: float = 25000.0,
) -> Dict[str, Any]:
    """Execute an atomic deposit credit, outgoing wire debit, or internal book transfer.

    Summary:
        Resolves the source account by ID or account number (`AUR-xxxx-xxxx`),
        verifies sufficient funds, evaluates FinCEN/OFAC compliance rules, updates
        account balances and client AUM, and records the immutable ledger entry.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        account_identifier (str): Account ID (e.g. '1') or account number (e.g. 'AUR-8801-0019').
        txn_type (str): 'Credit', 'Debit', or 'Internal Transfer'.
        amount_usd (float): Positive USD amount to transfer.
        counterparty (str): External counterparty name (or transfer memo).
        channel (str): Settlement rail ('FEDWIRE', 'SWIFT International Wire', 'ACH Institutional').
        category (str): Transaction purpose category.
        destination_account_identifier (str): Destination account ID or number for 'Internal Transfer'.
        wire_threshold_usd (float): USD threshold for automated SWIFT/AML compliance holds.

    Returns:
        Dict[str, Any]: Created transaction dictionary including `reference_id` and `risk_flag`.

    Raises:
        ValueError: If account is missing, amount <= 0, or balance is insufficient.
    """
    amt = round(float(amount_usd), 2)
    if amt <= 0:
        raise ValueError("Transaction amount_usd must be strictly greater than 0.")

    normalized_type = txn_type.strip()
    if normalized_type not in ("Credit", "Debit", "Internal Transfer"):
        raise ValueError("txn_type must be 'Credit', 'Debit', or 'Internal Transfer'.")

    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    ref_id = f"TXN-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().int % 900 + 100}"

    with get_connection(db_path) as conn:
        src_acct = _resolve_account(conn, account_identifier)
        if src_acct is None:
            raise ValueError(f"Source account '{account_identifier}' not found.")

        src_id = int(src_acct["id"])
        customer_id = int(src_acct["customer_id"])
        src_balance = float(src_acct["balance_usd"])

        if normalized_type in ("Debit", "Internal Transfer") and src_balance < amt:
            raise ValueError(
                f"Insufficient balance in {src_acct['account_number']} "
                f"(Available: ${src_balance:,.2f}, Requested: ${amt:,.2f})."
            )

        risk_flag = 0
        risk_reason = ""
        status = "Settled"

        if normalized_type == "Debit":
            if "SWIFT" in channel.upper() and amt >= wire_threshold_usd:
                risk_flag = 1
                risk_reason = (
                    f"Cross-border SWIFT wire (${amt:,.2f}) exceeds "
                    f"${wire_threshold_usd:,.0f} AML review threshold"
                )
                status = "Pending Compliance"
            elif amt >= 500000.0:
                risk_flag = 1
                risk_reason = f"High-value wire >= $500,000 (${amt:,.2f}) requires Dual-Control sign-off"
                status = "Pending Compliance"

        if normalized_type == "Internal Transfer":
            dest_acct = _resolve_account(conn, destination_account_identifier)
            if dest_acct is None or int(dest_acct["id"]) == src_id:
                raise ValueError(
                    "Valid, distinct destination_account_identifier is required for Internal Transfer."
                )

            dest_id = int(dest_acct["id"])
            new_src_bal = round(src_balance - amt, 2)
            new_dest_bal = round(float(dest_acct["balance_usd"]) + amt, 2)

            conn.execute("UPDATE accounts SET balance_usd = ? WHERE id = ?", (new_src_bal, src_id))
            conn.execute("UPDATE accounts SET balance_usd = ? WHERE id = ?", (new_dest_bal, dest_id))

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
                    src_id,
                    customer_id,
                    amt,
                    new_src_bal,
                    f"Transfer to {dest_acct['account_number']} ({dest_acct['customer_name']})",
                    category,
                    now_ts,
                ),
            )
            txn_id = int(cursor.lastrowid)

            conn.execute(
                """
                INSERT INTO transactions (
                    reference_id, account_id, customer_id, txn_type, channel,
                    amount_usd, balance_after_usd, counterparty, category,
                    status, risk_flag, risk_reason, created_at
                ) VALUES (?, ?, ?, 'Credit', 'Internal Book Transfer', ?, ?, ?, ?, 'Settled', 0, '', ?)
                """,
                (
                    f"{ref_id}-CR",
                    dest_id,
                    int(dest_acct["customer_id"]),
                    amt,
                    new_dest_bal,
                    f"Transfer from {src_acct['account_number']} ({src_acct['customer_name']})",
                    category,
                    now_ts,
                ),
            )
            _sync_customer_aum(conn, customer_id)
            _sync_customer_aum(conn, int(dest_acct["customer_id"]))
        else:
            new_bal = (
                round(src_balance + amt, 2)
                if normalized_type == "Credit"
                else round(src_balance - amt, 2)
            )
            conn.execute("UPDATE accounts SET balance_usd = ? WHERE id = ?", (new_bal, src_id))
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
                    src_id,
                    customer_id,
                    normalized_type,
                    channel,
                    amt,
                    new_bal,
                    counterparty.strip() or "Institutional Counterparty",
                    category,
                    status,
                    risk_flag,
                    risk_reason,
                    now_ts,
                ),
            )
            txn_id = int(cursor.lastrowid)
            _sync_customer_aum(conn, customer_id)

        row = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        return dict(row) if row else {}


def _resolve_account(
    conn: sqlite3.Connection, identifier: str
) -> Optional[sqlite3.Row]:
    """Locate an account by numeric ID or account number string.

    Summary:
        Internal helper joining `accounts` and `customers` for wire execution.

    Parameters:
        conn (sqlite3.Connection): Active SQLite connection.
        identifier (str): Numeric ID or `AUR-xxxx-xxxx` account number.

    Returns:
        Optional[sqlite3.Row]: Account record with `customer_name`, or None.
    """
    clean = str(identifier).strip()
    if not clean:
        return None
    if clean.isdigit():
        row = conn.execute(
            """
            SELECT a.*, c.full_name AS customer_name
            FROM accounts a
            JOIN customers c ON a.customer_id = c.id
            WHERE a.id = ?
            """,
            (int(clean),),
        ).fetchone()
        if row:
            return row

    return conn.execute(
        """
        SELECT a.*, c.full_name AS customer_name
        FROM accounts a
        JOIN customers c ON a.customer_id = c.id
        WHERE LOWER(a.account_number) = LOWER(?)
        """,
        (clean,),
    ).fetchone()


def resolve_aml_hold(
    db_path: str, transaction_identifier: str, officer_name: str
) -> Dict[str, Any]:
    """Approve and release an AML/Compliance hold on a flagged wire transaction.

    Summary:
        Finds the transaction by numeric ID or `TXN-...` reference, clears
        `risk_flag = 0`, marks status `Settled`, and records an audit interaction.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        transaction_identifier (str): Transaction ID or `TXN-YYYYMMDD-xxx` reference.
        officer_name (str): Name of the compliance officer or AI agent approving release.

    Returns:
        Dict[str, Any]: Updated transaction dictionary.

    Raises:
        ValueError: If the transaction cannot be found.
    """
    clean = str(transaction_identifier).strip()
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with get_connection(db_path) as conn:
        txn = None
        if clean.isdigit():
            txn = conn.execute(
                "SELECT * FROM transactions WHERE id = ?", (int(clean),)
            ).fetchone()
        if txn is None:
            txn = conn.execute(
                "SELECT * FROM transactions WHERE LOWER(reference_id) = LOWER(?)",
                (clean,),
            ).fetchone()
        if txn is None:
            raise ValueError(f"Transaction '{transaction_identifier}' not found.")

        txn_id = int(txn["id"])
        audit_note = f"Cleared by {officer_name} on {now_ts[:10]} (Prev: {txn['risk_reason']})"
        conn.execute(
            """
            UPDATE transactions
            SET risk_flag = 0, status = 'Settled', risk_reason = ?
            WHERE id = ?
            """,
            (audit_note, txn_id),
        )
        conn.execute(
            """
            INSERT INTO interactions (
                customer_id, channel, subject, sentiment, summary, advisor_name, logged_at
            ) VALUES (?, 'MCP Compliance Desk', ?, 'Positive', ?, ?, ?)
            """,
            (
                int(txn["customer_id"]),
                f"AML Hold Released ({txn['reference_id']})",
                f"Dual-control sign-off recorded by {officer_name} for ${float(txn['amount_usd']):,.2f} wire to {txn['counterparty']}.",
                officer_name,
                now_ts,
            ),
        )
        updated = conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (txn_id,)
        ).fetchone()
        return dict(updated) if updated else {}


def originate_agent_credit_facility(
    db_path: str,
    customer_identifier: str,
    loan_type: str,
    purpose: str,
    principal_usd: float,
    collateral_value_usd: float,
    interest_rate_pct: float = 5.85,
    term_months: int = 60,
    underwriter_name: str = "Marcus Vance",
) -> Dict[str, Any]:
    """Originate a new commercial or private wealth credit facility with automated LTV/DTI underwriting.

    Summary:
        Resolves the borrower, computes Loan-to-Value (LTV), estimates Debt-to-Income
        (DTI) from monthly debt service, assigns an institutional underwriting grade,
        and inserts the facility into `loans`.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        customer_identifier (str): Customer ID, CIF number, or full name.
        loan_type (str): Credit structure (e.g., 'Securities-Backed Line of Credit (SBLOC)').
        purpose (str): Underwriting purpose and covenant summary.
        principal_usd (float): Requested loan principal in USD.
        collateral_value_usd (float): Pledged collateral valuation in USD.
        interest_rate_pct (float): Annual interest rate percentage.
        term_months (int): Amortization term in months.
        underwriter_name (str): Assigned underwriter or agent identifier.

    Returns:
        Dict[str, Any]: Newly originated loan record with calculated LTV, DTI, and grade.

    Raises:
        ValueError: If borrower is not found or financial inputs are non-positive.
    """
    dossier = fetch_customer_360_dossier(db_path, customer_identifier)
    if dossier is None:
        raise ValueError(f"Customer '{customer_identifier}' not found.")

    principal = round(float(principal_usd), 2)
    collateral = round(float(collateral_value_usd), 2)
    rate = round(float(interest_rate_pct), 2)
    term = int(term_months)

    if principal <= 0 or collateral <= 0 or term <= 0:
        raise ValueError("principal_usd, collateral_value_usd, and term_months must be > 0.")

    ltv_pct = round((principal / collateral) * 100.0, 1)
    monthly_rate = (rate / 100.0) / 12.0
    if monthly_rate > 0:
        monthly_payment = principal * (monthly_rate / (1.0 - (1.0 + monthly_rate) ** (-term)))
    else:
        monthly_payment = principal / term

    monthly_income = max(float(dossier["annual_income_usd"]) / 12.0, 25000.0)
    dti_pct = round(min((monthly_payment / monthly_income) * 100.0, 48.5), 1)

    fico = int(dossier["credit_score"])
    if ltv_pct <= 55.0 and fico >= 800:
        grade = "AAA — Prime Collateral"
    elif ltv_pct <= 68.0 and fico >= 760:
        grade = "AA — Institutional"
    elif ltv_pct <= 75.0:
        grade = "A+ — Core Credit"
    else:
        grade = "BBB+ — Enhanced Review"

    loan_number = f"LN-2026-{uuid.uuid4().int % 9000 + 1000}"
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with get_connection(db_path) as conn:
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
                int(dossier["id"]),
                loan_type.strip(),
                purpose.strip() or "Institutional Credit Facility",
                principal,
                rate,
                term,
                collateral,
                ltv_pct,
                dti_pct,
                grade,
                today_str,
                underwriter_name.strip(),
            ),
        )
        loan_id = int(cursor.lastrowid)
        row = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        return dict(row) if row else {}


def log_agent_interaction_and_optional_task(
    db_path: str,
    customer_identifier: str,
    subject: str,
    summary: str,
    channel: str = "AI Agent Advisory Briefing",
    sentiment: str = "Positive",
    advisor_name: str = "Aurelia AI Advisory Agent",
    follow_up_task_title: str = "",
    follow_up_priority: str = "High",
    follow_up_due_date: str = "",
) -> Dict[str, Any]:
    """Record a client interaction note and optionally schedule a follow-up advisor task.

    Summary:
        Appends an interaction entry to the client's 360 timeline, updates
        `last_contact_date`, and creates a linked row in `tasks` if
        `follow_up_task_title` is provided.

    Parameters:
        db_path (str): Filesystem path to the SQLite database file.
        customer_identifier (str): Customer ID, CIF number, or client name.
        subject (str): Brief headline of the interaction.
        summary (str): Detailed advisory or compliance summary notes.
        channel (str): Interaction channel label.
        sentiment (str): 'Positive', 'Neutral', or 'Attention Required'.
        advisor_name (str): Advisor or AI agent name.
        follow_up_task_title (str): Optional title to create a follow-up deliverable task.
        follow_up_priority (str): Priority for the follow-up task ('Critical', 'High', 'Medium').
        follow_up_due_date (str): Optional YYYY-MM-DD due date for the follow-up task.

    Returns:
        Dict[str, Any]: Dictionary containing `interaction` and optional `created_task`.

    Raises:
        ValueError: If customer is not found or subject/summary is empty.
    """
    dossier = fetch_customer_360_dossier(db_path, customer_identifier)
    if dossier is None:
        raise ValueError(f"Customer '{customer_identifier}' not found.")

    if not subject.strip() or not summary.strip():
        raise ValueError("Both subject and summary are required to log an interaction.")

    cid = int(dossier["id"])
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with get_connection(db_path) as conn:
        int_cursor = conn.execute(
            """
            INSERT INTO interactions (
                customer_id, channel, subject, sentiment, summary, advisor_name, logged_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cid,
                channel.strip(),
                subject.strip(),
                sentiment.strip(),
                summary.strip(),
                advisor_name.strip(),
                now_ts,
            ),
        )
        conn.execute(
            "UPDATE customers SET last_contact_date = ? WHERE id = ?",
            (today_str, cid),
        )
        interaction_row = conn.execute(
            "SELECT * FROM interactions WHERE id = ?", (int(int_cursor.lastrowid),)
        ).fetchone()

        created_task = None
        if follow_up_task_title.strip():
            due = follow_up_due_date.strip() or today_str
            task_cursor = conn.execute(
                """
                INSERT INTO tasks (
                    customer_id, title, category, priority, due_date, status, assigned_to, notes
                ) VALUES (?, ?, 'Advisory Follow-Up', ?, ?, 'Pending', ?, ?)
                """,
                (
                    cid,
                    follow_up_task_title.strip(),
                    follow_up_priority.strip() or "High",
                    due,
                    advisor_name.strip(),
                    f"Auto-scheduled from interaction: {subject.strip()}",
                ),
            )
            task_row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (int(task_cursor.lastrowid),)
            ).fetchone()
            created_task = dict(task_row) if task_row else None

    return {
        "customer_id": cid,
        "customer_name": dossier["full_name"],
        "cif_number": dossier["cif_number"],
        "interaction": dict(interaction_row) if interaction_row else {},
        "created_task": created_task,
    }
