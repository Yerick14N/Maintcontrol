
from flask import Flask, render_template, request, redirect, url_for, session, send_file, jsonify, flash
from datetime import datetime, timedelta
import sqlite3
import re
import os
import csv
import io
import base64
import smtplib
import ssl
from email.message import EmailMessage
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from ai.scheduler import suggest_priorities
import secrets
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(__file__)
DATABASE = os.path.join(BASE_DIR, "maintcontrol.db")
TRIAL_DAYS = 30
SUPPORTED_LANGS = ["fr", "en", "es", "de"]
DEFAULT_LANG = "fr"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "CHANGE_THIS_SECRET_KEY")

# ---------- DB helpers ----------

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    if os.path.exists(DATABASE):
        return
    conn = get_db()
    c = conn.cursor()

    # Companies (multi-entreprises)
    c.execute("""
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            domain TEXT,
            created_at TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            plan_name TEXT,
            plan_price REAL
        )
    """)

    # Users
    c.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL, -- super_admin (éditeur), admin (entreprise), owner (patron), manager (chef), tech/employee, client
            company_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            trial_start TEXT,
            is_activated INTEGER DEFAULT 0,
            license_key TEXT,
            FOREIGN KEY(company_id) REFERENCES companies(id)
        )
    """)

    # Customers (clients réels des chantiers)
    c.execute("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            address TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(company_id) REFERENCES companies(id)
        )
    """)

    # Interventions
    c.execute("""
        CREATE TABLE interventions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            customer_id INTEGER,
            title TEXT NOT NULL,
            description TEXT,
            client_name TEXT,
            technician_name TEXT,
            status TEXT NOT NULL,
            priority TEXT NOT NULL,
            kind TEXT,
            category TEXT,
            scheduled_date TEXT,
            tech_notes TEXT,
            time_spent_minutes INTEGER DEFAULT 0,
            started_at TEXT,
            completed_at TEXT,
            tech_updated_at TEXT,
            client_signature_path TEXT,
            client_signed_at TEXT,
            created_at TEXT NOT NULL,
            created_by INTEGER,
            FOREIGN KEY(company_id) REFERENCES companies(id),
            FOREIGN KEY(customer_id) REFERENCES customers(id),
            FOREIGN KEY(created_by) REFERENCES users(id)
        )
    """)

    



    # Intervention assignees (many-to-many: une intervention peut avoir plusieurs salariés)
    c.execute("""
    CREATE TABLE intervention_assignees (
        intervention_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        company_id INTEGER NOT NULL,
        PRIMARY KEY (intervention_id, user_id),
        FOREIGN KEY(intervention_id) REFERENCES interventions(id) ON DELETE CASCADE,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
    )
    """)

    # License keys (par entreprise)
    c.execute("""
        CREATE TABLE license_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            key TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            created_by INTEGER,
            assigned_to INTEGER,
            used INTEGER DEFAULT 0,
            FOREIGN KEY(company_id) REFERENCES companies(id),
            FOREIGN KEY(created_by) REFERENCES users(id),
            FOREIGN KEY(assigned_to) REFERENCES users(id)
        )
    """)

    # Invoices (facturation des licences / abonnements)
    c.execute("""
        CREATE TABLE invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            user_id INTEGER,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            description TEXT,
            period_start TEXT,
            period_end TEXT,
            status TEXT NOT NULL, -- draft, pending, paid, cancelled
            created_at TEXT NOT NULL,
            paid_at TEXT,
            FOREIGN KEY(company_id) REFERENCES companies(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # Payments (système de paiement simulé)
    c.execute("""
        CREATE TABLE payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            status TEXT NOT NULL, -- pending, succeeded, failed
            created_at TEXT NOT NULL,
            FOREIGN KEY(invoice_id) REFERENCES invoices(id)
        )
    """)

    now = datetime.utcnow().isoformat()

    # Company de démo
    c.execute("""
        INSERT INTO companies (name, domain, created_at, is_active, plan_name, plan_price)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("DemoCompany", None, now, 1, "Essai", 0.0))
    company_id = c.lastrowid

    # Admin déjà activé pour cette entreprise
    admin_pw = generate_password_hash("admin")
    c.execute("""
        INSERT INTO users (username, password, role, company_id, created_at, trial_start, is_activated)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ("admin", admin_pw, "admin", company_id, now, now, 1))


    # Patron (owner) de démo pour cette entreprise
    owner_pw = generate_password_hash("patron")
    c.execute("""
        INSERT INTO users (username, password, role, company_id, created_at, trial_start, is_activated)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ("patron", owner_pw, "owner", company_id, now, now, 1))

    # Quelques utilisateurs de démo (tech / client) dans la même entreprise
    for i in range(1, 3):
        demo_pw = generate_password_hash("password")
        c.execute("""
            INSERT INTO users (username, password, role, company_id, created_at, trial_start, is_activated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (f"tech{i}", demo_pw, "tech", company_id, now, now, 0))

    for i in range(1, 3):
        demo_pw = generate_password_hash("password")
        c.execute("""
            INSERT INTO users (username, password, role, company_id, created_at, trial_start, is_activated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (f"client{i}", demo_pw, "client", company_id, now, now, 0))

    conn.commit()
    conn.close()


def migrate_db():
    """Add new columns/tables safely when upgrading."""
    conn = get_db()
    c = conn.cursor()

    # intervention_files table
    c.execute("""
        CREATE TABLE IF NOT EXISTS intervention_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            intervention_id INTEGER NOT NULL,
            company_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            filepath TEXT NOT NULL,
            uploaded_by INTEGER,
            uploaded_at TEXT NOT NULL,
            FOREIGN KEY(intervention_id) REFERENCES interventions(id),
            FOREIGN KEY(company_id) REFERENCES companies(id),
            FOREIGN KEY(uploaded_by) REFERENCES users(id)
        )
    """)


    # intervention_assignees table (multi-salariés)
    c.execute("""
        CREATE TABLE IF NOT EXISTS intervention_assignees (
            intervention_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            company_id INTEGER NOT NULL,
            PRIMARY KEY (intervention_id, user_id)
        )
    """)

    # login_attempts table
    c.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            ip TEXT,
            fail_count INTEGER DEFAULT 0,
            last_fail_at TEXT,
            locked_until TEXT
        )
    """)

    # interventions extra columns
    c.execute("PRAGMA table_info(interventions)")
    cols = {row[1] for row in c.fetchall()}
    def add_col(name, ddl):
        if name not in cols:
            c.execute(ddl)
    add_col("tech_notes", "ALTER TABLE interventions ADD COLUMN tech_notes TEXT")
    add_col("time_spent_minutes", "ALTER TABLE interventions ADD COLUMN time_spent_minutes INTEGER DEFAULT 0")
    add_col("started_at", "ALTER TABLE interventions ADD COLUMN started_at TEXT")
    add_col("completed_at", "ALTER TABLE interventions ADD COLUMN completed_at TEXT")
    add_col("tech_updated_at", "ALTER TABLE interventions ADD COLUMN tech_updated_at TEXT")
    add_col("client_signature_path", "ALTER TABLE interventions ADD COLUMN client_signature_path TEXT")
    add_col("client_signed_at", "ALTER TABLE interventions ADD COLUMN client_signed_at TEXT")

    conn.commit()
    conn.close()


def ensure_admin_credentials():
    """Use ADMIN_USERNAME / ADMIN_PASSWORD env vars to secure admin login."""
    admin_user = os.environ.get("ADMIN_USERNAME", "").strip()
    admin_pass = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not admin_user and not admin_pass:
        return
    if admin_pass and len(admin_pass) < 12:
        print("[MaintControl] ADMIN_PASSWORD too short (min 12). Ignored.")
        return

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE role='admin' ORDER BY id LIMIT 1")
    u = c.fetchone()
    if not u:
        username = admin_user or "admin"
        pw = admin_pass or secrets.token_urlsafe(24)
        now = datetime.utcnow().isoformat()
        # create default company if needed
        c.execute("SELECT id FROM companies ORDER BY id LIMIT 1")
        row = c.fetchone()
        company_id = row["id"] if row else 1
        c.execute("""
            INSERT INTO users (username, password, role, company_id, created_at, trial_start, is_activated)
            VALUES (?, ?, 'admin', ?, ?, ?, 1)
        """, (username, generate_password_hash(pw), company_id, now, now))
    else:
        username = admin_user or u["username"]
        pw_hash = generate_password_hash(admin_pass) if admin_pass else u["password"]
        c.execute("UPDATE users SET username=?, password=? WHERE id=?", (username, pw_hash, u["id"]))
    conn.commit()
    conn.close()


# Init DB at import (Flask 3 compatible)
init_db()
migrate_db()
ensure_admin_credentials()

# ---------- helpers ----------

def get_current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE id = ?", (uid,))
    row = c.fetchone()
    conn.close()
    return row

def get_current_company(user=None):
    if user is None:
        user = get_current_user()
    if not user:
        return None
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM companies WHERE id = ?", (user["company_id"],))
    company = c.fetchone()
    conn.close()
    return company

def is_trial_expired(user):
    if user is None:
        return False
    if user["role"] == "admin":
        return False
    if user["is_activated"]:
        return False
    trial_start = user["trial_start"]
    if not trial_start:
        return True
    start = datetime.fromisoformat(trial_start)
    return datetime.utcnow() > start + timedelta(days=TRIAL_DAYS)

def remaining_trial_days(user):
    if user is None or user["role"] == "admin" or user["is_activated"] or not user["trial_start"]:
        return None
    start = datetime.fromisoformat(user["trial_start"])
    remaining = (start + timedelta(days=TRIAL_DAYS) - datetime.utcnow()).days
    return max(0, remaining)

def require_login(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

@app.context_processor
def inject_globals():
    user = get_current_user()
    lang = session.get("lang", DEFAULT_LANG)
    return {
        "current_user": user,
        "current_company": get_current_company(user) if user else None,
        "trial_days_left": remaining_trial_days(user) if user else None,
        "is_trial_expired": is_trial_expired(user) if user else False,
        "lang": lang,
        "supported_langs": SUPPORTED_LANGS,
    }

# ---------- routes auth / langue ----------

@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/set_lang/<lang_code>")
def set_lang(lang_code):
    if lang_code not in SUPPORTED_LANGS:
        lang_code = DEFAULT_LANG
    session["lang"] = lang_code
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
        now = datetime.utcnow()

        conn = get_db()
        c = conn.cursor()

        # Lockout check
        c.execute("SELECT * FROM login_attempts WHERE username=? AND ip=? ORDER BY id DESC LIMIT 1", (username, ip))
        la = c.fetchone()
        if la and la["locked_until"]:
            try:
                until = datetime.fromisoformat(la["locked_until"])
                if until > now:
                    conn.close()
                    flash("Trop de tentatives. Réessayez plus tard.", "error")
                    return render_template("login.html")
            except Exception:
                pass

        c.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = c.fetchone()

        ok = bool(user and check_password_hash(user["password"], password))
        if ok:
            c.execute("INSERT INTO login_attempts (username, ip, fail_count, last_fail_at, locked_until) VALUES (?, ?, 0, ?, NULL)",
                      (username, ip, now.isoformat()))
            conn.commit()
            conn.close()
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))

        # failure update
        fail_count = 1
        if la and la["fail_count"] is not None and la["last_fail_at"]:
            try:
                last = datetime.fromisoformat(la["last_fail_at"])
                if (now - last).total_seconds() <= 600:
                    fail_count = int(la["fail_count"]) + 1
            except Exception:
                pass

        locked_until = None
        if fail_count >= 5:
            locked_until = (now + timedelta(minutes=15)).isoformat()

        c.execute("INSERT INTO login_attempts (username, ip, fail_count, last_fail_at, locked_until) VALUES (?, ?, ?, ?, ?)",
                  (username, ip, fail_count, now.isoformat(), locked_until))
        conn.commit()
        conn.close()
        flash("Identifiants invalides", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------- Dashboard ----------

@app.route("/dashboard")
@require_login
def dashboard():
    user = get_current_user()
    company = get_current_company(user)
    conn = get_db()
    c = conn.cursor()

    # Latest interventions shown on dashboard
    if user["role"] in ("tech", "employee"):
        c.execute("""
            SELECT i.*
            FROM interventions i
            JOIN intervention_assignees ia ON ia.intervention_id = i.id
            WHERE i.company_id = ? AND ia.user_id = ?
            ORDER BY i.created_at DESC
            LIMIT 10
        """, (company["id"], user["id"]))
        interventions = c.fetchall()
    elif user["role"] == "client":
        c.execute("""
            SELECT * FROM interventions
            WHERE company_id = ? AND client_name = ?
            ORDER BY created_at DESC
            LIMIT 10
        """, (company["id"], user["id"]))
        interventions = c.fetchall()
    else:
        c.execute("""
            SELECT * FROM interventions
            WHERE company_id = ?
            ORDER BY created_at DESC
            LIMIT 10
        """, (company["id"],))
        interventions = c.fetchall()

    # KPI
    def count_where(extra=""):
        q = "SELECT COUNT(*) AS n FROM interventions WHERE company_id = ? " + extra
        cp = [company["id"]]
        c.execute(q, tuple(cp))
        return c.fetchone()["n"]

    total = count_where("")
    open_count = count_where("AND status = 'open'")
    in_progress = count_where("AND status = 'in_progress'")
    done_count = count_where("AND status = 'done'")

    now_iso = datetime.utcnow().isoformat()
    c.execute("""
        SELECT COUNT(*) AS n
        FROM interventions
        WHERE company_id = ?
          AND scheduled_date IS NOT NULL
          AND scheduled_date != ''
          AND scheduled_date < ?
          AND status != 'done'
    """, (company["id"], now_iso))
    late_count = c.fetchone()["n"]

    if user["role"] == "tech":
        c.execute("""
            SELECT COUNT(*) AS n
            FROM interventions
            WHERE company_id = ? AND technician_name = ? AND status != 'done'
        """, (company["id"], user["id"]))
        my_open = c.fetchone()["n"]
    elif user["role"] == "client":
        c.execute("""
            SELECT COUNT(*) AS n
            FROM interventions
            WHERE company_id = ? AND client_name = ? AND status != 'done'
        """, (company["id"], user["id"]))
        my_open = c.fetchone()["n"]
    else:
        my_open = open_count

    conn.close()

    suggestions = suggest_priorities([dict(row) for row in interventions])
    trial_left = remaining_trial_days(user)

    return render_template(
        "dashboard.html",
        interventions=interventions,
        suggestions=suggestions,
        trial_left=trial_left,
        total_interventions=total,
        open_count=open_count,
        in_progress=in_progress,
        done_count=done_count,
        late_count=late_count,
        my_open=my_open,
    )

# ---------- Customers (clients réels) ----------

@app.route("/customers", methods=["GET", "POST"])
@require_login
def customers():
    user = get_current_user()
    company = get_current_company(user)

    if request.method == "POST":
        if user["role"] not in ("admin","owner","manager"):
            return "Forbidden", 403
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        address = (request.form.get("address") or "").strip()
        if not name:
            flash("Le nom du client est obligatoire.", "error")
        else:
            conn = get_db()
            c = conn.cursor()
            c.execute("""
                INSERT INTO customers (company_id, name, email, phone, address, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (company["id"], name, email, phone, address, datetime.utcnow().isoformat()))
            conn.commit()
            conn.close()
            flash("Client créé.", "success")

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM customers WHERE company_id = ? ORDER BY created_at DESC", (company["id"],))
    customers = c.fetchall()
    conn.close()
    return render_template("customers.html", customers=customers)

