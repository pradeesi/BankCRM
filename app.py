# ==============================================================================
# Purpose: Main Flask web application and HTTP controller layer for Aurelia
#   Private Bank & Trust CRM.
# Architecture/Context: Serves server-rendered Bootstrap 5 views and JSON API
#   endpoints across all 5 banking CRM modules (Executive Dashboard, Customer 360,
#   Loan Origination Pipeline, Wire & Account Ledger, and Tasks/NBA Engine).
# Dependencies/Side Effects: Depends on `config.py` and `database.py`; manages
#   signed Flask user sessions and initializes SQLite storage on startup.
# Author: Pradeep Singh
# License: Apache-2.0
# ==============================================================================

import logging
from typing import Any, Dict, Tuple, Union
from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from config import AppConfig, load_config
from database import (
    create_advisor_task,
    create_bank_account,
    create_client_interaction,
    create_customer_with_initial_account,
    create_loan_application,
    execute_ledger_transaction,
    get_advisor_by_email,
    get_all_advisors,
    get_customer_360,
    get_dashboard_summary,
    init_database,
    list_customers,
    list_ledger_transactions,
    list_loans_pipeline,
    list_tasks_and_offers,
    reset_and_seed_database,
    resolve_transaction_risk_flag,
    toggle_task_status,
    update_customer_kyc_and_tier,
    update_loan_stage,
    update_nba_offer_status,
)

logger = logging.getLogger("aurelia_crm.app")


def format_currency(value: Any) -> str:
    """Format a numeric value as a standard US Dollar currency string.

    Summary:
        Jinja2 template filter that converts floats/ints into `$1,234,567.89` format.

    Parameters:
        value (Any): Numeric value to format.

    Returns:
        str: Formatted currency string, or '$0.00' if conversion fails.
    """
    try:
        num = float(value or 0.0)
        return f"${num:,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def format_compact_currency(value: Any) -> str:
    """Format large USD figures into compact institutional notation ($M / $K).

    Summary:
        Converts numbers >= 1,000,000 into `$14.25M` and >= 1,000 into `$685.0K`
        for executive KPI cards and badges.

    Parameters:
        value (Any): Numeric USD amount.

    Returns:
        str: Compact currency string.
    """
    try:
        num = float(value or 0.0)
    except (TypeError, ValueError):
        return "$0"

    abs_num = abs(num)
    sign = "-" if num < 0 else ""
    if abs_num >= 1_000_000_000:
        return f"{sign}${abs_num / 1_000_000_000:.2f}B"
    if abs_num >= 1_000_000:
        return f"{sign}${abs_num / 1_000_000:.2f}M"
    if abs_num >= 1_000:
        return f"{sign}${abs_num / 1_000:.1f}K"
    return f"{sign}${abs_num:,.2f}"


