
from flask import Flask, render_template, request, redirect, url_for, session, send_file, jsonify, flash
from datetime import datetime, timedelta
import sqlite3
import os
import csv
import io
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from ai.scheduler import suggest_priorities
import secrets
from werkzeug.security import check_password_hash, generate_password_hash

DATABASE = os.path.join(os.path.dirname(__file__), "maintcontrol.db")
TRIAL_DAYS = 30
SUPPORTED_LANGS = ["fr", "en", "es", "de"]
DEFAULT_LANG = "fr"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "CHANGE_THIS_SECRET_KEY")

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    # Only create DB if it does not exist (no migration logic here)
    if os.path.exists(DATABASE):
        return
    conn = get_db()
    c = conn.cursor()

    # Users
    c.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL, -- admin, tech, client
            created_at TEXT NOT NULL,
            trial_start TEXT,
            is_activated INTEGER DEFAULT 0,
            license_key TEXT
        )
    """)

    # Interventions (avec type et catégorie)
    c.execute("""
        CREATE TABLE interventions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            client_name TEXT,
            technician_name TEXT,
            status TEXT NOT NULL,
            priority TEXT NOT NULL,
            kind TEXT, -- correctif, préventif, etc.
            category TEXT, -- électricité, plomberie...
            scheduled_date TEXT,
            created_at TEXT NOT NULL,
            created_by INTEGER,
            FOREIGN KEY(created_by) REFERENCES users(id)
        )
    """)

    # License keys
    c.execute("""
        CREATE TABLE license_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            created_by INTEGER,
            assigned_to INTEGER,
            used INTEGER DEFAULT 0,
            FOREIGN KEY(created_by) REFERENCES users(id),
            FOREIGN KEY(assigned_to) REFERENCES users(id)
        )
    """)

    now = datetime.utcnow().isoformat()

    # Seed admin (déjà activé)
    admin_pw = generate_password_hash("admin")
    c.execute("""
        INSERT INTO users (username, password, role, created_at, trial_start, is_activated)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("admin", admin_pw, "admin", now, now, 1))

    # Seed quelques utilisateurs de démo (non visibles comme "demo" sur l'UI)
    for i in range(1, 6):
        demo_pw = generate_password_hash("password")
        c.execute("""
            INSERT INTO users (username, password, role, created_at, trial_start, is_activated)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (f"user{i}", demo_pw, "tech" if i <= 3 else "client", now, now, 0))

    conn.commit()
    conn.close()

# IMPORTANT : initialiser la base immédiatement (compatible Flask 3.x & Render)
init_db()

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
        "trial_days_left": remaining_trial_days(user) if user else None,
        "is_trial_expired": is_trial_expired(user) if user else False,
        "lang": lang,
        "supported_langs": SUPPORTED_LANGS,
    }

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
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = c.fetchone()
        conn.close()
        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))
        else:
            flash("Identifiants invalides", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@require_login
def dashboard():
    user = get_current_user()
    conn = get_db()
    c = conn.cursor()

    # Interventions visibles selon le rôle
    if user["role"] == "admin":
        c.execute("SELECT * FROM interventions ORDER BY created_at DESC LIMIT 10")
    elif user["role"] == "tech":
        c.execute("SELECT * FROM interventions WHERE technician_name = ? ORDER BY created_at DESC LIMIT 10",
                  (user["username"],))
    else:
        c.execute("SELECT * FROM interventions WHERE client_name = ? ORDER BY created_at DESC LIMIT 10",
                  (user["username"],))
    interventions = c.fetchall()

    # Stats globales pour les cartes KPI
    c.execute("SELECT COUNT(*) AS n FROM interventions")
    total = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) AS n FROM interventions WHERE status = 'open'")
    open_count = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) AS n FROM interventions WHERE status = 'in_progress'")
    in_progress = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) AS n FROM interventions WHERE status = 'done'")
    done_count = c.fetchone()["n"]

    # Interventions en retard (planifiées avant maintenant et pas 'done')
    now_iso = datetime.utcnow().isoformat()
    c.execute("""
        SELECT COUNT(*) AS n
        FROM interventions
        WHERE scheduled_date IS NOT NULL
          AND scheduled_date != ''
          AND scheduled_date < ?
          AND status != 'done'
    """, (now_iso,))
    late_count = c.fetchone()["n"]

    # Interventions assignées à l'utilisateur actuel
    if user["role"] == "tech":
        c.execute("SELECT COUNT(*) AS n FROM interventions WHERE technician_name = ? AND status != 'done'",
                  (user["username"],))
        my_open = c.fetchone()["n"]
    elif user["role"] == "client":
        c.execute("SELECT COUNT(*) AS n FROM interventions WHERE client_name = ? AND status != 'done'",
                  (user["username"],))
        my_open = c.fetchone()["n"]
    else:
        my_open = open_count

    conn.close()

    # AI suggestion
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

@app.route("/interventions")
@require_login
def list_interventions():
    user = get_current_user()
    conn = get_db()
    c = conn.cursor()

    base_query = "SELECT * FROM interventions WHERE 1=1"
    params = []

    # Filtrage par rôle
    if user["role"] == "tech":
        base_query += " AND technician_name = ?"
        params.append(user["username"])
    elif user["role"] == "client":
        base_query += " AND client_name = ?"
        params.append(user["username"])

    # Filtres optionnels (statut, priorité, type, catégorie)
    status = request.args.get("status", "").strip()
    priority = request.args.get("priority", "").strip()
    kind = request.args.get("kind", "").strip()
    category = request.args.get("category", "").strip()

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

    base_query += " ORDER BY created_at DESC"
    c.execute(base_query, tuple(params))
    interventions = c.fetchall()
    conn.close()
    return render_template("interventions.html", interventions=interventions)

@app.route("/interventions/new", methods=["GET", "POST"])
@require_login
def new_intervention():
    user = get_current_user()
    if request.method == "POST":
        title = request.form.get("title")
        description = request.form.get("description")
        client_name = request.form.get("client_name")
        technician_name = request.form.get("technician_name")
        status = request.form.get("status") or "open"
        priority = request.form.get("priority") or "medium"
        kind = request.form.get("kind") or ""
        category = request.form.get("category") or ""
        scheduled_date = request.form.get("scheduled_date") or ""
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            INSERT INTO interventions
            (title, description, client_name, technician_name, status, priority, kind, category, scheduled_date, created_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (title, description, client_name, technician_name, status, priority, kind, category, scheduled_date,
              datetime.utcnow().isoformat(), user["id"]))
        conn.commit()
        conn.close()
        return redirect(url_for("list_interventions"))
    return render_template("intervention_form.html", intervention=None)

@app.route("/interventions/<int:intervention_id>/edit", methods=["GET", "POST"])
@require_login
def edit_intervention(intervention_id):
    user = get_current_user()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM interventions WHERE id = ?", (intervention_id,))
    intervention = c.fetchone()
    if not intervention:
        conn.close()
        return "Not found", 404
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "delete":
            c.execute("DELETE FROM interventions WHERE id = ?", (intervention_id,))
            conn.commit()
            conn.close()
            flash("Intervention supprimée.", "success")
            return redirect(url_for("list_interventions"))
        else:
            title = request.form.get("title")
            description = request.form.get("description")
            client_name = request.form.get("client_name")
            technician_name = request.form.get("technician_name")
            status = request.form.get("status")
            priority = request.form.get("priority")
            kind = request.form.get("kind") or ""
            category = request.form.get("category") or ""
            scheduled_date = request.form.get("scheduled_date")
            c.execute("""
                UPDATE interventions
                SET title=?, description=?, client_name=?, technician_name=?, status=?, priority=?, kind=?, category=?, scheduled_date=?
                WHERE id=?
            """, (title, description, client_name, technician_name, status, priority, kind, category, scheduled_date, intervention_id))
            conn.commit()
            conn.close()
            return redirect(url_for("list_interventions"))
    conn.close()
    return render_template("intervention_form.html", intervention=intervention)


@app.route("/interventions/export/csv")
@require_login
def export_csv():
    """Export CSV avec possibilité de filtres (statut, priorité, type, catégorie, dates, client, technicien)."""
    user = get_current_user()
    if is_trial_expired(user) and not user["is_activated"] and user["role"] != "admin":
        return "Trial expiré - export CSV réservé aux comptes activés.", 403

    conn = get_db()
    c = conn.cursor()

    base_query = "SELECT * FROM interventions WHERE 1=1"
    params = []

    # Restriction par rôle (comme dans la liste des interventions)
    if user["role"] == "tech":
        base_query += " AND technician_name = ?"
        params.append(user["username"])
    elif user["role"] == "client":
        base_query += " AND client_name = ?"
        params.append(user["username"])

    # Filtres optionnels
    status = request.args.get("status", "").strip()
    priority = request.args.get("priority", "").strip()
    kind = request.args.get("kind", "").strip()
    category = request.args.get("category", "").strip()
    client = request.args.get("client_name", "").strip()
    technician = request.args.get("technician_name", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

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
    writer = csv.writer(output)
    writer.writerow([col for col in rows[0].keys()] if rows else [])
    for row in rows:
        writer.writerow([row[col] for col in row.keys()])

    mem = io.BytesIO()
    mem.write(output.getvalue().encode("utf-8"))
    mem.seek(0)
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="interventions.csv")

@app.route("/interventions/export/pdf")
@require_login
def export_pdf():
    """Export PDF (liste des interventions) avec mêmes filtres que le CSV."""
    user = get_current_user()
    if is_trial_expired(user) and not user["is_activated"] and user["role"] != "admin":
        return "Trial expiré - export PDF réservé aux comptes activés.", 403

    conn = get_db()
    c = conn.cursor()

    base_query = "SELECT id, title, status, priority, kind, category, scheduled_date, client_name, technician_name, created_at FROM interventions WHERE 1=1"
    params = []

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
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

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
    p.drawString(50, y, "Rapport des interventions")
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


@app.route("/interventions/<int:intervention_id>/pdf")
@require_login
def intervention_pdf(intervention_id):
    """Rapport PDF détaillé pour une intervention unique."""
    user = get_current_user()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM interventions WHERE id = ?", (intervention_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return "Not found", 404

    # Contrôle d'accès de base
    if user["role"] == "tech" and row["technician_name"] not in (None, "", user["username"]):
        return "Forbidden", 403
    if user["role"] == "client" and row["client_name"] not in (None, "", user["username"]):
        return "Forbidden", 403

    mem = io.BytesIO()
    p = canvas.Canvas(mem, pagesize=letter)
    width, height = letter
    y = height - 50

    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, y, f"Intervention #{row['id']} - {row['title']}")
    y -= 25

    p.setFont("Helvetica", 10)
    fields = [
        ("Statut", row["status"]),
        ("Priorité", row["priority"]),
        ("Type", row["kind"] or ""),
        ("Catégorie", row["category"] or ""),
        ("Client", row["client_name"] or ""),
        ("Technicien", row["technician_name"] or ""),
        ("Créée le", row["created_at"] or ""),
        ("Planifiée pour", row["scheduled_date"] or ""),
    ]

    for label, value in fields:
        p.drawString(50, y, f"{label} : {value}")
        y -= 14

    y -= 10
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, y, "Description")
    y -= 16
    p.setFont("Helvetica", 10)

    desc = row["description"] or ""
    for line in desc.splitlines() or [""]:
        # simple wrap
        chunks = [line[i:i+90] for i in range(0, len(line), 90)] or [""]
        for ch in chunks:
            if y < 50:
                p.showPage()
                y = height - 50
                p.setFont("Helvetica", 10)
            p.drawString(50, y, ch)
            y -= 12

    p.showPage()
    p.save()
    mem.seek(0)
    return send_file(mem, mimetype="application/pdf", as_attachment=True, download_name=f"intervention_{row['id']}.pdf")


@app.route("/planning")
@require_login
def planning():
    """Vue planning simple : interventions du jour, en retard et à venir."""
    user = get_current_user()
    conn = get_db()
    c = conn.cursor()

    base_query = "SELECT * FROM interventions WHERE 1=1"
    params = []

    if user["role"] == "tech":
        base_query += " AND technician_name = ?"
        params.append(user["username"])
    elif user["role"] == "client":
        base_query += " AND client_name = ?"
        params.append(user["username"])

    base_query += " AND scheduled_date IS NOT NULL AND scheduled_date != ''"
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

    return render_template("planning.html",
                           today_list=today_list,
                           overdue=overdue,
                           upcoming=upcoming)

@app.route("/interventions/export/advanced")
@require_login
def export_advanced():
    """Page avec formulaire d'export avancé (filtres + boutons CSV/PDF)."""
    return render_template("export_advanced.html")
@app.route("/admin/licenses", methods=["GET", 'POST'])
@require_login
def admin_licenses():
    user = get_current_user()
    if user["role"] != "admin":
        return "Forbidden", 403

    conn = get_db()
    c = conn.cursor()

    if request.method == "POST":
        action = request.form.get("action")
        if action == "generate":
            key = "MC-" + secrets.token_hex(8).upper()
            c.execute("""
                INSERT INTO license_keys (key, created_at, created_by)
                VALUES (?, ?, ?)
            """, (key, datetime.utcnow().isoformat(), user["id"]))
            conn.commit()
        elif action == "assign":
            key = request.form.get("key")
            username = request.form.get("username")
            c.execute("SELECT id FROM users WHERE username = ?", (username,))
            u = c.fetchone()
            if not u:
                flash("Utilisateur introuvable", "error")
            else:
                c.execute("SELECT * FROM license_keys WHERE key = ? AND used = 0", (key,))
                k = c.fetchone()
                if not k:
                    flash("Clé invalide ou déjà utilisée", "error")
                else:
                    c.execute("UPDATE users SET is_activated = 1, license_key = ? WHERE id = ?", (key, u["id"]))
                    c.execute("UPDATE license_keys SET assigned_to = ?, used = 1 WHERE id = ?", (u["id"], k["id"]))
                    conn.commit()
                    flash("Clé assignée et utilisateur activé", "success")

    c.execute("SELECT * FROM license_keys ORDER BY created_at DESC")
    keys = c.fetchall()
    c.execute("SELECT id, username, role, is_activated FROM users ORDER BY id")
    users = c.fetchall()
    conn.close()
    return render_template("admin_licenses.html", keys=keys, users=users)

@app.route("/admin/users", methods=["GET", "POST"])
@require_login
def admin_users():
    """Gestion des utilisateurs par l'administrateur : création + suppression."""
    admin = get_current_user()
    if admin["role"] != "admin":
        return "Forbidden", 403

    conn = get_db()
    c = conn.cursor()

    if request.method == "POST":
        action = request.form.get("action", "create")

        if action == "create":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            role = request.form.get("role") or "tech"
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
                        INSERT INTO users (username, password, role, created_at, trial_start, is_activated, license_key)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (username, hashed, role, now, trial_start, 0, None))
                    new_user_id = c.lastrowid

                    if activate_now:
                        key = "MC-" + secrets.token_hex(8).upper()
                        c.execute("""
                            INSERT INTO license_keys (key, created_at, created_by, assigned_to, used)
                            VALUES (?, ?, ?, ?, 1)
                        """, (key, now, admin["id"], new_user_id))
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
            user_id = request.form.get("user_id")
            try:
                user_id_int = int(user_id)
            except (TypeError, ValueError):
                user_id_int = None

            if not user_id_int:
                flash("Utilisateur invalide.", "error")
            else:
                c.execute("SELECT * FROM users WHERE id = ?", (user_id_int,))
                target = c.fetchone()
                if not target:
                    flash("Utilisateur introuvable.", "error")
                elif target["id"] == admin["id"]:
                    flash("Vous ne pouvez pas supprimer votre propre compte administrateur.", "error")
                elif target["role"] == "admin":
                    flash("Vous ne pouvez pas supprimer un autre administrateur.", "error")
                else:
                    c.execute("DELETE FROM users WHERE id = ?", (user_id_int,))
                    conn.commit()
                    flash("Utilisateur supprimé.", "success")

    c.execute("""
        SELECT id, username, role, created_at, trial_start, is_activated, license_key
        FROM users
        ORDER BY id
    """)
    users = c.fetchall()
    conn.close()
    return render_template("admin_users.html", users=users)

@app.route("/activate", methods=["GET", "POST"])
@require_login
def activate():
    user = get_current_user()
    if user["role"] == "admin" or user["is_activated"]:
        return redirect(url_for("dashboard"))

    msg = None
    if request.method == "POST":
        key = request.form.get("key")
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM license_keys WHERE key = ? AND used = 0", (key,))
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

@app.route("/api/interventions")
@require_login
def api_interventions():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM interventions ORDER BY created_at DESC")
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify(rows)

@app.route("/i18n/<lang>.json")
def i18n(lang):
    if lang not in SUPPORTED_LANGS:
        lang = DEFAULT_LANG
    path = os.path.join(os.path.dirname(__file__), "i18n", f"{lang}.json")
    if not os.path.exists(path):
        lang = DEFAULT_LANG
        path = os.path.join(os.path.dirname(__file__), "i18n", f"{lang}.json")
    return send_file(path, mimetype="application/json")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