# ---------- Interventions ----------

@app.route("/interventions")
@require_login
def list_interventions():
    user = get_current_user()
    company = get_current_company(user)
    if user["role"] in ("tech","employee"):
        return redirect(url_for("tech_interventions"))
    conn = get_db()
    c = conn.cursor()

    base_query = "SELECT i.*, cu.name AS customer_name FROM interventions i LEFT JOIN customers cu ON i.customer_id = cu.id WHERE i.company_id = ?"
    params = [company["id"]]

    if user["role"] == "tech":
        base_query += " AND i.technician_name = ?"
        params.append(user["username"])
    elif user["role"] == "client":
        base_query += " AND i.client_name = ?"
        params.append(user["username"])

    status = request.args.get("status", "").strip()
    priority = request.args.get("priority", "").strip()
    kind = request.args.get("kind", "").strip()
    category = request.args.get("category", "").strip()

    if status:
        base_query += " AND i.status = ?"
        params.append(status)
    if priority:
        base_query += " AND i.priority = ?"
        params.append(priority)
    if kind:
        base_query += " AND i.kind = ?"
        params.append(kind)
    if category:
        base_query += " AND i.category = ?"
        params.append(category)

    base_query += " ORDER BY i.created_at DESC"
    c.execute(base_query, tuple(params))
    interventions = c.fetchall()

    # customers for filter info maybe
    c.execute("SELECT * FROM customers WHERE company_id = ? ORDER BY name", (company["id"],))
    customers = c.fetchall()

    conn.close()
    return render_template("interventions.html", interventions=interventions, customers=customers)