def create_app(cfg: AppConfig = None) -> Flask:
    """Instantiate and configure the Flask application instance.

    Summary:
        Application factory that initializes SQLite schema/seeding, registers
        custom Jinja2 filters, attaches global template context processors, and
        binds all banking CRM routes.

    Parameters:
        cfg (AppConfig, optional): Explicit configuration object. Loads from
            environment via `load_config()` when omitted.

    Returns:
        Flask: Configured Flask WSGI application.
    """
    active_cfg = cfg or load_config()
    app = Flask(__name__)
    app.secret_key = active_cfg.secret_key
    app.config["APP_CONFIG"] = active_cfg

    # Ensure SQLite database and demo dataset are ready before handling requests
    init_database(active_cfg.database_path, auto_seed=active_cfg.auto_seed_database)

    app.jinja_env.filters["usd"] = format_currency
    app.jinja_env.filters["usd_compact"] = format_compact_currency

    def get_current_advisor() -> Dict[str, Any]:
        """Resolve the currently active Relationship Manager or Risk Officer persona.

        Summary:
            Reads `advisor_email` from the signed Flask session or falls back to
            the configured default advisor so the portal works frictionlessly.

        Parameters:
            None

        Returns:
            Dict[str, Any]: Active advisor dictionary.
        """
        email = session.get("advisor_email") or active_cfg.default_rm_email
        advisor = get_advisor_by_email(active_cfg.database_path, email)
        if advisor is None:
            return {
                "full_name": "Elena Rostova",
                "email": active_cfg.default_rm_email,
                "role_title": "Managing Director — Private Wealth",
                "department": "Private Client Group",
                "branch_location": "New York — Park Ave Flagship",
                "avatar_initials": "ER",
            }
        return advisor

    @app.context_processor
    def inject_global_context() -> Dict[str, Any]:
        """Inject institutional metadata, active advisor persona, and alert badges into templates.

        Summary:
            Provides `bank_name`, `current_advisor`, `all_advisors`, and live
            navigation counters (`flagged_txn_count`, `open_tasks_count`) to all views.

        Parameters:
            None

        Returns:
            Dict[str, Any]: Template context dictionary.
        """
        advisors = get_all_advisors(active_cfg.database_path)
        current_adv = get_current_advisor()
        summary = get_dashboard_summary(active_cfg.database_path)
        all_clients = list_customers(active_cfg.database_path)
        return {
            "bank_name": active_cfg.bank_name,
            "current_advisor": current_adv,
            "all_advisors": advisors,
            "global_clients": all_clients,
            "nav_kpis": summary["kpis"],
            "wire_threshold_usd": active_cfg.high_value_wire_threshold_usd,
        }

    @app.route("/login", methods=["GET", "POST"])
    def login_view() -> Union[str, Response]:
        """Render the institutional login portal or authenticate a selected persona.

        Summary:
            Allows 1-click sign-in as a Relationship Manager, Commercial Credit SVP,
            or Chief Compliance Officer, setting the active session persona.

        Parameters:
            None (reads `advisor_email` from form POST data).

        Returns:
            Union[str, Response]: Rendered `login.html` or redirect to Dashboard.
        """
        if request.method == "POST":
            email = request.form.get("advisor_email", active_cfg.default_rm_email).strip()
            advisor = get_advisor_by_email(active_cfg.database_path, email)
            if advisor:
                session["advisor_email"] = advisor["email"]
                logger.info("Authenticated advisor session for %s (%s)", advisor["full_name"], advisor["role_title"])
                flash(
                    f"Welcome back, {advisor['full_name']} ({advisor['role_title']}).",
                    "success",
                )
            return redirect(url_for("dashboard_view"))

        return render_template("login.html")

    @app.route("/switch-persona", methods=["POST"])
    def switch_persona_action() -> Response:
        """Switch the active bank staff persona from the top navigation bar.

        Summary:
            Updates `session['advisor_email']` and redirects back to the referring page.

        Parameters:
            None (reads `advisor_email` and `next_url` from POST form).

        Returns:
            Response: Redirect to the active page.
        """
        email = request.form.get("advisor_email", "").strip()
        advisor = get_advisor_by_email(active_cfg.database_path, email)
        if advisor:
            session["advisor_email"] = advisor["email"]
            logger.info("Switched active persona to %s", advisor["full_name"])
            flash(
                f"Switched active workspace persona to {advisor['full_name']} — {advisor['role_title']}.",
                "info",
            )
        next_url = request.form.get("next_url") or request.referrer or url_for("dashboard_view")
        return redirect(next_url)

    @app.route("/logout", methods=["GET"])
    def logout_view() -> Response:
        """Clear the active advisor session and redirect to the login screen.

        Summary:
            Terminates the current session and presents the persona switcher portal.

        Parameters:
            None

        Returns:
            Response: Redirect to `/login`.
        """
        session.clear()
        flash("Session locked. Select an institutional persona to resume.", "info")
        return redirect(url_for("login_view"))

    @app.route("/", methods=["GET"])
    def dashboard_view() -> str:
        """Render the Executive & Relationship Manager Command Center Dashboard.

        Summary:
            Displays total AUM, net liquidity inflows, credit pipeline volume,
            Chart.js institutional analytics, top UHNW clients, and urgent tasks.

        Parameters:
            None

        Returns:
            str: Rendered HTML for `dashboard.html`.
        """
        summary = get_dashboard_summary(active_cfg.database_path)
        return render_template(
            "dashboard.html",
            summary=summary,
            active_page="dashboard",
        )

    @app.route("/customers", methods=["GET"])
    def customers_view() -> str:
        """Render the Customer 360 Directory with instant search and compliance filters.

        Summary:
            Lists all institutional and private wealth clients filtered by search
            query, relationship tier, KYC status, or assigned Relationship Manager.

        Parameters:
            None (reads `q`, `tier`, `kyc`, and `rm` from query string).

        Returns:
            str: Rendered HTML for `customers.html`.
        """
        search_q = request.args.get("q", "").strip()
        tier_filter = request.args.get("tier", "").strip()
        kyc_filter = request.args.get("kyc", "").strip()
        rm_filter = request.args.get("rm", "").strip()

        customers = list_customers(
            active_cfg.database_path,
            search_query=search_q,
            tier_filter=tier_filter,
            kyc_filter=kyc_filter,
            rm_filter=rm_filter,
        )
        return render_template(
            "customers.html",
            customers=customers,
            filters={
                "q": search_q,
                "tier": tier_filter,
                "kyc": kyc_filter,
                "rm": rm_filter,
            },
            active_page="customers",
        )

    @app.route("/customers/new", methods=["POST"])
    def create_customer_action() -> Response:
        """Handle client onboarding form submission and create an initial funded account.

        Summary:
            Validates form fields, creates the client and opening account in SQLite,
            and redirects to the newly created Customer 360 profile.

        Parameters:
            None (reads onboarding fields from POST form).

        Returns:
            Response: Redirect to `customer_detail_view` or `customers_view` on error.
        """
        try:
            payload = {
                "full_name": request.form.get("full_name", ""),
                "company_name": request.form.get("company_name", ""),
                "segment": request.form.get("segment", "High-Net-Worth (HNW)"),
                "tier": request.form.get("tier", "Platinum Sovereign"),
                "email": request.form.get("email", ""),
                "phone": request.form.get("phone", ""),
                "city": request.form.get("city", "New York"),
                "country": request.form.get("country", "United States"),
                "kyc_status": request.form.get("kyc_status", "Verified"),
                "aml_risk_rating": request.form.get("aml_risk_rating", "Low"),
                "credit_score": int(request.form.get("credit_score", "790") or 790),
                "annual_income_usd": float(request.form.get("annual_income_usd", "750000") or 750000),
                "initial_deposit_usd": float(request.form.get("initial_deposit_usd", "500000") or 500000),
                "initial_account_type": request.form.get(
                    "initial_account_type", "Private Wealth Advisory Portfolio"
                ),
                "relationship_manager": request.form.get(
                    "relationship_manager", get_current_advisor()["full_name"]
                ),
            }
            new_customer = create_customer_with_initial_account(
                active_cfg.database_path, payload
            )
            logger.info(
                "Onboarded new client %s (%s)",
                new_customer.get("full_name"),
                new_customer.get("cif_number"),
            )
            flash(
                f"Client {new_customer['full_name']} ({new_customer['cif_number']}) onboarded with initial AUM of {format_currency(new_customer['total_aum_usd'])}.",
                "success",
            )
            return redirect(url_for("customer_detail_view", customer_id=new_customer["id"]))
        except (ValueError, KeyError) as exc:
            logger.warning("Client onboarding validation failed: %s", exc)
            flash(f"Onboarding validation error: {exc}", "danger")
            return redirect(url_for("customers_view"))

    @app.route("/customers/<int:customer_id>", methods=["GET"])
    def customer_detail_view(customer_id: int) -> Union[str, Response]:
        """Render the comprehensive Customer 360 Dossier for a specific client.

        Summary:
            Shows KYC/AML status, credit score gauge, multi-account holdings,
            asset allocation chart, loan facilities, interaction timeline, and
            AI Next-Best-Action recommendations.

        Parameters:
            customer_id (int): Primary key ID of the customer.

        Returns:
            Union[str, Response]: Rendered `customer_detail.html` or redirect if missing.
        """
        dossier = get_customer_360(active_cfg.database_path, customer_id)
        if dossier is None:
            flash(f"Client record #{customer_id} was not found.", "warning")
            return redirect(url_for("customers_view"))

        return render_template(
            "customer_detail.html",
            client=dossier,
            active_page="customers",
        )

    @app.route("/customers/<int:customer_id>/compliance", methods=["POST"])
    def update_customer_compliance_action(customer_id: int) -> Response:
        """Update a client's KYC verification status, AML risk rating, and service tier.

        Summary:
            Applies compliance changes submitted from the Customer 360 profile drawer
            and logs an audit trail interaction.

        Parameters:
            customer_id (int): Target customer ID.

        Returns:
            Response: Redirect back to the Customer 360 profile.
        """
        try:
            kyc_status = request.form.get("kyc_status", "Verified")
            aml_risk_rating = request.form.get("aml_risk_rating", "Low")
            tier = request.form.get("tier", "Platinum Sovereign")
            advisor_name = get_current_advisor()["full_name"]

            updated = update_customer_kyc_and_tier(
                active_cfg.database_path,
                customer_id=customer_id,
                kyc_status=kyc_status,
                aml_risk_rating=aml_risk_rating,
                tier=tier,
                advisor_name=advisor_name,
            )
            flash(
                f"Updated compliance & tier profile for {updated['full_name']} (KYC: {updated['kyc_status']}, AML Risk: {updated['aml_risk_rating']}).",
                "success",
            )
        except ValueError as exc:
            logger.warning("Compliance update failed for customer %d: %s", customer_id, exc)
            flash(str(exc), "danger")

        return redirect(url_for("customer_detail_view", customer_id=customer_id))

    @app.route("/accounts/new", methods=["POST"])
    def create_account_action() -> Response:
        """Open a new depository or wealth management account for a client.

        Summary:
            Validates account parameters, creates the account and initial funding
            entry, updates client AUM, and redirects back to the referring view.

        Parameters:
            None (reads `customer_id`, `account_type`, `currency`, `initial_balance_usd`, `apy_rate`).

        Returns:
            Response: Redirect to the Customer 360 view or Ledger view.
        """
        customer_id = int(request.form.get("customer_id", "0") or 0)
        try:
            payload = {
                "customer_id": customer_id,
                "account_type": request.form.get("account_type", "Commercial Treasury Sweep"),
                "currency": request.form.get("currency", "USD"),
                "initial_balance_usd": float(request.form.get("initial_balance_usd", "0") or 0.0),
                "apy_rate": float(request.form.get("apy_rate", "4.75") or 4.75),
            }
            acct = create_bank_account(active_cfg.database_path, payload)
            logger.info("Opened new account %s for customer %d", acct["account_number"], customer_id)
            flash(
                f"Opened {acct['account_type']} ({acct['account_number']}) with balance {format_currency(acct['balance_usd'])}.",
                "success",
            )
        except ValueError as exc:
            logger.warning("Failed to open bank account: %s", exc)
            flash(f"Account creation error: {exc}", "danger")

        if customer_id > 0 and "customers" in (request.referrer or ""):
            return redirect(url_for("customer_detail_view", customer_id=customer_id))
        return redirect(url_for("ledger_view"))

    @app.route("/loans", methods=["GET"])
    def loans_view() -> str:
        """Render the Commercial & Private Credit Origination Pipeline module.

        Summary:
            Displays the 5-stage credit underwriting Kanban board, detailed
            underwriting table, and interactive DTI/LTV credit structuring calculator.

        Parameters:
            None (reads optional `stage` query parameter).

        Returns:
            str: Rendered HTML for `loans.html`.
        """
        stage_filter = request.args.get("stage", "").strip()
        pipeline = list_loans_pipeline(active_cfg.database_path, stage_filter=stage_filter)
        return render_template(
            "loans.html",
            pipeline=pipeline,
            stage_filter=stage_filter,
            active_page="loans",
        )

    @app.route("/loans/new", methods=["POST"])
    def create_loan_action() -> Response:
        """Originate a new commercial or private wealth credit facility.

        Summary:
            Calculates LTV, DTI, and underwriting grade from the submitted loan
            parameters and adds the facility to the underwriting pipeline.

        Parameters:
            None (reads loan fields from POST form).

        Returns:
            Response: Redirect to `loans_view` or `customer_detail_view`.
        """
        customer_id = int(request.form.get("customer_id", "0") or 0)
        try:
            payload = {
                "customer_id": customer_id,
                "loan_type": request.form.get(
                    "loan_type", "Securities-Backed Line of Credit (SBLOC)"
                ),
                "purpose": request.form.get("purpose", ""),
                "principal_usd": float(request.form.get("principal_usd", "0") or 0.0),
                "interest_rate_pct": float(request.form.get("interest_rate_pct", "5.85") or 5.85),
                "term_months": int(request.form.get("term_months", "60") or 60),
                "collateral_value_usd": float(
                    request.form.get("collateral_value_usd", "0") or 0.0
                ),
                "assigned_underwriter": request.form.get(
                    "assigned_underwriter", get_current_advisor()["full_name"]
                ),
            }
            loan = create_loan_application(active_cfg.database_path, payload)
            logger.info(
                "Originated loan %s ($%.2f, LTV %.1f%%)",
                loan["loan_number"],
                float(loan["principal_usd"]),
                float(loan["ltv_ratio_pct"]),
            )
            flash(
                f"Originated facility {loan['loan_number']} ({format_currency(loan['principal_usd'])}) — "
                f"LTV: {loan['ltv_ratio_pct']}%, DTI: {loan['dti_ratio_pct']}%, Grade: {loan['underwriting_grade']}.",
                "success",
            )
        except ValueError as exc:
            logger.warning("Loan origination error: %s", exc)
            flash(f"Loan underwriting error: {exc}", "danger")

        if request.form.get("return_to_customer") == "1" and customer_id > 0:
            return redirect(url_for("customer_detail_view", customer_id=customer_id))
        return redirect(url_for("loans_view"))

    @app.route("/loans/<int:loan_id>/stage", methods=["POST"])
    def update_loan_stage_action(loan_id: int) -> Response:
        """Advance or reassign a loan facility's underwriting pipeline stage.

        Summary:
            Updates the stage of `loan_id` and records an underwriting audit entry.

        Parameters:
            loan_id (int): Target loan primary key ID.

        Returns:
            Response: Redirect to the referring page or `/loans`.
        """
        new_stage = request.form.get("stage", "Credit Committee Review").strip()
        try:
            loan = update_loan_stage(
                active_cfg.database_path,
                loan_id=loan_id,
                new_stage=new_stage,
                advisor_name=get_current_advisor()["full_name"],
            )
            flash(
                f"Facility {loan['loan_number']} transitioned to '{loan['stage']}'.",
                "success",
            )
        except ValueError as exc:
            logger.warning("Failed to update loan stage for %d: %s", loan_id, exc)
            flash(str(exc), "danger")

        return redirect(request.referrer or url_for("loans_view"))

    @app.route("/ledger", methods=["GET"])
    def ledger_view() -> str:
        """Render the Institutional Wire & Account Ledger module.

        Summary:
            Displays real-time account balances, credit/debit/transfer execution
            controls, and AML/OFAC compliance review queues.

        Parameters:
            None (reads `type`, `risk`, and `q` query parameters).

        Returns:
            str: Rendered HTML for `ledger.html`.
        """
        txn_type = request.args.get("type", "").strip()
        risk_only = request.args.get("risk", "").strip() == "1"
        search_q = request.args.get("q", "").strip()

        ledger_data = list_ledger_transactions(
            active_cfg.database_path,
            txn_type_filter=txn_type,
            risk_only=risk_only,
            search_query=search_q,
        )
        return render_template(
            "ledger.html",
            ledger=ledger_data,
            filters={"type": txn_type, "risk": "1" if risk_only else "", "q": search_q},
            active_page="ledger",
        )

    @app.route("/ledger/transaction", methods=["POST"])
    def execute_transaction_action() -> Response:
        """Execute a live wire transfer, deposit credit, or internal book transfer.

        Summary:
            Processes the submitted transaction atomically, applies AML risk rules,
            recalculates client AUM, and flashes confirmation or compliance hold alerts.

        Parameters:
            None (reads transaction fields from POST form).

        Returns:
            Response: Redirect to the referring page or `/ledger`.
        """
        try:
            payload = {
                "account_id": int(request.form.get("account_id", "0") or 0),
                "txn_type": request.form.get("txn_type", "Debit"),
                "channel": request.form.get("channel", "FEDWIRE"),
                "amount_usd": float(request.form.get("amount_usd", "0") or 0.0),
                "counterparty": request.form.get("counterparty", ""),
                "category": request.form.get("category", "Corporate Treasury Settlement"),
                "destination_account_id": int(
                    request.form.get("destination_account_id", "0") or 0
                ),
            }
            txn = execute_ledger_transaction(
                active_cfg.database_path,
                payload=payload,
                wire_threshold_usd=active_cfg.high_value_wire_threshold_usd,
            )
            logger.info(
                "Executed ledger transaction %s (%s $%.2f, risk_flag=%s)",
                txn["reference_id"],
                txn["txn_type"],
                float(txn["amount_usd"]),
                txn["risk_flag"],
            )
            if int(txn.get("risk_flag", 0)) == 1:
                flash(
                    f"Transaction {txn['reference_id']} ({format_currency(txn['amount_usd'])}) recorded with Compliance Hold: {txn['risk_reason']}.",
                    "warning",
                )
            else:
                flash(
                    f"Transaction {txn['reference_id']} ({txn['txn_type']} of {format_currency(txn['amount_usd'])}) settled immediately.",
                    "success",
                )
        except ValueError as exc:
            logger.warning("Ledger transaction rejected: %s", exc)
            flash(f"Transaction rejected: {exc}", "danger")

        return redirect(request.referrer or url_for("ledger_view"))

    @app.route("/ledger/<int:transaction_id>/resolve-risk", methods=["POST"])
    def resolve_risk_action(transaction_id: int) -> Response:
        """Approve and release an AML/Compliance hold on a flagged wire transaction.

        Summary:
            Clears `risk_flag` on the specified transaction and records the officer's
            dual-control sign-off on the client timeline.

        Parameters:
            transaction_id (int): Primary key ID of the flagged transaction.

        Returns:
            Response: Redirect back to the referring page or `/ledger`.
        """
        try:
            txn = resolve_transaction_risk_flag(
                active_cfg.database_path,
                transaction_id=transaction_id,
                advisor_name=get_current_advisor()["full_name"],
            )
            logger.info("Cleared compliance hold on transaction %s", txn["reference_id"])
            flash(
                f"Dual-Control Compliance Sign-off recorded for {txn['reference_id']}. Status updated to Settled.",
                "success",
            )
        except ValueError as exc:
            logger.warning("Failed to resolve risk flag on %d: %s", transaction_id, exc)
            flash(str(exc), "danger")

        return redirect(request.referrer or url_for("ledger_view"))

    @app.route("/tasks", methods=["GET"])
    def tasks_view() -> str:
        """Render the Client Tasks, Touchpoints & Next-Best-Action Offers workspace.

        Summary:
            Displays advisor action items, client interaction logs, and AI-driven
            wealth/treasury product recommendations with match scores.

        Parameters:
            None (reads optional `status` query parameter).

        Returns:
            str: Rendered HTML for `tasks.html`.
        """
        status_filter = request.args.get("status", "").strip()
        workspace_data = list_tasks_and_offers(
            active_cfg.database_path, status_filter=status_filter
        )
        return render_template(
            "tasks.html",
            workspace=workspace_data,
            status_filter=status_filter,
            active_page="tasks",
        )

    @app.route("/tasks/new", methods=["POST"])
    def create_task_action() -> Response:
        """Create a new client follow-up task or compliance deliverable.

        Summary:
            Validates and stores a new task linked to a customer and advisor.

        Parameters:
            None (reads task fields from POST form).

        Returns:
            Response: Redirect back to the referring page or `/tasks`.
        """
        try:
            payload = {
                "customer_id": int(request.form.get("customer_id", "0") or 0),
                "title": request.form.get("title", ""),
                "category": request.form.get("category", "Portfolio Management"),
                "priority": request.form.get("priority", "High"),
                "due_date": request.form.get("due_date", ""),
                "assigned_to": request.form.get(
                    "assigned_to", get_current_advisor()["full_name"]
                ),
                "notes": request.form.get("notes", ""),
            }
            task = create_advisor_task(active_cfg.database_path, payload)
            flash(f"Scheduled task '{task['title']}' ({task['priority']} priority).", "success")
        except ValueError as exc:
            flash(f"Task creation error: {exc}", "danger")

        return redirect(request.referrer or url_for("tasks_view"))

    @app.route("/tasks/<int:task_id>/toggle", methods=["POST"])
    def toggle_task_action(task_id: int) -> Response:
        """Toggle a task between Pending and Completed states.

        Summary:
            Flips task status in SQLite and redirects to the active view.

        Parameters:
            task_id (int): Primary key ID of the task.

        Returns:
            Response: Redirect to the referring page.
        """
        try:
            updated = toggle_task_status(active_cfg.database_path, task_id)
            flash(
                f"Task '{updated['title']}' marked as {updated['status']}.",
                "success",
            )
        except ValueError as exc:
            flash(str(exc), "danger")

        return redirect(request.referrer or url_for("tasks_view"))

    @app.route("/interactions/new", methods=["POST"])
    def create_interaction_action() -> Response:
        """Log a new advisor-client call, meeting briefing, or compliance touchpoint.

        Summary:
            Inserts a timestamped interaction note and updates the client's last
            contact date.

        Parameters:
            None (reads interaction fields from POST form).

        Returns:
            Response: Redirect back to the referring page.
        """
        try:
            payload = {
                "customer_id": int(request.form.get("customer_id", "0") or 0),
                "channel": request.form.get("channel", "In-Person Executive Briefing"),
                "subject": request.form.get("subject", ""),
                "sentiment": request.form.get("sentiment", "Positive"),
                "summary": request.form.get("summary", ""),
                "advisor_name": get_current_advisor()["full_name"],
            }
            interaction = create_client_interaction(active_cfg.database_path, payload)
            flash(
                f"Logged {interaction['channel']} touchpoint: '{interaction['subject']}'.",
                "success",
            )
        except ValueError as exc:
            flash(f"Interaction log error: {exc}", "danger")

        return redirect(request.referrer or url_for("tasks_view"))

    @app.route("/nba/<int:offer_id>/status", methods=["POST"])
    def update_nba_status_action(offer_id: int) -> Response:
        """Update a Next-Best-Action product recommendation status and log the pitch.

        Summary:
            Transitions an NBA offer to 'Pitched — Client Reviewing' or
            'Accepted & Enrolled' and logs an advisory interaction.

        Parameters:
            offer_id (int): Primary key ID of the NBA offer.

        Returns:
            Response: Redirect to the referring page.
        """
        new_status = request.form.get("status", "Pitched — Client Reviewing")
        try:
            offer = update_nba_offer_status(
                active_cfg.database_path,
                offer_id=offer_id,
                new_status=new_status,
                advisor_name=get_current_advisor()["full_name"],
            )
            flash(
                f"Updated Next-Best-Action '{offer['product_name']}' to '{offer['status']}'.",
                "success",
            )
        except ValueError as exc:
            flash(str(exc), "danger")

        return redirect(request.referrer or url_for("tasks_view"))

    @app.route("/admin/reset-demo", methods=["POST"])
    def reset_demo_action() -> Response:
        """Reset the SQLite database and re-seed the canonical institutional dataset.

        Summary:
            Restores all 8 clients, 17 accounts, 10 transactions, 7 loans, and
            advisor workflows to their clean initial state.

        Parameters:
            None

        Returns:
            Response: Redirect to the Executive Dashboard.
        """
        counts = reset_and_seed_database(active_cfg.database_path)
        logger.info("Demo database reset and re-seeded: %s", counts)
        flash(
            f"Demo database reset complete: restored {counts['customers']} clients, "
            f"{counts['accounts']} accounts, {counts['loans']} credit facilities, and "
            f"{counts['transactions']} ledger entries.",
            "info",
        )
        return redirect(url_for("dashboard_view"))

    @app.route("/api/health", methods=["GET"])
    def api_health() -> Tuple[Response, int]:
        """Provide a JSON readiness and liveness probe for Cloud Run and GKE.

        Summary:
            Verifies SQLite connectivity and returns service health telemetry.

        Parameters:
            None

        Returns:
            Tuple[Response, int]: JSON status response and HTTP 200 code.
        """
        summary = get_dashboard_summary(active_cfg.database_path)
        return (
            jsonify(
                {
                    "status": "healthy",
                    "service": active_cfg.bank_name,
                    "clients_count": summary["kpis"]["client_count"],
                    "total_aum_usd": summary["kpis"]["total_aum_usd"],
                }
            ),
            200,
        )

    return app


app = create_app()

if __name__ == "__main__":
    runtime_cfg = app.config["APP_CONFIG"]
    logger.info(
        "Starting %s on http://%s:%d (env=%s)",
        runtime_cfg.bank_name,
        runtime_cfg.host,
        runtime_cfg.port,
        runtime_cfg.flask_env,
    )
    app.run(
        host=runtime_cfg.host,
        port=runtime_cfg.port,
        debug=runtime_cfg.flask_debug,
    )
