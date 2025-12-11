
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

DATABASE = os.path.join(os.path.dirname(__file__), "maintcontrol.db")
TRIAL_DAYS = 30
SUPPORTED_LANGS = ["fr", "en", "es", "de"]
DEFAULT_LANG = "fr"

app = Flask(__name__)
app.secret_key = "CHANGE_THIS_SECRET_KEY"

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    if os.path.exists(DATABASE):
        return
    conn = get_db()
    c = conn.cursor()

    # Users: up to 5 users + 1 admin
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

    # Interventions
    c.execute("""
        CREATE TABLE interventions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            client_name TEXT,
            technician_name TEXT,
            status TEXT NOT NULL,
            priority TEXT NOT NULL,
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

    # Seed admin (already fully activated)
    c.execute("""
        INSERT INTO users (username, password, role, created_at, trial_start, is_activated)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("admin", "admin", "admin", now, now, 1))

    # Seed some demo users (trial)
    for i in range(1, 6):
        c.execute("""
            INSERT INTO users (username, password, role, created_at, trial_start, is_activated)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (f"user{i}", "password", "tech" if i <= 3 else "client", now, now, 0))

    conn.commit()
    conn.close()

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

def setup():
    init_db()

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
        c.execute("SELECT * FROM users WHERE username = ? AND password = ?", (username, password))
        user = c.fetchone()
        conn.close()
        if user:
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
    if user["role"] == "admin":
        c.execute("SELECT * FROM interventions ORDER BY created_at DESC LIMIT 10")
    elif user["role"] == "tech":
        c.execute("SELECT * FROM interventions WHERE technician_name = ? ORDER BY created_at DESC LIMIT 10",
                  (user["username"],))
    else:
        c.execute("SELECT * FROM interventions WHERE client_name = ? ORDER BY created_at DESC LIMIT 10",
                  (user["username"],))
    interventions = c.fetchall()
    conn.close()

    # simple AI suggestion using our scheduler
    suggestions = suggest_priorities([dict(row) for row in interventions])

    trial_left = remaining_trial_days(user)
    return render_template("dashboard.html", interventions=interventions, suggestions=suggestions, trial_left=trial_left)

@app.route("/interventions")
@require_login
def list_interventions():
    user = get_current_user()
    conn = get_db()
    c = conn.cursor()
    if user["role"] == "admin":
        c.execute("SELECT * FROM interventions ORDER BY created_at DESC")
    elif user["role"] == "tech":
        c.execute("SELECT * FROM interventions WHERE technician_name = ? ORDER BY created_at DESC",
                  (user["username"],))
    else:
        c.execute("SELECT * FROM interventions WHERE client_name = ? ORDER BY created_at DESC",
                  (user["username"],))
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
        scheduled_date = request.form.get("scheduled_date") or ""
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            INSERT INTO interventions
            (title, description, client_name, technician_name, status, priority, scheduled_date, created_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (title, description, client_name, technician_name, status, priority, scheduled_date,
              datetime.utcnow().isoformat(), user["id"]))
        conn.commit()
        conn.close()
        return redirect(url_for("list_interventions"))
    return render_template("intervention_form.html")

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
        title = request.form.get("title")
        description = request.form.get("description")
        client_name = request.form.get("client_name")
        technician_name = request.form.get("technician_name")
        status = request.form.get("status")
        priority = request.form.get("priority")
        scheduled_date = request.form.get("scheduled_date")
        c.execute("""
            UPDATE interventions
            SET title=?, description=?, client_name=?, technician_name=?, status=?, priority=?, scheduled_date=?
            WHERE id=?
        """, (title, description, client_name, technician_name, status, priority, scheduled_date, intervention_id))
        conn.commit()
        conn.close()
        return redirect(url_for("list_interventions"))
    conn.close()
    return render_template("intervention_form.html", intervention=intervention)


@app.route("/interventions/<int:intervention_id>/delete", methods=["POST"])
@require_login
def delete_intervention(intervention_id):
    user = get_current_user()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM interventions WHERE id = ?", (intervention_id,))
    intervention = c.fetchone()
    if not intervention:
        conn.close()
        return "Not found", 404
    # Only admin or creator can supprimer
    if user["role"] != "admin" and intervention["created_by"] != user["id"]:
        conn.close()
        return "Forbidden", 403
    c.execute("DELETE FROM interventions WHERE id = ?", (intervention_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("list_interventions"))

@app.route("/interventions/export/csv")
@require_login
def export_csv():
    user = get_current_user()
    if is_trial_expired(user):
        return "Trial expiré - export CSV réservé aux comptes activés.", 403

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM interventions")
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
    user = get_current_user()
    if is_trial_expired(user):
        return "Trial expiré - export PDF réservé aux comptes activés.", 403

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, title, status, priority, scheduled_date FROM interventions")
    rows = c.fetchall()
    conn.close()

    mem = io.BytesIO()
    p = canvas.Canvas(mem, pagesize=letter)
    width, height = letter
    y = height - 50
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, y, "Rapport des interventions")
    y -= 30
    p.setFont("Helvetica", 10)
    for row in rows:
        line = f"#{row['id']} | {row['title']} | {row['status']} | {row['priority']} | {row['scheduled_date'] or ''}"
        if y < 50:
            p.showPage()
            y = height - 50
        p.drawString(50, y, line)
        y -= 15
    p.showPage()
    p.save()
    mem.seek(0)
    return send_file(mem, mimetype="application/pdf", as_attachment=True, download_name="interventions.pdf")

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
            # generate a random license key
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
    # simple JSON API, used by frontend automation
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
    setup()      # initialise la base de données au lancement
    app.run(debug=True)