@app.route("/interventions/new", methods=["GET", "POST"])
@require_login
def new_intervention():
    user = get_current_user()
    company = get_current_company(user)
    if user["role"] not in ("admin","owner","manager"):
        return "Forbidden", 403

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM customers WHERE company_id = ? ORDER BY name", (company["id"],))
    customers = c.fetchall()
    c.execute("SELECT id, username, role FROM users WHERE company_id = ? AND role IN ('tech','employee','manager') ORDER BY username", (company["id"],))
    employees = c.fetchall()

    if request.method == "POST":
        title = request.form.get("title")
        description = request.form.get("description")
        customer_id = request.form.get("customer_id") or None
        client_name = request.form.get("client_name") or ""
        status = request.form.get("status") or "open"
        priority = request.form.get("priority") or "medium"
        kind = request.form.get("kind") or ""
        category = request.form.get("category") or ""
        scheduled_date = request.form.get("scheduled_date") or None

        # multi-assignees
        assignee_ids = request.form.getlist("assignees")
        assignee_ids_int = []
        for v in assignee_ids:
            try:
                assignee_ids_int.append(int(v))
            except Exception:
                pass

        # if customer selected, auto-fill client_name with customer name
        if customer_id:
            try:
                cid_int = int(customer_id)
            except Exception:
                cid_int = None
            if cid_int:
                c.execute("SELECT name FROM customers WHERE id = ? AND company_id = ?", (cid_int, company["id"]))
                row = c.fetchone()
                if row:
                    client_name = row["name"]
                customer_id = cid_int
            else:
                customer_id = None

        # resolve usernames for display/exports (kept for backward compat)
        technician_name = ""
        if assignee_ids_int:
            qmarks = ",".join(["?"] * len(assignee_ids_int))
            c.execute(f"SELECT id, username FROM users WHERE company_id = ? AND id IN ({qmarks})", tuple([company["id"]] + assignee_ids_int))
            rows = c.fetchall()
            id_to_name = {r["id"]: r["username"] for r in rows}
            technician_name = ", ".join([id_to_name.get(i) for i in assignee_ids_int if id_to_name.get(i)])

        c.execute("""
            INSERT INTO interventions
            (company_id, customer_id, title, description, client_name, technician_name, status, priority, kind, category, scheduled_date, created_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (company["id"], customer_id, title, description, client_name, technician_name, status, priority, kind, category, scheduled_date, datetime.utcnow().isoformat(), user["id"]))

        intervention_id = c.lastrowid

        # save pivot rows
        c.execute("DELETE FROM intervention_assignees WHERE intervention_id = ? AND company_id = ?", (intervention_id, company["id"]))
        for uid in assignee_ids_int:
            c.execute(
                "INSERT OR IGNORE INTO intervention_assignees (intervention_id, user_id, company_id) VALUES (?, ?, ?)",
                (intervention_id, uid, company["id"])
            )

        conn.commit()
        conn.close()
        return redirect(url_for("list_interventions"))

    conn.close()
    return render_template("intervention_form.html", intervention=None, customers=customers, employees=employees, selected_assignees=[])

@app.route("/interventions/<int:intervention_id>/edit", methods=["GET", "POST"])
@require_login
def edit_intervention(intervention_id):
    user = get_current_user()
    company = get_current_company(user)
    if user["role"] not in ("admin","owner","manager"):
        return "Forbidden", 403

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM interventions WHERE id=? AND company_id=?", (intervention_id, company["id"]))
    intervention = c.fetchone()
    if not intervention:
        conn.close()
        flash("Intervention introuvable.", "error")
        return redirect(url_for("list_interventions"))

    c.execute("SELECT * FROM customers WHERE company_id = ? ORDER BY name", (company["id"],))
    customers = c.fetchall()
    c.execute("SELECT id, username, role FROM users WHERE company_id = ? AND role IN ('tech','employee','manager') ORDER BY username", (company["id"],))
    employees = c.fetchall()
    c.execute("SELECT user_id FROM intervention_assignees WHERE intervention_id = ? AND company_id = ?", (intervention_id, company["id"]))
    selected_assignees = [r["user_id"] for r in c.fetchall()]

    if request.method == "POST":
        action = request.form.get("action", "save")

        if action == "delete":
            c.execute("DELETE FROM interventions WHERE id = ? AND company_id = ?", (intervention_id, company["id"]))
            c.execute("DELETE FROM intervention_assignees WHERE intervention_id = ? AND company_id = ?", (intervention_id, company["id"]))
            conn.commit()
            conn.close()
            flash("Intervention supprimée.", "success")
            return redirect(url_for("list_interventions"))

        title = request.form.get("title")
        description = request.form.get("description")
        customer_id = request.form.get("customer_id") or None
        client_name = request.form.get("client_name") or ""
        status = request.form.get("status") or intervention["status"]
        priority = request.form.get("priority") or intervention["priority"]
        kind = request.form.get("kind") or ""
        category = request.form.get("category") or ""
        scheduled_date = request.form.get("scheduled_date") or None

        assignee_ids = request.form.getlist("assignees")
        assignee_ids_int = []
        for v in assignee_ids:
            try:
                assignee_ids_int.append(int(v))
            except Exception:
                pass

        if customer_id:
            try:
                cid_int = int(customer_id)
            except Exception:
                cid_int = None
            if cid_int:
                c.execute("SELECT name FROM customers WHERE id = ? AND company_id = ?", (cid_int, company["id"]))
                row = c.fetchone()
                if row:
                    client_name = row["name"]
                customer_id = cid_int
            else:
                customer_id = None

        technician_name = ""
        if assignee_ids_int:
            qmarks = ",".join(["?"] * len(assignee_ids_int))
            c.execute(f"SELECT id, username FROM users WHERE company_id = ? AND id IN ({qmarks})", tuple([company["id"]] + assignee_ids_int))
            rows = c.fetchall()
            id_to_name = {r["id"]: r["username"] for r in rows}
            technician_name = ", ".join([id_to_name.get(i) for i in assignee_ids_int if id_to_name.get(i)])

        c.execute("""
            UPDATE interventions
            SET customer_id=?, title=?, description=?, client_name=?, technician_name=?, status=?, priority=?, kind=?, category=?, scheduled_date=?
            WHERE id=? AND company_id=?
        """, (customer_id, title, description, client_name, technician_name, status, priority, kind, category, scheduled_date, intervention_id, company["id"]))

        c.execute("DELETE FROM intervention_assignees WHERE intervention_id = ? AND company_id = ?", (intervention_id, company["id"]))
        for uid in assignee_ids_int:
            c.execute(
                "INSERT OR IGNORE INTO intervention_assignees (intervention_id, user_id, company_id) VALUES (?, ?, ?)",
                (intervention_id, uid, company["id"])
            )

        conn.commit()
        conn.close()
        flash("Intervention mise à jour.", "success")
        return redirect(url_for("list_interventions"))

    conn.close()
    return render_template("intervention_form.html", intervention=intervention, customers=customers, employees=employees, selected_assignees=selected_assignees)

@app.route("/tech/interventions")
@require_login
def tech_interventions():
    user = get_current_user()
    if user["role"] not in ("tech","employee"):
        return redirect(url_for("dashboard"))
    company = get_current_company(user)

    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT i.* FROM interventions i JOIN intervention_assignees ia ON ia.intervention_id = i.id WHERE i.company_id = ? AND ia.user_id = ? ORDER BY
          CASE WHEN status='done' THEN 1 ELSE 0 END,
          scheduled_date IS NULL,
          scheduled_date ASC,
          created_at DESC
    """, (company["id"], user["id"]))
    items = c.fetchall()
    conn.close()
    return render_template("tech_interventions.html", interventions=items)

@app.route("/tech/interventions/<int:intervention_id>", methods=["GET", "POST"])
@require_login
def tech_intervention_detail(intervention_id):
    user = get_current_user()
    if user["role"] not in ("tech","employee"):
        return redirect(url_for("dashboard"))
    company = get_current_company(user)

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT i.* FROM interventions i JOIN intervention_assignees ia ON ia.intervention_id=i.id WHERE i.id=? AND i.company_id=? AND ia.user_id=?", (intervention_id, company["id"], user["id"]))
    it = c.fetchone()
    if not it:
        conn.close()
        flash("Intervention introuvable.", "error")
        return redirect(url_for("tech_interventions"))

    # files
    c.execute("SELECT * FROM intervention_files WHERE intervention_id=? AND company_id=? ORDER BY uploaded_at DESC",
              (intervention_id, company["id"]))
    files = c.fetchall()

    if request.method == "POST":
        status = (request.form.get("status") or it["status"]).strip()
        note = (request.form.get("tech_notes") or "").strip()
        try:
            mins = int(request.form.get("time_spent_minutes") or (it["time_spent_minutes"] or 0))
        except Exception:
            mins = int(it["time_spent_minutes"] or 0)

        now = datetime.utcnow().isoformat()
        started_at = it["started_at"]
        completed_at = it["completed_at"]

        if status == "in_progress" and not started_at:
            started_at = now
        if status == "done" and not completed_at:
            completed_at = now

        c.execute("""
            UPDATE interventions
            SET status=?, tech_notes=?, time_spent_minutes=?, started_at=?, completed_at=?, tech_updated_at=?
            WHERE id=?
        """, (status, note, mins, started_at, completed_at, now, intervention_id))
        conn.commit()
        conn.close()
        flash("Mise à jour enregistrée.", "success")
        return redirect(url_for("tech_intervention_detail", intervention_id=intervention_id))

    conn.close()
    return render_template("tech_intervention_detail.html", intervention=it, files=files)

@app.route("/tech/interventions/<int:intervention_id>/upload", methods=["POST"])
@require_login
def tech_upload_proof(intervention_id):
    user = get_current_user()
    if user["role"] not in ("tech","employee"):
        return "Forbidden", 403
    company = get_current_company(user)

    f = request.files.get("file")
    if not f or not f.filename:
        flash("Fichier manquant.", "error")
        return redirect(url_for("tech_intervention_detail", intervention_id=intervention_id))

    # ensure intervention belongs to tech
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT i.* FROM interventions i JOIN intervention_assignees ia ON ia.intervention_id=i.id WHERE i.id=? AND i.company_id=? AND ia.user_id=?", (intervention_id, company["id"], user["id"]))
    it = c.fetchone()
    if not it:
        conn.close()
        return "Forbidden", 403

    safe_name = f"{company['id']}_{intervention_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}_{os.path.basename(f.filename)}"
    path = os.path.join(_uploads_dir(), safe_name)
    f.save(path)

    c.execute("""
        INSERT INTO intervention_files (intervention_id, company_id, filename, filepath, uploaded_by, uploaded_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (intervention_id, company["id"], os.path.basename(f.filename), path, user["id"], datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    flash("Preuve ajoutée.", "success")
    return redirect(url_for("tech_intervention_detail", intervention_id=intervention_id))

@app.route("/uploads/<path:filename>")
@require_login
def serve_upload(filename):
    # Basic protection: only logged in users can access; further checks can be added
    from flask import send_from_directory
    return send_from_directory(_uploads_dir(), filename)

@app.route("/tech/interventions/<int:intervention_id>/sign", methods=["POST"])
@require_login
def tech_save_signature(intervention_id):
    user = get_current_user()
    if user["role"] not in ("tech","employee"):
        return "Forbidden", 403
    company = get_current_company(user)
    sig = (request.form.get("signature_data") or "").strip()

    if not sig.startswith("data:image/png;base64,"):
        flash("Signature invalide.", "error")
        return redirect(url_for("tech_intervention_detail", intervention_id=intervention_id))

    b64 = sig.split(",", 1)[1]
    data = base64.b64decode(b64)

    fname = f"sig_{company['id']}_{intervention_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.png"
    fpath = os.path.join(_uploads_dir(), fname)
    with open(fpath, "wb") as fp:
        fp.write(data)

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT i.* FROM interventions i JOIN intervention_assignees ia ON ia.intervention_id=i.id WHERE i.id=? AND i.company_id=? AND ia.user_id=?", (intervention_id, company["id"], user["id"]))
    it = c.fetchone()
    if not it:
        conn.close()
        return "Forbidden", 403

    now = datetime.utcnow().isoformat()
    c.execute("UPDATE interventions SET client_signature_path=?, client_signed_at=? WHERE id=?",
              (fpath, now, intervention_id))
    conn.commit()
    conn.close()
    flash("Signature enregistrée.", "success")
    return redirect(url_for("tech_intervention_detail", intervention_id=intervention_id))

@app.route("/tech/interventions/<int:intervention_id>/report.pdf")
@require_login
def tech_report_pdf(intervention_id):
    user = get_current_user()
    if user["role"] not in ("tech","employee"):
        return "Forbidden", 403
    company = get_current_company(user)

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT i.* FROM interventions i JOIN intervention_assignees ia ON ia.intervention_id=i.id WHERE i.id=? AND i.company_id=? AND ia.user_id=?", (intervention_id, company["id"], user["id"]))
    it = c.fetchone()
    if not it:
        conn.close()
        return "Not found", 404
    c.execute("SELECT * FROM intervention_files WHERE intervention_id=? AND company_id=? ORDER BY uploaded_at DESC",
              (intervention_id, company["id"]))
    files = c.fetchall()
    conn.close()

    mem = io.BytesIO()
    p = canvas.Canvas(mem, pagesize=letter)
    width, height = letter
    y = height - 60

    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, y, "Rapport Technicien - MaintControl")
    y -= 28
    p.setFont("Helvetica", 11)
    p.drawString(50, y, f"Entreprise: {company['name']}")
    y -= 18
    p.drawString(50, y, f"Technicien: {user['username']}")
    y -= 18
    p.drawString(50, y, f"Intervention #{it['id']}: {it['title']}")
    y -= 18
    p.drawString(50, y, f"Statut: {it['status']}  |  Priorité: {it['priority']}")
    y -= 18
    if it.get("scheduled_date"):
        p.drawString(50, y, f"Planifié: {it['scheduled_date'][:10]}")
        y -= 18
    p.drawString(50, y, f"Temps passé (min): {it.get('time_spent_minutes') or 0}")
    y -= 22

    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, y, "Notes technicien")
    y -= 16
    p.setFont("Helvetica", 10)
    note = (it.get("tech_notes") or "-").replace("\r", "")
    for line in note.split("\n"):
        p.drawString(50, y, line[:120])
        y -= 14
        if y < 120:
            p.showPage()
            y = height - 60
            p.setFont("Helvetica", 10)

    y -= 10
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, y, "Preuves (fichiers)")
    y -= 16
    p.setFont("Helvetica", 10)
    if not files:
        p.drawString(50, y, "-")
        y -= 14
    else:
        for f in files[:15]:
            p.drawString(50, y, f"- {f['filename']} ({f['uploaded_at']})")
            y -= 14
            if y < 120:
                p.showPage()
                y = height - 60
                p.setFont("Helvetica", 10)

    # Signature image if exists
    sig_path = it.get("client_signature_path")
    if sig_path and os.path.exists(sig_path):
        y -= 10
        p.setFont("Helvetica-Bold", 12)
        p.drawString(50, y, "Signature client")
        y -= 10
        try:
            p.drawImage(sig_path, 50, max(40, y-140), width=220, height=120, preserveAspectRatio=True, mask='auto')
            y -= 150
        except Exception:
            pass

    p.showPage()
    p.save()
    mem.seek(0)
    return send_file(mem, mimetype="application/pdf", as_attachment=True, download_name=f"rapport_tech_{it['id']}.pdf")

# ---------- Exports ----------

@app.route("/interventions/export/csv")
@require_login
def export_csv():
    user = get_current_user()
    company = get_current_company(user)
    if user["role"] not in ("admin","owner","manager"):
        return "Forbidden", 403
    if is_trial_expired(user) and not user["is_activated"] and user["role"] != "admin":
        return "Trial expiré - export CSV réservé aux comptes activés.", 403

    conn = get_db()
    c = conn.cursor()

    base_query = "SELECT * FROM interventions WHERE company_id = ?"
    params = [company["id"]]

    if user["role"] == "tech":
        base_query += " AND technician_name = ?"
        params.append(user["username"])
    elif user["role"] == "client":
        base_query += " AND client_name = ?"
        params.append(user["username"])

    status = request.args.get("status", "").strip()
    priority = request.args.get("priority", "").strip()
    kind = request.args.get("kind", "").strip()
    category = request.args.get("category", "").strip()
    client = request.args.get("client_name", "").strip()
    technician = request.args.get("technician_name", "").strip()
    date_from = normalize_iso_bound((request.args.get("date_from") or ""), is_end=False)
    date_to = normalize_iso_bound((request.args.get("date_to") or ""), is_end=True)

    if status:
        base_query += " AND status = ?"
        params.append(status)
    if priority:
        base_query += " AND priority = ?"
        params.append(priority)
    if kind:
        base_query += " AND kind = ?"
        params.append(kind)
    if category:
        base_query += " AND category = ?"
        params.append(category)
    if client:
        base_query += " AND client_name = ?"
        params.append(client)
    if technician:
        base_query += " AND technician_name = ?"
        params.append(technician)
    if date_from:
        base_query += " AND created_at >= ?"
        params.append(date_from)
    if date_to:
        base_query += " AND created_at <= ?"
        params.append(date_to)

    base_query += " ORDER BY created_at DESC"
    c.execute(base_query, tuple(params))
    rows = c.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([col for col in rows[0].keys()] if rows else [])
    for row in rows:
        writer.writerow([row[col] for col in row.keys()])

    mem = io.BytesIO()
    mem.write(output.getvalue().encode("utf-8-sig"))
    mem.seek(0)
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="interventions.csv")

@app.route("/interventions/export/pdf")
@require_login
def export_pdf():
    user = get_current_user()
    company = get_current_company(user)
    if user["role"] not in ("admin","owner","manager"):
        return "Forbidden", 403
    if is_trial_expired(user) and not user["is_activated"] and user["role"] != "admin":
        return "Trial expiré - export PDF réservé aux comptes activés.", 403

    conn = get_db()
    c = conn.cursor()

    base_query = "SELECT id, title, status, priority, kind, category, scheduled_date, client_name, technician_name FROM interventions WHERE company_id = ?"
    params = [company["id"]]

    if user["role"] == "tech":
        base_query += " AND technician_name = ?"
        params.append(user["username"])
    elif user["role"] == "client":
        base_query += " AND client_name = ?"
        params.append(user["username"])

    status = request.args.get("status", "").strip()
    priority = request.args.get("priority", "").strip()
    kind = request.args.get("kind", "").strip()
    category = request.args.get("category", "").strip()
    client = request.args.get("client_name", "").strip()
    technician = request.args.get("technician_name", "").strip()
    date_from = normalize_iso_bound((request.args.get("date_from") or ""), is_end=False)
    date_to = normalize_iso_bound((request.args.get("date_to") or ""), is_end=True)

    if status:
        base_query += " AND status = ?"
        params.append(status)
    if priority:
        base_query += " AND priority = ?"
        params.append(priority)
    if kind:
        base_query += " AND kind = ?"
        params.append(kind)
    if category:
        base_query += " AND category = ?"
        params.append(category)
    if client:
        base_query += " AND client_name = ?"
        params.append(client)
    if technician:
        base_query += " AND technician_name = ?"
        params.append(technician)
    if date_from:
        base_query += " AND created_at >= ?"
        params.append(date_from)
    if date_to:
        base_query += " AND created_at <= ?"
        params.append(date_to)

    base_query += " ORDER BY created_at DESC"
    c.execute(base_query, tuple(params))
    rows = c.fetchall()
    conn.close()

    mem = io.BytesIO()
    p = canvas.Canvas(mem, pagesize=letter)
    width, height = letter
    y = height - 50
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, y, f"Rapport des interventions - {company['name']}")
    y -= 25
    p.setFont("Helvetica", 9)

    for row in rows:
        line = f"#{row['id']} | {row['title']} | {row['status']} | {row['priority']} | {row['kind'] or ''} | {row['category'] or ''} | {row['client_name'] or ''} | {row['technician_name'] or ''} | {row['scheduled_date'] or ''}"
        if y < 50:
            p.showPage()
            y = height - 50
            p.setFont("Helvetica", 9)
        p.drawString(40, y, line[:200])
        y -= 12

    p.showPage()
    p.save()
    mem.seek(0)
    return send_file(mem, mimetype="application/pdf", as_attachment=True, download_name="interventions.pdf")


@app.route("/interventions/export/email", methods=["POST"])
@require_login
def export_email():
    user = get_current_user()
    company = get_current_company(user)
    if user["role"] not in ("admin", "owner", "manager"):
        return "Forbidden", 403

    to_emails = (request.form.get("to_emails") or "").strip()
    subject = (request.form.get("subject") or "Export MaintControl").strip()
    body = (request.form.get("body") or "").strip()
    fmt = (request.form.get("format") or "pdf").lower()

    if not to_emails:
        flash("Email destinataire manquant.", "error")
        return redirect(url_for("interventions"))

    # Build same query as exports
    conn = get_db()
    c = conn.cursor()
    base_query = "SELECT * FROM interventions WHERE company_id = ?"
    params = [company["id"]]

    status = (request.form.get("status") or "").strip()
    priority = (request.form.get("priority") or "").strip()
    kind = (request.form.get("kind") or "").strip()
    category = (request.form.get("category") or "").strip()
    client = (request.form.get("client_name") or "").strip()
    technician = (request.form.get("technician_name") or "").strip()
    date_from = normalize_iso_bound((request.form.get("date_from") or ""), is_end=False)
    date_to = normalize_iso_bound((request.form.get("date_to") or ""), is_end=True)

    if status:
        base_query += " AND status = ?"; params.append(status)
    if priority:
        base_query += " AND priority = ?"; params.append(priority)
    if kind:
        base_query += " AND kind = ?"; params.append(kind)
    if category:
        base_query += " AND category = ?"; params.append(category)
    if client:
        base_query += " AND client_name = ?"; params.append(client)
    if technician:
        base_query += " AND technician_name = ?"; params.append(technician)
    if date_from:
        base_query += " AND created_at >= ?"; params.append(date_from)
    if date_to:
        base_query += " AND created_at <= ?"; params.append(date_to)

    base_query += " ORDER BY created_at DESC"
    c.execute(base_query, tuple(params))
    rows = c.fetchall()
    conn.close()

    attachments = []
    if fmt == "csv":
        out = io.StringIO()
        w = csv.writer(out, delimiter=";")
        w.writerow([col for col in rows[0].keys()] if rows else [])
        for r in rows:
            w.writerow([r[col] for col in r.keys()])
        data = out.getvalue().encode("utf-8-sig")
        attachments.append(("interventions.csv", "text/csv", data))
    else:
        mem = io.BytesIO()
        p = canvas.Canvas(mem, pagesize=letter)
        width, height = letter
        y = height - 60
        p.setFont("Helvetica-Bold", 14)
        p.drawString(50, y, f"Export interventions - {company['name']}")
        y -= 22
        p.setFont("Helvetica", 10)
        if not rows:
            p.drawString(50, y, "Aucune intervention ne correspond à ces filtres.")
        else:
            for r in rows[:60]:
                line = f"#{r['id']} | {r['status']} | {r['priority']} | {r['title']}"
                p.drawString(50, y, line[:120])
                y -= 14
                if y < 80:
                    p.showPage()
                    y = height - 60
                    p.setFont("Helvetica", 10)
        p.showPage()
        p.save()
        mem.seek(0)
        attachments.append(("interventions.pdf", "application/pdf", mem.getvalue()))

    try:
        send_email_smtp(to_emails, subject, body, attachments)
        flash("Email envoyé.", "success")
    except Exception as e:
        flash(f"Erreur email: {e}", "error")

    return redirect(url_for("interventions"))


@app.route("/interventions/export/advanced")
@require_login
def export_advanced():
    return render_template("export_advanced.html")

# ---------- Planning ----------

@app.route("/planning")
@require_login
def planning():
    user = get_current_user()
    company = get_current_company(user)
    conn = get_db()
    c = conn.cursor()

    base_query = "SELECT * FROM interventions WHERE company_id = ? AND scheduled_date IS NOT NULL AND scheduled_date != ''"
    params = [company["id"]]

    if user["role"] in ("tech", "employee"):
        c.execute("""
            SELECT i.*
            FROM interventions i
            JOIN intervention_assignees ia ON ia.intervention_id = i.id
            WHERE i.company_id = ?
              AND ia.user_id = ?
              AND i.scheduled_date IS NOT NULL AND i.scheduled_date != ''
        """, (company["id"], user["id"]))
        all_sched = c.fetchall()
    elif user["role"] == "client":
        c.execute(base_query + " AND client_name = ?", (company["id"], user["username"]))
        all_sched = c.fetchall()
    else:
        c.execute(base_query, tuple(params))
        all_sched = c.fetchall()

    conn.close()

    today = datetime.utcnow().date()
    today_list, overdue, upcoming = [], [], []
    for it in all_sched:
        try:
            d = datetime.fromisoformat(it["scheduled_date"]).date()
        except Exception:
            continue
        if d == today:
            today_list.append(it)
        elif d < today and it["status"] != "done":
            overdue.append(it)
        elif d > today:
            upcoming.append(it)

    return render_template(
        "planning.html",
        today_list=today_list,
        overdue=overdue,
        upcoming=upcoming,
    )

# ---------- Licences & activation ----------

@app.route("/admin/licenses", methods=["GET", "POST"])
@require_login
def admin_licenses():
    user = get_current_user()
    if user["role"] != "admin":
        return "Forbidden", 403
    company = get_current_company(user)
    conn = get_db()
    c = conn.cursor()

    if request.method == "POST":
        action = request.form.get("action")
        if action == "generate":
            key = "MC-" + secrets.token_hex(8).upper()
            c.execute("""
                INSERT INTO license_keys (company_id, key, created_at, created_by)
                VALUES (?, ?, ?, ?)
            """, (company["id"], key, datetime.utcnow().isoformat(), user["id"]))
            conn.commit()
        elif action == "assign":
            key = request.form.get("key")
            username = request.form.get("username")
            c.execute("SELECT id FROM users WHERE username = ? AND company_id = ?", (username, company["id"]))
            u = c.fetchone()
            if not u:
                flash("Utilisateur introuvable", "error")
            else:
                c.execute("SELECT * FROM license_keys WHERE key = ? AND used = 0 AND company_id = ?", (key, company["id"]))
                k = c.fetchone()
                if not k:
                    flash("Clé invalide ou déjà utilisée", "error")
                else:
                    c.execute("UPDATE users SET is_activated = 1, license_key = ? WHERE id = ?", (key, u["id"]))
                    c.execute("UPDATE license_keys SET assigned_to = ?, used = 1 WHERE id = ?", (u["id"], k["id"]))
                    conn.commit()
                    flash("Clé assignée et utilisateur activé", "success")

    c.execute("SELECT * FROM license_keys WHERE company_id = ? ORDER BY created_at DESC", (company["id"],))
    keys = c.fetchall()
    c.execute("SELECT id, username, role, is_activated FROM users WHERE company_id = ? ORDER BY id", (company["id"],))
    users = c.fetchall()
    conn.close()
    return render_template("admin_licenses.html", keys=keys, users=users)

@app.route("/admin/users", methods=["GET", "POST"])
@require_login
def admin_users():
    admin = get_current_user()
    if admin["role"] not in ("admin","owner"):
        return "Forbidden", 403
    company = get_current_company(admin)
    conn = get_db()
    c = conn.cursor()

    if request.method == "POST":
        action = request.form.get("action", "create")

        if action == "create":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            role = (request.form.get("role") or "tech").strip().lower()
            # Role rules:
            # - admin (entreprise) can create: owner/patron, manager, tech, employee, client
            # - owner/patron can create: manager, tech, employee, client (but NOT admin and NOT owner)
            allowed_roles_admin = ("owner", "manager", "tech", "employee", "client")
            allowed_roles_owner = ("manager", "tech", "employee", "client")

            if admin["role"] == "admin":
                allowed_roles = allowed_roles_admin
            else:
                allowed_roles = allowed_roles_owner

            if role not in allowed_roles:
                role = "tech"
            start_trial = request.form.get("start_trial") == "on"
            activate_now = request.form.get("activate_now") == "on"

            if not username or not password:
                flash("Nom d'utilisateur et mot de passe sont obligatoires.", "error")
            else:
                now = datetime.utcnow().isoformat()
                trial_start = now if start_trial else None
                try:
                    hashed = generate_password_hash(password)
                    c.execute("""
                        INSERT INTO users (username, password, role, company_id, created_at, trial_start, is_activated, license_key)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (username, hashed, role, company["id"], now, trial_start, 0, None))
                    new_user_id = c.lastrowid

                    if activate_now:
                        key = "MC-" + secrets.token_hex(8).upper()
                        c.execute("""
                            INSERT INTO license_keys (company_id, key, created_at, created_by, assigned_to, used)
                            VALUES (?, ?, ?, ?, ?, 1)
                        """, (company["id"], key, now, admin["id"], new_user_id))
                        c.execute("""
                            UPDATE users
                            SET is_activated = 1, license_key = ?
                            WHERE id = ?
                        """, (key, new_user_id))

                    conn.commit()
                    flash("Utilisateur créé avec succès.", "success")
                except sqlite3.IntegrityError:
                    flash("Nom d'utilisateur déjà utilisé.", "error")

        elif action == "delete":
            if admin["role"] != "admin":
                return "Forbidden", 403

            user_id = request.form.get("user_id")
            try:
                user_id_int = int(user_id)
            except (TypeError, ValueError):
                user_id_int = None

            if not user_id_int:
                flash("Utilisateur invalide.", "error")
            else:
                c.execute("SELECT * FROM users WHERE id = ? AND company_id = ?", (user_id_int, company["id"]))
                target = c.fetchone()
                if not target:
                    flash("Utilisateur introuvable.", "error")
                elif target["id"] == admin["id"]:
                    flash("Vous ne pouvez pas supprimer votre propre compte administrateur.", "error")
                elif target["role"] == "admin":
                    flash("Vous ne pouvez pas supprimer un autre administrateur.", "error")
                else:
                    c.execute("DELETE FROM users WHERE id = ? AND company_id = ?", (user_id_int, company["id"]))
                    conn.commit()
                    flash("Utilisateur supprimé.", "success")

    c.execute("""
        SELECT id, username, role, created_at, trial_start, is_activated, license_key
        FROM users
        WHERE company_id = ?
        ORDER BY id
    """, (company["id"],))
    users = c.fetchall()
    conn.close()
    # Pass current user's role so the template can show/hide sensitive role options (e.g., owner/patron)
    return render_template("admin_users.html", users=users, current_role=admin["role"])

@app.route("/activate", methods=["GET", "POST"])
@require_login
def activate():
    user = get_current_user()
    company = get_current_company(user)
    if user["role"] == "admin" or user["is_activated"]:
        return redirect(url_for("dashboard"))

    msg = None
    if request.method == "POST":
        key = request.form.get("key")
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM license_keys WHERE key = ? AND used = 0 AND company_id = ?", (key, company["id"]))
        k = c.fetchone()
        if not k:
            msg = "Clé invalide ou déjà utilisée."
        else:
            c.execute("UPDATE users SET is_activated = 1, license_key = ? WHERE id = ?", (key, user["id"]))
            c.execute("UPDATE license_keys SET assigned_to = ?, used = 1 WHERE id = ?", (user["id"], k["id"]))
            conn.commit()
            msg = "Votre compte a été activé avec succès."
        conn.close()
    return render_template("activate.html", message=msg)

# ---------- Billing (facturation & paiements simulés) ----------

@app.route("/billing", methods=["GET", "POST"])
@require_login
def billing():
    user = get_current_user()
    if user["role"] != "admin":
        return "Forbidden", 403
    company = get_current_company(user)
    conn = get_db()
    c = conn.cursor()

    if request.method == "POST":
        action = request.form.get("action")
        if action == "create_invoice":
            amount = float(request.form.get("amount") or "0")
            description = request.form.get("description") or "Abonnement licence MaintControl"
            now = datetime.utcnow()
            period_start = now.isoformat()
            period_end = (now + timedelta(days=30)).isoformat()
            c.execute("""
                INSERT INTO invoices (company_id, user_id, amount, currency, description, period_start, period_end, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (company["id"], user["id"], amount, "EUR", description, period_start, period_end, "pending", now.isoformat()))
            conn.commit()
            flash("Facture créée (en attente de paiement).", "success")

        elif action == "pay_invoice":
            invoice_id = int(request.form.get("invoice_id"))
            c.execute("SELECT * FROM invoices WHERE id = ? AND company_id = ?", (invoice_id, company["id"]))
            inv = c.fetchone()
            if not inv:
                flash("Facture introuvable.", "error")
            elif inv["status"] == "paid":
                flash("Facture déjà payée.", "info")
            else:
                now = datetime.utcnow().isoformat()
                c.execute("""
                    INSERT INTO payments (invoice_id, amount, currency, status, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (inv["id"], inv["amount"], inv["currency"], "succeeded", now))
                c.execute("""
                    UPDATE invoices
                    SET status = 'paid', paid_at = ?
                    WHERE id = ?
                """, (now, inv["id"]))
                # Mettre à jour le plan de l'entreprise (simple simulation)
                c.execute("""
                    UPDATE companies
                    SET plan_name = ?, plan_price = ?
                    WHERE id = ?
                """, ("Licence active", inv["amount"], company["id"]))
                conn.commit()
                flash("Paiement simulé effectué, facture marquée comme payée.", "success")

    c.execute("SELECT * FROM invoices WHERE company_id = ? ORDER BY created_at DESC", (company["id"],))
    invoices = c.fetchall()
    c.execute("SELECT * FROM payments WHERE invoice_id IN (SELECT id FROM invoices WHERE company_id = ?)", (company["id"],))
    payments = c.fetchall()
    conn.close()
    return render_template("billing.html", invoices=invoices, payments=payments, company=company)

# ---------- Company settings (nom + domaine personnalisé affiché) ----------

@app.route("/company/settings", methods=["GET", "POST"])
@require_login
def company_settings():
    user = get_current_user()
    if user["role"] != "admin":
        return "Forbidden", 403
    company = get_current_company(user)

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        domain = (request.form.get("domain") or "").strip() or None
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            UPDATE companies
            SET name = ?, domain = ?
            WHERE id = ?
        """, (name or company["name"], domain, company["id"]))
        conn.commit()
        conn.close()
        flash("Paramètres entreprise mis à jour.", "success")
        return redirect(url_for("company_settings"))

    return render_template("company_settings.html", company=company)

# ---------- API et i18n ----------

@app.route("/api/interventions")
@require_login
def api_interventions():
    user = get_current_user()
    company = get_current_company(user)
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM interventions WHERE company_id = ? ORDER BY created_at DESC", (company["id"],))
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify(rows)

@app.route("/i18n/<lang>.json")
def i18n(lang):
    if lang not in SUPPORTED_LANGS:
        lang = DEFAULT_LANG
    path = os.path.join(BASE_DIR, "i18n", f"{lang}.json")
    if not os.path.exists(path):
        lang = DEFAULT_LANG
        path = os.path.join(BASE_DIR, "i18n", f"{lang}.json")
    return send_file(path, mimetype="application/json")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)